from __future__ import annotations

import json
import asyncio
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.models.entities import (
    AiUsageRecord,
    Attachment,
    AttachmentLink,
    AuditLog,
    ClassAgentBinding,
    ClassRoom,
    InteractionAnalysis,
    ClassOnboardingSession,
    Student,
    SystemSetting,
    User,
    UserSession,
    WriteProposal,
)
from app.schemas.admin import AdminClassCreate, OpenClawAgentUpdate, OpenClawGlobalUpdate
from app.schemas.domain import ClassCreate
from app.services import accounts, approval, class_student, logs as log_service, openclaw_provisioning, openclaw_usage
from app.services.common import audit
from app.utils.time import now


SETTING_DEFINITIONS: dict[str, dict[str, Any]] = {
    "feature.file_analysis": {
        "group": "features", "label": "文件智能解析", "description": "课表、座位表和 onboarding 文件上传后交由 OpenClaw 解析。",
        "type": "boolean", "default": True,
    },
    "feature.event_ai": {
        "group": "features", "label": "学生事件智能分类", "description": "网页登记学生事件时自动判断子类、倾向和严重程度。",
        "type": "boolean", "default": True,
    },
    "feature.wechat_binding": {
        "group": "features", "label": "微信绑定", "description": "允许班级智能体生成微信二维码和绑定路由。",
        "type": "boolean", "default": True,
    },
    "feature.reminders": {
        "group": "features", "label": "主动提醒", "description": "允许智能体查询和发送到期提醒；关闭后已有提醒保留。",
        "type": "boolean", "default": True,
    },
    "admin.usage_window_days": {
        "group": "constants", "label": "默认统计窗口（天）", "description": "管理员使用量页面首次加载的统计天数。",
        "type": "integer", "default": 30, "minimum": 7, "maximum": 365,
    },
    "admin.database_page_size": {
        "group": "constants", "label": "数据库默认分页行数", "description": "数据库调试页面每次默认读取的行数。",
        "type": "integer", "default": 50, "minimum": 10, "maximum": 200,
    },
}

_agent_usage_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


def setting_value(db: Session, key: str) -> Any:
    definition = SETTING_DEFINITIONS.get(key)
    if not definition:
        raise AppError("SETTING_NOT_ALLOWED", "该配置项不允许通过管理端修改", 422, {"key": key})
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value_json if row else definition["default"]


def feature_enabled(db: Session, key: str) -> bool:
    return bool(setting_value(db, key))


def require_feature(db: Session, key: str) -> None:
    if not feature_enabled(db, key):
        raise AppError("FEATURE_DISABLED", "该功能已被管理员关闭", 403, {"feature": key})


def list_settings(db: Session) -> list[dict[str, Any]]:
    stored = {row.key: row.value_json for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(SETTING_DEFINITIONS)))}
    return [{"key": key, **definition, "value": stored.get(key, definition["default"])} for key, definition in SETTING_DEFINITIONS.items()]


def update_setting(db: Session, key: str, value: Any, *, operator_id: str | None) -> dict[str, Any]:
    definition = SETTING_DEFINITIONS.get(key)
    if not definition:
        raise AppError("SETTING_NOT_ALLOWED", "该配置项不允许通过管理端修改", 422, {"key": key})
    expected = definition["type"]
    if expected == "boolean" and type(value) is not bool:
        raise AppError("VALIDATION_ERROR", "配置值必须是布尔值", 422)
    if expected == "integer":
        if type(value) is not int:
            raise AppError("VALIDATION_ERROR", "配置值必须是整数", 422)
        if value < definition["minimum"] or value > definition["maximum"]:
            raise AppError("VALIDATION_ERROR", f"配置值应在 {definition['minimum']} 到 {definition['maximum']} 之间", 422)
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    before = row.value_json if row else definition["default"]
    if row:
        row.value_json = value
    else:
        row = SystemSetting(key=key, value_json=value)
        db.add(row)
    audit(db, "update_setting", "system_setting", row.id, operator_id=operator_id, before={"value": before}, after={"value": value})
    db.commit()
    return {"key": key, **definition, "value": value}


def _count(db: Session, model, *filters) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*filters)) or 0)


