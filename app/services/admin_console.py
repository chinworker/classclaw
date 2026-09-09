from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import usage_database
from app.config import settings
from app.core.errors import AppError
from app.database import Base
from app.models.entities import (
    Attachment,
    AuditLog,
    ClassAgentBinding,
    ClassRoom,
    InteractionAnalysis,
    Student,
    User,
    UserSession,
    WriteProposal,
)
from app.models.usage import AiUsageRecord, UsageBase
from app.schemas.admin import OpenClawAgentUpdate, OpenClawGlobalUpdate
from app.services import (
    accounts,
    class_student,
    configuration_catalog,
    openclaw_bridge,
    openclaw_provisioning,
    openclaw_usage,
    openclaw_workspaces,
)
from app.services import (
    logs as log_service,
)
from app.services.common import audit
from app.utils.time import now

SETTING_DEFINITIONS: dict[str, dict[str, Any]] = {
    "feature.file_analysis": {
        "group": "features", "label": "文件智能解析", "description": "课表、座位表和 onboarding 文件上传后交由 OpenClaw 解析。",
        "type": "boolean", "config_path": "features.file_analysis",
    },
    "feature.event_ai": {
        "group": "features", "label": "学生事件智能分类", "description": "网页登记学生事件时自动判断子类、倾向和严重程度。",
        "type": "boolean", "config_path": "features.event_ai",
    },
    "feature.wechat_binding": {
        "group": "features", "label": "微信绑定", "description": "允许班级智能体生成微信二维码和绑定路由。",
        "type": "boolean", "config_path": "features.wechat_binding",
    },
    "feature.reminders": {
        "group": "features", "label": "主动提醒", "description": "允许智能体查询和发送到期提醒；关闭后已有提醒保留。",
        "type": "boolean", "config_path": "features.reminders",
    },
    "admin.usage_window_days": {
        "group": "constants", "label": "默认统计窗口（天）", "description": "管理员使用量页面首次加载的统计天数。",
        "type": "integer", "config_path": "web.usage_window_days", "minimum": 7, "maximum": 365,
    },
    "admin.database_page_size": {
        "group": "constants", "label": "数据库默认分页行数", "description": "数据库调试页面每次默认读取的行数。",
        "type": "integer", "config_path": "web.database_page_size", "minimum": 10, "maximum": 200,
    },
}

_agent_usage_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


def setting_value(key: str) -> Any:
    definition = SETTING_DEFINITIONS.get(key)
    if not definition:
        raise AppError("SETTING_NOT_FOUND", "该静态配置项不存在", 404, {"key": key})
    target: Any = settings
    for part in definition["config_path"].split("."):
        target = getattr(target, part)
    return target


def feature_enabled(key: str) -> bool:
    return bool(setting_value(key))


def require_feature(key: str) -> None:
    if not feature_enabled(key):
        raise AppError("FEATURE_DISABLED", "该功能已被管理员关闭", 403, {"feature": key})


def list_settings() -> list[dict[str, Any]]:
    return configuration_catalog.list_settings(settings)


def settings_catalog() -> dict[str, Any]:
    return configuration_catalog.catalog(settings)


def _count(db: Session, model, *filters) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*filters)) or 0)


def overview(db: Session) -> dict[str, Any]:
    database_path: Path | None = None
    prefix = "sqlite:///"
    if settings.database_url.startswith(prefix) and not settings.database_url.endswith(":memory:"):
        database_path = Path(settings.database_url.removeprefix(prefix))
    attachment_bytes = int(db.scalar(select(func.coalesce(func.sum(Attachment.file_size), 0))) or 0)
    usage_path = Path(settings.usage_database_url.removeprefix(prefix))
    try:
        usage_bytes = usage_path.stat().st_size
    except OSError:
        usage_bytes = None
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
            "usage_database_bytes": usage_bytes,
            "attachment_bytes": attachment_bytes,
            "attachment_count": _count(db, Attachment),
            "database_dialect": db.bind.dialect.name if db.bind else None,
        },
        "recent_audit": list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8))),
    }