def overview(db: Session) -> dict[str, Any]:
    database_path: Path | None = None
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix) and not settings.database_url.endswith(":memory:"):
        database_path = Path(settings.database_url.removeprefix(prefix))
    attachment_bytes = int(db.scalar(select(func.coalesce(func.sum(Attachment.file_size), 0))) or 0)
    return {
        "counts": {
            "users": _count(db, User),
            "active_teachers": _count(db, User, User.role == "head_teacher", User.is_active.is_(True)),
            "classes": _count(db, ClassRoom, ClassRoom.deleted_at.is_(None)),
            "students": _count(db, Student, Student.deleted_at.is_(None)),
            "agents": _count(db, ClassAgentBinding),
            "linked_agents": _count(db, ClassAgentBinding, ClassAgentBinding.status == "linked"),
            "active_sessions": _count(db, UserSession, UserSession.revoked_at.is_(None), UserSession.expires_at > now()),
        },
        "storage": {
            "database_bytes": database_path.stat().st_size if database_path and database_path.exists() else None,
            "attachment_bytes": attachment_bytes,
            "attachment_count": _count(db, Attachment),
            "database_dialect": db.bind.dialect.name if db.bind else None,
        },
        "recent_audit": list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8))),
    }


def usage_stats(db: Session, days: int | None = None) -> dict[str, Any]:
    window = days or int(setting_value(db, "admin.usage_window_days"))
    if window < 1 or window > 365:
        raise AppError("VALIDATION_ERROR", "统计窗口应在 1 到 365 天之间", 422)
    start = now() - timedelta(days=window - 1)
    audits = list(db.scalars(select(AuditLog).where(AuditLog.created_at >= start)))
    analyses = list(db.scalars(select(InteractionAnalysis).where(InteractionAnalysis.created_at >= start)))
    proposals = list(db.scalars(select(WriteProposal).where(WriteProposal.created_at >= start)))
    sessions = list(db.scalars(select(UserSession).where(UserSession.created_at >= start)))
    usage = list(db.scalars(select(AiUsageRecord).where(AiUsageRecord.created_at >= start)))
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "writes": 0, "logins": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

    def day(value) -> str:
        return value.date().isoformat() if hasattr(value, "date") else str(value)[:10]

    for row in analyses:
        daily[day(row.created_at)]["requests"] += 1
    for row in audits:
        daily[day(row.created_at)]["writes"] += 1
    for row in sessions:
        daily[day(row.created_at)]["logins"] += 1
    for row in usage:
        bucket = daily[day(row.created_at)]
        bucket["input_tokens"] += row.input_tokens
        bucket["output_tokens"] += row.output_tokens
        bucket["total_tokens"] += row.total_tokens
    operations = Counter(row.operation for row in usage)
    models = Counter(row.model or "unknown" for row in usage)
    return {
        "days": window,
        "totals": {
            "ai_requests": len(usage),
            "interaction_analyses": len(analyses),
            "write_proposals": len(proposals),
            "audit_actions": len(audits),
            "logins": len(sessions),
            "input_tokens": sum(row.input_tokens for row in usage),
            "output_tokens": sum(row.output_tokens for row in usage),
            "total_tokens": sum(row.total_tokens for row in usage),
            "cached_input_tokens": sum(row.cached_input_tokens for row in usage),
        },
        "daily": [{"date": key, **daily[key]} for key in sorted(daily)],
        "by_operation": [{"name": key, "requests": value, "tokens": sum(row.total_tokens for row in usage if row.operation == key)} for key, value in operations.most_common()],
        "by_model": [{"name": key, "requests": value, "tokens": sum(row.total_tokens for row in usage if (row.model or "unknown") == key)} for key, value in models.most_common()],
        "token_collection_started": min((row.created_at for row in usage), default=None),
    }


def _plugin_entry(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = (((config.get("plugins") or {}).get("entries") or {}).get(name) or {})
    return value if isinstance(value, dict) else {}


async def openclaw_config() -> dict[str, Any]:
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    config = snapshot.get("config") or {}
    endpoints = (((config.get("gateway") or {}).get("http") or {}).get("endpoints") or {})
    responses = endpoints.get("responses") if isinstance(endpoints.get("responses"), dict) else {}
    return {
        "gateway": {
            "url": settings.openclaw_gateway_url,
            "token_configured": bool(settings.openclaw_gateway_token),
            "token_masked": f"***{settings.openclaw_gateway_token[-4:]}" if settings.openclaw_gateway_token else None,
            "timeout_seconds": settings.openclaw_timeout_seconds,
            "default_agent_id": settings.openclaw_agent_id,
            "wechat_channel": settings.openclaw_wechat_channel,
            "workspace_root": str(settings.openclaw_class_workspace_root),
        },
        "runtime": {
            "responses_enabled": bool(responses.get("enabled", False)),
            "dm_scope": (config.get("session") or {}).get("dmScope") or "per-account-channel-peer",
            "classclaw_plugin_enabled": _plugin_entry(config, "classclaw").get("enabled") is not False,
            "wechat_plugin_enabled": _plugin_entry(config, settings.openclaw_wechat_channel).get("enabled") is not False,
            "config_hash": snapshot.get("hash"),
        },
    }


def _safe_models(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    allowed = {"id", "name", "provider", "api", "contextWindow", "reasoning", "input", "available"}
    return [{key: row.get(key) for key in allowed if key in row} for row in rows if isinstance(row, dict)]


def _agent_runtime_row(config: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    for row in (config.get("agents") or {}).get("list") or []:
        if isinstance(row, dict) and str(row.get("id") or row.get("agentId")) == agent_id:
            return row
    return None


def _model_fields(value: Any) -> tuple[str | None, list[str]]:
    if isinstance(value, str):
        return value, []
    if isinstance(value, dict):
        primary = value.get("primary")
        fallbacks = value.get("fallbacks")
        return (str(primary) if primary else None, [str(item) for item in fallbacks or [] if item])
    return None, []


async def openclaw_agent_settings(db: Session, class_id: str) -> dict[str, Any]:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("OPENCLAW_AGENT_NOT_CREATED", "该班级的 OpenClaw 智能体尚未创建", 409)
    snapshot, models_payload = await asyncio.gather(
        openclaw_provisioning.admin_rpc("config.get"),
        openclaw_provisioning.admin_rpc("models.list"),
    )
    row = _agent_runtime_row(snapshot.get("config") or {}, binding.openclaw_agent_id)
    if not row:
        raise AppError("OPENCLAW_AGENT_NOT_FOUND", "OpenClaw 配置中找不到该智能体", 409)
    primary, fallbacks = _model_fields(row.get("model"))
    safe_runtime = {
        "display_name": ((row.get("identity") or {}).get("name") if isinstance(row.get("identity"), dict) else None) or row.get("name"),
        "model": primary,
        "provider": primary.split("/", 1)[0] if primary and "/" in primary else None,
        "model_fallbacks": fallbacks,
        "utility_model": row.get("utilityModel"),
        "thinking_default": row.get("thinkingDefault"),
        "reasoning_default": row.get("reasoningDefault"),
        "verbose_default": row.get("verboseDefault"),
        "fast_mode_default": row.get("fastModeDefault"),
        "context_injection": row.get("contextInjection"),
        "bootstrap_max_chars": row.get("bootstrapMaxChars"),
        "bootstrap_total_max_chars": row.get("bootstrapTotalMaxChars"),
        "max_skills_prompt_chars": (row.get("skillsLimits") or {}).get("maxSkillsPromptChars"),
        "memory_search_enabled": (row.get("memorySearch") or {}).get("enabled"),
        "model_params": row.get("params") if isinstance(row.get("params"), dict) else {},
        "skills": row.get("skills") or [],
        "tools": row.get("tools") or {},
    }
    return {
        "binding": binding, "runtime": safe_runtime, "models": _safe_models(models_payload),
        "workspace": openclaw_provisioning.workspace_files(db, class_id), "config_hash": snapshot.get("hash"),
    }


async def update_openclaw_config(data: OpenClawGlobalUpdate) -> dict[str, Any]:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return await openclaw_config()
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    raw: dict[str, Any] = {}
    paths: list[str] = []
    if "responses_enabled" in changes:
        raw.setdefault("gateway", {}).setdefault("http", {}).setdefault("endpoints", {})["responses"] = {"enabled": changes["responses_enabled"]}
        paths.append("gateway.http.endpoints.responses.enabled")
    if "dm_scope" in changes:
        raw["session"] = {"dmScope": changes["dm_scope"]}
        paths.append("session.dmScope")
    if "classclaw_plugin_enabled" in changes:
        raw.setdefault("plugins", {}).setdefault("entries", {})["classclaw"] = {"enabled": changes["classclaw_plugin_enabled"]}
        paths.append("plugins.entries.classclaw.enabled")
    params: dict[str, Any] = {
        "raw": json.dumps(raw, ensure_ascii=False), "replacePaths": paths,
        "note": "Update ClassClaw global settings from admin console", "restartDelayMs": 500,
    }
    if snapshot.get("hash"):
        params["baseHash"] = snapshot["hash"]
    await openclaw_provisioning.admin_rpc("config.patch", params)
    return {"updated": changes, "restart_requested": True}


async def update_openclaw_agent(db: Session, class_id: str, data: OpenClawAgentUpdate) -> dict[str, Any]:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("OPENCLAW_AGENT_NOT_CREATED", "该班级的 OpenClaw 智能体尚未创建", 409)
    changes = data.model_dump(exclude_unset=True)
    if data.display_name is not None:
        await openclaw_provisioning.admin_rpc("agents.update", {
            "agentId": binding.openclaw_agent_id, "name": data.display_name.strip(),
            "workspace": binding.workspace_path, "emoji": "🏫",
        })
    runtime_fields = set(changes) - {"display_name"}
    if runtime_fields:
        snapshot = await openclaw_provisioning.admin_rpc("config.get")
        config = snapshot.get("config") or {}
        rows = list((config.get("agents") or {}).get("list") or [])
        found = False
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or str(row.get("id") or row.get("agentId")) != binding.openclaw_agent_id:
                continue
            updated = dict(row)
            if "model" in changes or "model_fallbacks" in changes:
                current_primary, current_fallbacks = _model_fields(updated.get("model"))
                primary = data.model.strip() if "model" in changes and data.model and data.model.strip() else (None if "model" in changes else current_primary)
                fallbacks = data.model_fallbacks if "model_fallbacks" in changes else current_fallbacks
                if fallbacks and not primary:
                    raise AppError("VALIDATION_ERROR", "配置回退模型前必须设置主模型", 422)
                if primary:
                    updated["model"] = {"primary": primary, "fallbacks": fallbacks or []} if "model_fallbacks" in changes or fallbacks else primary
                else:
                    updated.pop("model", None)
            field_map = {
                "utility_model": "utilityModel", "thinking_default": "thinkingDefault",
                "reasoning_default": "reasoningDefault", "verbose_default": "verboseDefault",
                "fast_mode_default": "fastModeDefault", "context_injection": "contextInjection",
                "bootstrap_max_chars": "bootstrapMaxChars", "bootstrap_total_max_chars": "bootstrapTotalMaxChars",
                "model_params": "params",
            }
            for source, target in field_map.items():
                if source not in changes:
                    continue
                value = changes[source]
                if value is None:
                    updated.pop(target, None)
                else:
                    updated[target] = value
            if "max_skills_prompt_chars" in changes:
                limits = dict(updated.get("skillsLimits") or {})
                if data.max_skills_prompt_chars is None:
                    limits.pop("maxSkillsPromptChars", None)
                else:
                    limits["maxSkillsPromptChars"] = data.max_skills_prompt_chars
                if limits:
                    updated["skillsLimits"] = limits
                else:
                    updated.pop("skillsLimits", None)
            if "memory_search_enabled" in changes:
                if data.memory_search_enabled is None:
                    updated.pop("memorySearch", None)
                else:
                    updated["memorySearch"] = {**(updated.get("memorySearch") or {}), "enabled": data.memory_search_enabled}
            rows[index] = updated
            found = True
            break
        if not found:
            raise AppError("OPENCLAW_AGENT_NOT_FOUND", "OpenClaw 配置中找不到该智能体", 409)
        params: dict[str, Any] = {
            "raw": json.dumps({"agents": {"list": rows}}, ensure_ascii=False), "replacePaths": ["agents.list"],
            "note": f"Update ClassClaw agent {binding.openclaw_agent_id}", "restartDelayMs": 500,
        }
        if snapshot.get("hash"):
            params["baseHash"] = snapshot["hash"]
        await openclaw_provisioning.admin_rpc("config.patch", params)
    audit(db, "update_openclaw_agent", "class_agent_binding", binding.id, operator_type="admin", after=changes)
    db.commit()
    return {"binding": binding, "updated": changes, "restart_requested": bool(runtime_fields)}


async def openclaw_agent_usage(db: Session, days: int) -> list[dict[str, Any]]:
    if days < 1 or days > 365:
        raise AppError("VALIDATION_ERROR", "统计窗口应在 1 到 365 天之间", 422)
    cached = _agent_usage_cache.get(days)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    rows = db.execute(
        select(ClassAgentBinding, ClassRoom)
        .join(ClassRoom, ClassRoom.id == ClassAgentBinding.class_id)
        .where(ClassRoom.deleted_at.is_(None))
        .order_by(ClassRoom.name)
    ).all()
    end_date = now().date()
    start_date = end_date - timedelta(days=days - 1)
    agent_ids = [binding.openclaw_agent_id for binding, _cls in rows if binding.openclaw_agent_id]
    scan_task = asyncio.create_task(asyncio.to_thread(openclaw_usage.scan_agents, agent_ids, start_date, end_date))
    tasks = [
        openclaw_provisioning.admin_rpc("usage.cost", {"days": days, "agentId": binding.openclaw_agent_id})
        if binding.openclaw_agent_id else None
        for binding, _cls in rows
    ]
    pending = [task for task in tasks if task is not None]
    results = await asyncio.gather(*pending, return_exceptions=True) if pending else []
    try:
        scanned = await scan_task
    except Exception as exc:
        scanned = {agent_id: {"available": False, "error": str(exc)[:500], "calls": 0, "messages": {}, "latency": None, "daily": []} for agent_id in agent_ids}
    result_iter = iter(results)
    output = []
    for (binding, cls), task in zip(rows, tasks, strict=True):
        value = next(result_iter) if task is not None else None
        payload = {} if isinstance(value, Exception) else (value or {})
        metrics = scanned.get(binding.openclaw_agent_id or "", {"available": False, "calls": 0, "messages": {}, "latency": None, "daily": []})
        warnings = []
        if isinstance(value, Exception):
            warnings.append(f"Token/费用统计不可用：{str(value)[:300]}")
        if not metrics.get("available"):
            warnings.append(f"调用/延迟统计不可用：{metrics.get('error') or 'OpenClaw 会话目录不可用'}")
        if metrics.get("truncated"):
            warnings.append("会话记录超过扫描上限，调用与延迟数据只包含最近记录")
        output.append({
            "class_id": cls.id, "class_name": cls.name, "agent_id": binding.openclaw_agent_id,
            "status": binding.status, "totals": payload.get("totals") or {},
            "calls": metrics.get("calls") or 0, "messages": metrics.get("messages") or {},
            "latency": metrics.get("latency"), "daily": metrics.get("daily") or [],
            "metrics_source": metrics.get("source"),
            "metrics_files_scanned": metrics.get("files_scanned", 0), "metrics_read_errors": metrics.get("read_errors", 0),
            "warnings": warnings,
            "updated_at": payload.get("updatedAt"), "cache_status": payload.get("cacheStatus"),
        })
    _agent_usage_cache[days] = (time.monotonic(), output)
    return output


async def logs_view(*, source: str, limit: int, level: str | None, query: str | None, cursor: int | None = None) -> dict[str, Any]:
    if source == "classclaw":
        return log_service.classclaw_logs(limit=limit, level=level, query=query)
    if source == "openclaw":
        payload = await openclaw_provisioning.admin_rpc("logs.tail", {"limit": min(limit * 5, 1000), "maxBytes": 500_000, **({"cursor": cursor} if cursor is not None else {})})
        return log_service.openclaw_logs_payload(payload, limit=limit, level=level, query=query)
    raise AppError("VALIDATION_ERROR", "日志来源必须是 classclaw 或 openclaw", 422)


def create_admin_class(db: Session, data: AdminClassCreate) -> ClassRoom:
    conflicts = approval.class_name_conflicts(db, data.name)
    if conflicts:
        raise AppError("CLASS_NAME_CONFLICT", "班级名称已存在", 409, {"conflicts": conflicts})
    if data.owner_user_id:
        user = db.get(User, data.owner_user_id)
        if not user or user.role != "head_teacher" or not user.is_active:
            raise AppError("NOT_FOUND", "可用的班主任账号不存在", 404)
        if accounts.user_class_id(db, user.id):
            raise AppError("CLASS_LIMIT_REACHED", "该班主任账号已有班级", 409)
    cls = class_student.create_class(db, ClassCreate(**data.model_dump(exclude={"owner_user_id", "provision_agent"})), commit=False)
    cls.owner_user_id = data.owner_user_id
    if data.owner_user_id and not cls.head_teacher:
        user = db.get(User, data.owner_user_id)
        cls.head_teacher = user.display_name if user else None
    db.commit()
    openclaw_provisioning.ensure_binding(db, cls)
    return cls


def update_class_owner(db: Session, class_id: str, owner_user_id: str | None) -> ClassRoom:
    cls = class_student.get_class(db, class_id, include_inactive=True)
    if owner_user_id:
        user = db.get(User, owner_user_id)
        if not user or user.role != "head_teacher" or not user.is_active:
            raise AppError("NOT_FOUND", "可用的班主任账号不存在", 404)
        existing = accounts.user_class_id(db, owner_user_id)
        if existing and existing != class_id:
            raise AppError("CLASS_LIMIT_REACHED", "该班主任账号已有班级", 409, {"class_id": existing})
    before = cls.owner_user_id
    cls.owner_user_id = owner_user_id
    audit(db, "assign_owner", "class", cls.id, operator_type="admin", before={"owner_user_id": before}, after={"owner_user_id": owner_user_id})
    db.commit()
    return cls


async def initialize_class_data(db: Session, *, operator_id: str | None) -> dict[str, Any]:
    """Delete every class and class-scoped transient record while preserving accounts and system settings."""
    classes = list(db.scalars(select(ClassRoom).where(ClassRoom.deleted_at.is_(None)).order_by(ClassRoom.created_at)))
    class_results: list[dict[str, Any]] = []
    cleanup_errors: list[str] = []
    deleted_classes = 0
    for cls in classes:
        agent_cleanup: dict[str, Any] | None = None
        agent_error: str | None = None
        try:
            agent_cleanup = await openclaw_provisioning.cleanup_class_agent_resources(db, cls.id)
        except Exception as exc:
            agent_error = str(exc)[:1000]
            cleanup_errors.append(f"{cls.name}: {agent_error}")
        try:
            deleted = class_student.hard_delete_class(db, cls.id, operator_id=operator_id)
            deleted_classes += 1
            class_results.append({"class_id": cls.id, "class_name": cls.name, "deleted": True, "agent_cleanup": agent_cleanup, "agent_error": agent_error, "deleted_counts": deleted.get("deleted_counts", {})})
        except Exception as exc:
            db.rollback()
            cleanup_errors.append(f"{cls.name} database: {str(exc)[:1000]}")
            class_results.append({"class_id": cls.id, "class_name": cls.name, "deleted": False, "agent_cleanup": agent_cleanup, "agent_error": agent_error, "database_error": str(exc)[:1000]})

    remaining_attachments = list(db.scalars(select(Attachment)))
    attachment_paths = [row.stored_path for row in remaining_attachments]
    transient_counts = {
        "write_proposals": _count(db, WriteProposal),
        "interaction_analyses": _count(db, InteractionAnalysis),
        "onboarding_sessions": _count(db, ClassOnboardingSession),
        "attachment_links": _count(db, AttachmentLink),
        "attachments": len(remaining_attachments),
    }
    db.execute(delete(WriteProposal))
    db.execute(delete(InteractionAnalysis))
    db.execute(delete(ClassOnboardingSession))
    db.execute(delete(AttachmentLink))
    db.execute(delete(Attachment))
    audit(db, "initialize_class_data", "system", "class-data", operator_type="admin", operator_id=operator_id, after={"deleted_classes": deleted_classes, "transient_counts": transient_counts, "cleanup_error_count": len(cleanup_errors)})
    db.commit()

    file_errors: list[str] = []
    attachment_root = settings.attachment_dir.resolve()
    for stored_path in attachment_paths:
        path = (settings.attachment_dir.parent / stored_path).resolve()
        if path == attachment_root or attachment_root not in path.parents:
            file_errors.append(f"unsafe path: {stored_path}")
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            file_errors.append(f"{stored_path}: {exc}")
    errors = [*cleanup_errors, *file_errors]
    failed_classes = len(classes) - deleted_classes
    status = "completed" if not errors and failed_classes == 0 else "partial" if deleted_classes or transient_counts else "failed"
    return {
        "status": status,
        "deleted_classes": deleted_classes,
        "failed_classes": failed_classes,
        "preserved": ["users", "user_sessions", "system_settings", "audit_logs", "ai_usage_records"],
        "transient_deleted": transient_counts,
        "class_results": class_results,
        "errors": errors,
        "finished_at": now(),
    }