def usage_stats(db: Session, days: int | None = None) -> dict[str, Any]:
    window = days or int(setting_value("admin.usage_window_days"))
    if window < 1 or window > 365:
        raise AppError("VALIDATION_ERROR", "统计窗口应在 1 到 365 天之间", 422)
    start = (now() - timedelta(days=window - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    audits = list(db.scalars(select(AuditLog).where(AuditLog.created_at >= start)))
    analyses = list(db.scalars(select(InteractionAnalysis).where(InteractionAnalysis.created_at >= start)))
    proposals = list(db.scalars(select(WriteProposal).where(WriteProposal.created_at >= start)))
    sessions = list(db.scalars(select(UserSession).where(UserSession.created_at >= start)))
    with usage_database.reader_session() as usage_db:
        usage = list(usage_db.scalars(select(AiUsageRecord).where(AiUsageRecord.created_at >= start)))
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


def database_overview(db: Session) -> dict[str, Any]:
    tables = [
        {"name": name, "row_count": db.scalar(select(func.count()).select_from(table)) or 0, "database": "core"}
        for name, table in sorted(Base.metadata.tables.items())
    ]
    try:
        with usage_database.reader_session() as usage_db:
            tables.extend(
                {"name": name, "row_count": usage_db.scalar(select(func.count()).select_from(table)) or 0, "database": "usage"}
                for name, table in sorted(UsageBase.metadata.tables.items())
            )
    except AppError as exc:
        tables.extend({"name": name, "row_count": None, "database": "usage", "error": exc.code} for name in UsageBase.metadata.tables)
    return {"dialect": db.bind.dialect.name if db.bind else None, "tables": tables}


def database_table(db: Session, table_name: str, offset: int, limit: int) -> dict[str, Any]:
    def read(session: Session, table, database: str) -> dict[str, Any]:
        statement = select(table).order_by(*table.primary_key.columns).offset(offset).limit(limit)
        rows = session.execute(statement).mappings().all()
        hidden = {"users": {"password_hash"}, "user_sessions": {"token_hash"}}.get(table_name, set())
        return {
            "table": table_name, "database": database, "columns": list(table.c.keys()),
            "items": [{key: "***" if key in hidden and value is not None else value for key, value in row.items()} for row in rows],
            "total": session.scalar(select(func.count()).select_from(table)) or 0, "offset": offset, "limit": limit,
        }

    table = UsageBase.metadata.tables.get(table_name)
    if table is not None:
        with usage_database.reader_session() as usage_db:
            return read(usage_db, table, "usage")
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise AppError("NOT_FOUND", "数据库表不存在", 404, {"table": table_name})
    return read(db, table, "core")


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
        "file_config": {
            "path": snapshot.get("path"),
            "default_model": _model_fields(((config.get("agents") or {}).get("defaults") or {}).get("model"))[0],
            "default_image_model": _model_fields(((config.get("agents") or {}).get("defaults") or {}).get("imageModel"))[0],
            "providers": sorted(((config.get("models") or {}).get("providers") or {}).keys()),
        },
    }


def _safe_models(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    allowed = {"id", "name", "provider", "api", "contextWindow", "reasoning", "input", "available"}
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        safe = {key: row.get(key) for key in allowed if key in row}
        model_id = str(safe.get("id") or "").strip()
        provider = str(safe.get("provider") or "").strip()
        if model_id and provider and "/" not in model_id:
            safe["id"] = f"{provider}/{model_id}"
        result.append(safe)
    return result


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


def _runtime_workspace(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("workspace") or row.get("workspacePath")
    return str(value) if value else None


def _agent_model_summary(config: dict[str, Any], agent_id: str | None, binding: ClassAgentBinding | None = None) -> dict[str, Any]:
    row = _agent_runtime_row(config, agent_id) if agent_id else None
    primary, _ = _model_fields((row or {}).get("model"))
    default, _ = _model_fields(((config.get("agents") or {}).get("defaults") or {}).get("model"))
    return {
        "main_model": primary or default,
        "inherits_main": not bool(primary),
        "image_model": binding.image_model if binding else None,
        "speech_model": binding.speech_model if binding else None,
    }


def _system_agent_descriptors(config: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = (config.get("agents") or {}).get("defaults") or {}
    main_id = settings.openclaw_agent_id
    extractor_id = settings.openclaw_extractor_agent_id
    main_row = _agent_runtime_row(config, main_id)
    extractor_row = _agent_runtime_row(config, extractor_id) if extractor_id else None
    main_workspace = _runtime_workspace(main_row) or defaults.get("workspace")
    extractor_workspace = _runtime_workspace(extractor_row) or str((settings.openclaw_class_workspace_root / "_extractor").resolve())
    return [
        {
            "identifier": "main",
            "kind": "main",
            "label": "Main 智能体",
            "description": "OpenClaw 默认智能体，负责非班级专属任务和系统级对话。",
            "agent_id": main_id,
            "workspace_path": str(main_workspace) if main_workspace else None,
            "present": main_row is not None,
            "enabled": True,
            "resettable": False,
            "status": "active" if main_row else "unavailable",
            "model_summary": _agent_model_summary(config, main_id),
        },
        {
            "identifier": "extractor",
            "kind": "extractor",
            "label": "数据提取智能体",
            "description": "用于网页上传解析和结构化信息提取，不开放班级工具。",
            "agent_id": extractor_id,
            "workspace_path": extractor_workspace,
            "present": extractor_row is not None,
            "enabled": settings.openclaw_extractor_enabled,
            "resettable": True,
            "status": "disabled" if not settings.openclaw_extractor_enabled else "active" if extractor_row else "unavailable",
            "model_summary": _agent_model_summary(config, extractor_id),
        },
    ]


def _class_agent_descriptors(db: Session, config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ClassRoom, ClassAgentBinding, User)
        .outerjoin(ClassAgentBinding, ClassRoom.id == ClassAgentBinding.class_id)
        .outerjoin(User, User.id == ClassRoom.owner_user_id)
        .where(ClassRoom.deleted_at.is_(None))
        .order_by(ClassRoom.name)
    ).all()
    return [
        {
            "identifier": cls.id,
            "kind": "class",
            "label": cls.name,
            "description": "班级专属智能体，拥有独立提示词、工具和可选微信绑定。",
            "agent_id": binding.openclaw_agent_id if binding else None,
            "workspace_path": binding.workspace_path if binding else None,
            "present": bool(binding and binding.openclaw_agent_id and _agent_runtime_row(config, binding.openclaw_agent_id)),
            "enabled": True,
            "resettable": True,
            "status": (
                "pending_agent" if not binding else
                "unavailable" if binding.openclaw_agent_id and not _agent_runtime_row(config, binding.openclaw_agent_id) else binding.status
            ),
            "class": {"id": cls.id, "name": cls.name, "status": cls.status},
            "owner": accounts.public_user(owner, cls.id) if owner else None,
            "binding": binding,
            "model_summary": _agent_model_summary(config, binding.openclaw_agent_id if binding else None, binding),
        }
        for cls, binding, owner in rows
    ]


async def openclaw_agent_catalog(db: Session) -> list[dict[str, Any]]:
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    config = snapshot.get("config") or {}
    return [*_system_agent_descriptors(config), *_class_agent_descriptors(db, config)]


def _resolve_agent(db: Session, identifier: str, config: dict[str, Any]) -> dict[str, Any]:
    descriptors = [*_system_agent_descriptors(config), *_class_agent_descriptors(db, config)]
    descriptor = next(
        (item for item in descriptors if identifier in {item["identifier"], item.get("agent_id")}),
        None,
    )
    if not descriptor:
        raise AppError("OPENCLAW_AGENT_NOT_FOUND", "未找到可管理的 OpenClaw 智能体", 404, {"identifier": identifier})
    if not descriptor.get("agent_id"):
        raise AppError("OPENCLAW_AGENT_NOT_CREATED", "该智能体尚未创建", 409, {"identifier": identifier})
    return descriptor


def _workspace_defaults(descriptor: dict[str, Any]) -> dict[str, str] | None:
    if descriptor["kind"] == "extractor":
        return openclaw_bridge.extractor_workspace_defaults()
    return None


def _workspace_snapshot(db: Session, descriptor: dict[str, Any]) -> dict[str, Any]:
    if descriptor["kind"] == "class":
        result = openclaw_provisioning.workspace_files(db, descriptor["class"]["id"])
        for item in result["files"]:
            item["resettable"] = True
            item["exists"] = True
        result["available"] = True
        result["resettable"] = True
        return result
    workspace_path = descriptor.get("workspace_path")
    if not workspace_path:
        return {"workspace": None, "files": [], "available": False, "resettable": False}
    result = openclaw_workspaces.snapshot(workspace_path, defaults=_workspace_defaults(descriptor))
    result["available"] = True
    result["resettable"] = descriptor["resettable"]
    return result


async def openclaw_agent_settings(db: Session, identifier: str) -> dict[str, Any]:
    snapshot, models_payload = await asyncio.gather(
        openclaw_provisioning.admin_rpc("config.get"),
        openclaw_provisioning.admin_rpc("models.list"),
    )
    config = snapshot.get("config") or {}
    descriptor = _resolve_agent(db, identifier, config)
    row = _agent_runtime_row(config, descriptor["agent_id"])
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
        "agent": descriptor,
        "binding": descriptor.get("binding"),
        "runtime": safe_runtime,
        "thinking_configuration": {"managed": descriptor["kind"] == "class", "default": settings.openclaw_class_agent_thinking},
        "models": _safe_models(models_payload),
        "workspace": _workspace_snapshot(db, descriptor),
        "config_hash": snapshot.get("hash"),
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


async def update_openclaw_agent(db: Session, identifier: str, data: OpenClawAgentUpdate) -> dict[str, Any]:
    changes = data.model_dump(exclude_unset=True)
    initial_snapshot = await openclaw_provisioning.admin_rpc("config.get")
    descriptor = _resolve_agent(db, identifier, initial_snapshot.get("config") or {})
    if descriptor["kind"] == "class" and "thinking_default" in changes:
        raise AppError(
            "CLASS_AGENT_THINKING_MANAGED", "班级默认思考强度请修改 openclaw.class_agent_thinking 配置；单个对话请使用本会话思考强度", 409,
        )
    agent_id = descriptor["agent_id"]
    if data.display_name is not None:
        params = {"agentId": agent_id, "name": data.display_name.strip()}
        if descriptor.get("workspace_path"):
            params["workspace"] = descriptor["workspace_path"]
        await openclaw_provisioning.admin_rpc("agents.update", params)
    runtime_fields = set(changes) - {"display_name"}
    if runtime_fields:
        snapshot = await openclaw_provisioning.admin_rpc("config.get") if data.display_name is not None else initial_snapshot
        config = snapshot.get("config") or {}
        rows = list((config.get("agents") or {}).get("list") or [])
        found = False
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or str(row.get("id") or row.get("agentId")) != agent_id:
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
            "note": f"Update managed OpenClaw agent {agent_id}", "restartDelayMs": 500,
        }
        if snapshot.get("hash"):
            params["baseHash"] = snapshot["hash"]
        await openclaw_provisioning.admin_rpc("config.patch", params)
    target_type = "class_agent_binding" if descriptor["kind"] == "class" else "openclaw_agent"
    binding = descriptor.get("binding")
    if binding and "model" in changes:
        binding.main_model = data.model.strip() if data.model and data.model.strip() else None
    target_id = binding.id if binding else agent_id
    audit(db, "update_openclaw_agent", target_type, target_id, operator_type="admin", after=changes)
    db.commit()
    return {"agent": descriptor, "binding": descriptor.get("binding"), "updated": changes, "restart_requested": bool(runtime_fields)}


async def update_openclaw_workspace(
    db: Session,
    identifier: str,
    filename: str,
    content: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    descriptor = _resolve_agent(db, identifier, snapshot.get("config") or {})
    if descriptor["kind"] == "class":
        return openclaw_provisioning.update_workspace_file(
            db, descriptor["class"]["id"], filename, content, expected_sha256,
        )
    if not descriptor.get("workspace_path"):
        raise AppError("OPENCLAW_WORKSPACE_UNAVAILABLE", "该智能体未配置工作区", 409)
    return openclaw_workspaces.update_file(
        db,
        agent_id=descriptor["agent_id"],
        workspace_path=descriptor["workspace_path"],
        filename=filename,
        content=content,
        expected_sha256=expected_sha256,
    )


async def reset_openclaw_workspace(db: Session, identifier: str, filename: str) -> dict[str, Any]:
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    descriptor = _resolve_agent(db, identifier, snapshot.get("config") or {})
    if descriptor["kind"] == "class":
        return openclaw_provisioning.reset_workspace_file(db, descriptor["class"]["id"], filename)
    if not descriptor.get("workspace_path"):
        raise AppError("OPENCLAW_WORKSPACE_UNAVAILABLE", "该智能体未配置工作区", 409)
    return openclaw_workspaces.reset_file(
        db,
        agent_id=descriptor["agent_id"],
        workspace_path=descriptor["workspace_path"],
        filename=filename,
        defaults=_workspace_defaults(descriptor),
    )


async def openclaw_agent_usage(db: Session, days: int) -> list[dict[str, Any]]:
    if days < 1 or days > 365:
        raise AppError("VALIDATION_ERROR", "统计窗口应在 1 到 365 天之间", 422)
    cached = _agent_usage_cache.get(days)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    config = snapshot.get("config") or {}
    descriptors = [*_system_agent_descriptors(config), *_class_agent_descriptors(db, config)]
    end_date = now().date()
    start_date = end_date - timedelta(days=days - 1)
    agent_ids = list(dict.fromkeys(item["agent_id"] for item in descriptors if item.get("agent_id") and item.get("present")))
    scan_task = asyncio.create_task(asyncio.to_thread(openclaw_usage.scan_agents, agent_ids, start_date, end_date))
    tasks = [
        openclaw_provisioning.admin_rpc("usage.cost", {"days": days, "agentId": item["agent_id"]})
        if item.get("agent_id") and item.get("present") else None
        for item in descriptors
    ]
    pending = [task for task in tasks if task is not None]
    results = await asyncio.gather(*pending, return_exceptions=True) if pending else []
    try:
        scanned = await scan_task
    except Exception as exc:
        scanned = {agent_id: {"available": False, "error": str(exc)[:500], "calls": 0, "messages": {}, "latency": None, "daily": []} for agent_id in agent_ids}
    result_iter = iter(results)
    output = []
    for descriptor, task in zip(descriptors, tasks, strict=True):
        value = next(result_iter) if task is not None else None
        payload = {} if isinstance(value, Exception) else (value or {})
        metrics = scanned.get(descriptor.get("agent_id") or "", {"available": False, "calls": 0, "messages": {}, "latency": None, "daily": []})
        warnings = []
        if isinstance(value, Exception):
            warnings.append(f"Token/费用统计不可用：{str(value)[:300]}")
        if not metrics.get("available"):
            warnings.append(f"调用/延迟统计不可用：{metrics.get('error') or 'OpenClaw 会话目录不可用'}")
        if metrics.get("truncated"):
            warnings.append("会话记录超过扫描上限，调用与延迟数据只包含最近记录")
        cls = descriptor.get("class") or {}
        output.append({
            "identifier": descriptor["identifier"], "kind": descriptor["kind"], "label": descriptor["label"],
            "class_id": cls.get("id"), "class_name": cls.get("name"), "agent_id": descriptor.get("agent_id"),
            "status": descriptor["status"], "totals": payload.get("totals") or {},
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


def _reset_directory(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve() or len(root.parts) < 3:
        raise AppError("UNSAFE_RESET_PATH", "初始化目录范围不安全，已停止文件清理", 500, {"path": str(root)})
    files = sum(1 for item in root.rglob("*") if item.is_file()) if root.exists() else 0
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return {"path": str(root), "files_deleted": files}


async def initialize_system(db: Session) -> dict[str, Any]:
    """Factory-reset all ClassClaw data and recreate only the required defaults."""
    errors: list[str] = []

    try:
        extractor_workspace = openclaw_bridge.reset_extractor_workspace()
    except Exception as exc:
        extractor_workspace = None
        errors.append(f"extractor workspace: {str(exc)[:1000]}")
    try:
        extractor_ready = await openclaw_bridge.ensure_extractor_agent(force=True)
        if not extractor_ready:
            errors.append("extractor agent: OpenClaw 数据提取智能体未能恢复")
    except Exception as exc:
        extractor_ready = False
        errors.append(f"extractor agent: {str(exc)[:1000]}")

    try:
        agent_cleanup = await openclaw_provisioning.cleanup_all_class_agent_resources(db)
        errors.extend(agent_cleanup["errors"])
    except Exception as exc:
        agent_cleanup = {"bindings": None, "protected_agents": [settings.openclaw_agent_id, settings.openclaw_extractor_agent_id], "errors": [str(exc)[:1000]]}
        errors.append(f"class agents: {str(exc)[:1000]}")
    try:
        default_sessions = openclaw_provisioning.clear_default_agent_sessions()
    except Exception as exc:
        default_sessions = None
        errors.append(f"default agent sessions: {str(exc)[:1000]}")

    try:
        attachments = _reset_directory(settings.attachment_dir)
    except Exception as exc:
        attachments = None
        errors.append(f"attachments: {str(exc)[:1000]}")

    try:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        settings.log_file.open("w", encoding="utf-8").close()
        log_reset = {"path": str(settings.log_file), "cleared": True}
    except OSError as exc:
        log_reset = {"path": str(settings.log_file), "cleared": False}
        errors.append(f"logs: {str(exc)[:1000]}")

    if errors:
        raise AppError(
            "SYSTEM_INITIALIZATION_INCOMPLETE",
            "外部资源未能全部重置，数据库尚未清空；请恢复 OpenClaw 或文件权限后重试",
            503,
            {
                "errors": errors,
                "agent_cleanup": agent_cleanup,
                "attachments": attachments,
                "logs": log_reset,
                "database_preserved_for_retry": True,
            },
        )

    deleted_counts = {
        table.name: int(db.scalar(select(func.count()).select_from(table)) or 0)
        for table in Base.metadata.sorted_tables
    }
    # Two SQLite files cannot share an atomic commit. If usage cleanup fails,
    # preserve core data; if core cleanup fails, explicitly report the usage reset.
    deleted_counts["ai_usage_records"] = await asyncio.to_thread(usage_database.clear_records)
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.commit()
        db.expunge_all()
    except Exception as exc:
        db.rollback()
        raise AppError(
            "SYSTEM_INITIALIZATION_FAILED", "业务库删除已回滚；用量库已清空，请修复后重试", 500,
            {"error": str(exc)[:1000], "usage_records_deleted": deleted_counts["ai_usage_records"]},
        ) from exc

    default_admin = accounts.ensure_default_admin(db)
    _agent_usage_cache.clear()
    openclaw_bridge.reset_runtime_caches()
    return {
        "status": "completed",
        "database_rows_deleted": sum(deleted_counts.values()),
        "deleted_counts": deleted_counts,
        "deleted_classes": deleted_counts.get("classes", 0),
        "failed_classes": 0,
        "attachments": attachments,
        "agent_cleanup": agent_cleanup,
        "default_agents": [
            {"agent_id": settings.openclaw_agent_id, "kind": "main", "preserved": True},
            {
                "agent_id": settings.openclaw_extractor_agent_id,
                "kind": "extractor",
                "preserved": True,
                "ready": extractor_ready,
                "workspace": extractor_workspace,
            },
        ],
        "default_agent_sessions": default_sessions,
        "default_admin": {
            "id": default_admin.id,
            "username": default_admin.username,
            "must_change_password": default_admin.must_change_password,
        },
        "logs": log_reset,
        "preserved": ["database_schema", "environment_configuration", "openclaw_main_agent", "openclaw_extractor_agent"],
        "errors": errors,
        "finished_at": now(),
    }
