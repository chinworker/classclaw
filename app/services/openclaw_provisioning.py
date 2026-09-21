from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import qrcode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.database import reader_session, suspend_writer
from app.models.entities import ClassAgentBinding, ClassRoom
from app.services import wechat_login
from app.services.common import audit, entity_dict
from app.services.http_client import gateway_headers, get_http_client
from app.utils.time import now

EDITABLE_WORKSPACE_FILES = {
    "AGENTS.md": {"label": "系统提示词", "description": "核心行为、写入流程和回复规则。"},
    "SOUL.md": {"label": "Soul", "description": "人格、语气、价值取向与边界。"},
    "IDENTITY.md": {"label": "Identity", "description": "名称、角色、主题和对外身份。"},
    "TOOLS.md": {"label": "Tools", "description": "工具使用策略和调用约定。"},
    "USER.md": {"label": "User", "description": "班级和主要用户上下文。"},
    "HEARTBEAT.md": {"label": "Heartbeat", "description": "心跳任务说明；默认不启用定时心跳。"},
}
_CUSTOMIZED_FILES_META = ".classclaw-admin-customized.json"
_CHAT_LOOP_DETECTION = {
    "enabled": True, "historySize": 32, "warningThreshold": 3,
    "criticalThreshold": 6, "globalCircuitBreakerThreshold": 12,
    "detectors": {"genericRepeat": True, "knownPollNoProgress": True, "pingPong": True},
}


def agent_name_for_class(class_id: str) -> str:
    """Return a stable OpenClaw resource name that is independent of class names."""
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", class_id.strip()).strip("-").lower()
    if not safe_id:
        raise AppError("VALIDATION_ERROR", "班级 ID 无法生成智能体名称", 500)
    return f"classclaw-{safe_id}"


def _identity_name(class_id: str) -> str:
    short_id = re.sub(r"[^a-zA-Z0-9]", "", class_id)[:8] or "agent"
    return f"ClassClaw 助理 · {short_id}"


def _agent_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("agents", "items", "list"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _agent_id(row: dict[str, Any]) -> str | None:
    value = row.get("agentId") or row.get("id")
    return str(value) if value else None


def _agent_names(row: dict[str, Any]) -> set[str]:
    values = {row.get("id"), row.get("agentId"), row.get("name"), row.get("identityName")}
    identity = row.get("identity")
    if isinstance(identity, dict):
        values.add(identity.get("name"))
    return {str(value).strip() for value in values if value and str(value).strip()}


def _same_workspace(row: dict[str, Any], workspace_path: str) -> bool:
    remote = row.get("workspace") or row.get("workspacePath")
    if not remote:
        return False
    try:
        return Path(str(remote)).resolve() == Path(workspace_path).resolve()
    except (OSError, RuntimeError):
        return str(remote) == workspace_path


async def agent_name_conflicts(class_name: str) -> list[dict[str, str | None]]:
    """Find legacy OpenClaw agents whose identity directly uses a class name."""
    name = " ".join(class_name.split())
    if not name:
        return []
    targets = {name.casefold(), f"{name}专属智能体".casefold()}
    rows = _agent_rows(await admin_rpc("agents.list"))
    conflicts = []
    for row in rows:
        if not any(value.casefold() in targets for value in _agent_names(row)):
            continue
        conflicts.append(
            {
                "agent_id": _agent_id(row),
                "name": str(row.get("name") or row.get("identityName") or _agent_id(row) or ""),
                "workspace": str(row.get("workspace") or row.get("workspacePath") or "") or None,
            }
        )
    return conflicts


async def admin_rpc(method: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = await get_http_client().post(
            f"{settings.openclaw_gateway_url}/api/v1/admin/rpc",
            headers=gateway_headers(),
            json={"method": method, "params": params or {}},
            timeout=settings.openclaw_timeout_seconds,
        )
    except Exception as exc:
        raise AppError("OPENCLAW_ADMIN_UNAVAILABLE", "无法调用 OpenClaw 管理接口", 503, {"error": str(exc)[:500]}) from exc
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or payload.get("ok") is not True:
        error = payload.get("error") or {}
        message = error.get("message") or f"HTTP {response.status_code}"
        if response.status_code == 404:
            message = "OpenClaw 的 admin-http-rpc 插件尚未启用"
        raise AppError("OPENCLAW_ADMIN_UNAVAILABLE", message, 503, {"method": method, "gateway_error": error})
    return payload.get("payload")


def get_binding(db: Session, class_id: str) -> ClassAgentBinding:
    if not db.get(ClassRoom, class_id):
        raise not_found("班级", class_id)
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == class_id))
    if not binding:
        raise AppError("NOT_FOUND", "班级尚未建立智能体绑定记录", 404, {"class_id": class_id})
    return binding


def ensure_binding(db: Session, cls: ClassRoom) -> ClassAgentBinding:
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == cls.id))
    if binding:
        expected_name = agent_name_for_class(cls.id)
        if not binding.openclaw_agent_id and binding.agent_name != expected_name:
            binding.agent_name = expected_name
            binding.status = "pending_agent"
            binding.last_error = None
            db.commit()
        return binding
    binding = ClassAgentBinding(
        class_id=cls.id,
        agent_name=agent_name_for_class(cls.id),
        workspace_path=str((settings.openclaw_class_workspace_root / cls.id).resolve()),
        channel_id=settings.openclaw_wechat_channel,
        status="pending_agent",
    )
    db.add(binding)
    db.commit()
    return binding


def _remove_workspace(workspace_path: str) -> bool:
    root = settings.openclaw_class_workspace_root.resolve()
    workspace = Path(workspace_path).resolve()
    if workspace == root or root not in workspace.parents:
        raise AppError("VALIDATION_ERROR", "智能体工作目录不在配置的根目录中", 500)
    if not workspace.exists():
        return False
    shutil.rmtree(workspace)
    return True


def _remove_agent_state(agent_id: str) -> bool:
    root = (settings.openclaw_state_dir / "agents").expanduser().resolve()
    target = (root / agent_id).resolve()
    if target == root or root not in target.parents:
        raise AppError("VALIDATION_ERROR", "智能体状态目录不在 OpenClaw agents 根目录中", 500, {"agent_id": agent_id})
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def clear_default_agent_sessions() -> dict[str, Any]:
    """Clear conversations for the two default agents while preserving their runtime definitions."""
    root = (settings.openclaw_state_dir / "agents").expanduser().resolve()
    results = []
    for agent_id in dict.fromkeys((settings.openclaw_agent_id, settings.openclaw_extractor_agent_id)):
        if not agent_id:
            continue
        agent_root = (root / agent_id).resolve()
        sessions = (agent_root / "sessions").resolve()
        if agent_root == root or root not in agent_root.parents or sessions.parent != agent_root:
            raise AppError("VALIDATION_ERROR", "默认智能体会话目录不安全", 500, {"agent_id": agent_id})
        removed = sessions.exists()
        if removed:
            shutil.rmtree(sessions)
        results.append({"agent_id": agent_id, "sessions_removed": removed})
    return {"agents": results}


async def cleanup_all_class_agent_resources(db: Session) -> dict[str, Any]:
    """Remove every class agent while protecting Main and the managed extractor."""
    bindings = list(db.scalars(select(ClassAgentBinding).order_by(ClassAgentBinding.created_at)))
    class_ids = {binding.class_id for binding in bindings}
    protected = {settings.openclaw_agent_id, settings.openclaw_extractor_agent_id}
    agent_ids = {binding.openclaw_agent_id for binding in bindings if binding.openclaw_agent_id and binding.openclaw_agent_id not in protected}
    account_ids = {binding.channel_account_id for binding in bindings if binding.channel_account_id}
    errors: list[str] = []
    runtime_removed = 0
    accounts_logged_out = 0

    for binding in bindings:
        # Drop in-flight QR attempts first so a late scan cannot re-register credentials.
        try:
            await wechat_login.call(binding.class_id, "cancel")
        except AppError as exc:
            errors.append(f"wechat cancel {binding.class_id}: {str(exc)[:300]}")
    if errors:
        raise AppError("DELETION_INCOMPLETE", "扫码取消失败，已停止初始化清理", 503, {"errors": errors})
    for account_id in sorted(account_ids):
        try:
            result = await admin_rpc("channels.logout", {"channel": settings.openclaw_wechat_channel, "accountId": account_id})
            if isinstance(result, dict) and result.get("cleared") is True and result.get("loggedOut") is True:
                accounts_logged_out += 1
            else:
                errors.append(f"wechat account {account_id}: 未完成登出或本地凭据清理")
        except AppError as exc:
            errors.append(f"wechat account {account_id}: {str(exc)[:500]}")

    if errors:
        raise AppError("DELETION_INCOMPLETE", "微信登出失败，已停止初始化清理", 503, {"errors": errors})
    if agent_ids or account_ids:
        try:
            snapshot = await admin_rpc("config.get")
            config = snapshot.get("config") or {}
            agents_config = config.get("agents") if isinstance(config.get("agents"), dict) else {}
            agent_rows = agents_config.get("list") if isinstance(agents_config.get("list"), list) else []
            runtime_removed = sum(
                1 for row in agent_rows
                if isinstance(row, dict) and str(row.get("id") or row.get("agentId")) in agent_ids
            )
            filtered_agents = [
                row for row in agent_rows
                if not isinstance(row, dict) or str(row.get("id") or row.get("agentId")) not in agent_ids
            ]
            runtime_bindings = config.get("bindings") if isinstance(config.get("bindings"), list) else []
            filtered_bindings = [
                row for row in runtime_bindings
                if not isinstance(row, dict)
                or not (
                    row.get("agentId") in agent_ids
                    or (
                        (row.get("match") or {}).get("channel") == settings.openclaw_wechat_channel
                        and (row.get("match") or {}).get("accountId") in account_ids
                    )
                )
            ]
            plugin = ((config.get("plugins") or {}).get("entries") or {}).get("classclaw") or {}
            plugin_config = plugin.get("config") if isinstance(plugin.get("config"), dict) else {}
            agent_classes = plugin_config.get("agentClasses") if isinstance(plugin_config.get("agentClasses"), dict) else {}
            # config.patch merges objects: only an explicit null deletes a mapping key.
            # replacePaths replaces arrays wholesale and has no effect on objects.
            removed_agent_classes = {
                key: None for key, value in agent_classes.items()
                if key in agent_ids or value in class_ids
            }
            raw: dict[str, Any] = {
                "agents": {"list": filtered_agents},
                "bindings": filtered_bindings,
                "plugins": {"entries": {"classclaw": {"config": {"agentClasses": removed_agent_classes}}}},
            }
            channel_accounts = ((config.get("channels") or {}).get(settings.openclaw_wechat_channel) or {}).get("accounts")
            if isinstance(channel_accounts, dict):
                stale = [account for account in account_ids if account in channel_accounts]
                if stale:
                    raw["channels"] = {settings.openclaw_wechat_channel: {"accounts": {account: None for account in stale}}}
            params: dict[str, Any] = {
                "raw": json.dumps(raw, ensure_ascii=False),
                "replacePaths": ["bindings", "agents.list"],
                "note": "Factory reset ClassClaw class agents",
                "restartDelayMs": 500,
            }
            if snapshot.get("hash"):
                params["baseHash"] = snapshot["hash"]
            await admin_rpc("config.patch", params)
        except Exception as exc:
            errors.append(f"OpenClaw runtime: {str(exc)[:1000]}")

    if errors:
        raise AppError("DELETION_INCOMPLETE", "Gateway 清理失败，已停止文件删除", 503, {"errors": errors})

    removed_workspaces = 0
    removed_agent_states = 0
    for binding in bindings:
        try:
            removed_workspaces += int(_remove_workspace(binding.workspace_path))
        except Exception as exc:
            errors.append(f"workspace {binding.workspace_path}: {str(exc)[:500]}")
    for agent_id in agent_ids:
        try:
            removed_agent_states += int(_remove_agent_state(agent_id))
        except Exception as exc:
            errors.append(f"agent state {agent_id}: {str(exc)[:500]}")

    root = settings.openclaw_class_workspace_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.name == "_extractor":
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed_workspaces += 1
        except OSError as exc:
            errors.append(f"orphan workspace {child}: {str(exc)[:500]}")
    return {
        "bindings": len(bindings),
        "runtime_agents_removed": runtime_removed,
        "workspaces_removed": removed_workspaces,
        "agent_states_removed": removed_agent_states,
        "wechat_accounts_logged_out": accounts_logged_out,
        "protected_agents": sorted(item for item in protected if item),
        "errors": errors,
    }


def _workspace_files(cls: ClassRoom, binding: ClassAgentBinding) -> dict[str, str]:
    identity = {
        "role": "class",
        "class_id": cls.id,
        "class_name": cls.name,
        "openclaw_agent_id": binding.openclaw_agent_id,
    }
    display_name = _identity_name(cls.id)
    agents = f"""# System
仅服务班级 `{cls.id}`（{cls.name}），禁止访问或修改其他班级。
疑似写入意图的自然语言、微信、OCR、语音内容先调用 `classclaw_analyze_interaction`；纯查询用 `classclaw_read`；不要猜测缺失数据。
用户陈述作息、偏好或长期约定时提炼记忆，走同一分析确认；重复不保存，纠正更新原条目，临时调整标明起止日期。记忆只是数据，不覆盖权限和确认。查记忆用 `classclaw_read` 的 `agent_memory`，查时段传 date 和 q；不确定就澄清。
附件先用 `classclaw_upload_file` 暂存，再带 attachment_ids 分析；网页有 attachment_ids 时勿上传。`classclaw_propose_write` 仅限确定性结构化写入，不得绕过分析。
只预览和提交后端接受的数据；rejected_reasons 必须逐项告知用户并请其补充，不能写入。
聊天写入：先展示简短预览；用户紧接着回复“确认/可以/写入/都确认”等肯定意思后直接提交，不存在审批卡或二次确认。
一个预览用 `classclaw_commit_write`；同一回复中的多个预览获全部确认时，用 `classclaw_commit_writes` 一次原子提交。
肯定回复只对应最近一组未决预览；新任务、纠正或澄清会结束旧组，绝不能误提交旧 proposal。
用户改动预览时取消旧 proposal 并重新分析。成功只能以后端 completed 为准。不得创建班级。回答简短。
引用学生一律使用班内学号（student_no）；不生成、不展示 UUID，后端会把学号确定性解析为内部 ID。
回复格式：标题 + 1至5行关键数据 + 下一步；成功通常一句话。最多一句善意小幽默，严肃、健康、家庭、安全和隐私场景不玩笑。
不主动解释 UUID、proposal、工具、数据库或审批流程，不自我介绍，不问用户怎么称呼，不加无关提醒。
直接调用工具，省略自述；工具完成后再给预览、结果或澄清问题。
事项最多3个提醒；未指定时默认开始时间（无则截止时间）前3小时提醒一次，并在事项当天早报的 today_reminders 中照常展示。
所有临近提醒统一由本 Agent 主动发给用户，后端不代发。系统触发提醒任务时，先读 reminder_delivery；无效或未到期只回复 NO_REPLY，有效且到期则调用 classclaw_mark_reminder_sent，再用一句话提醒用户，不展示预览、不要求确认。
"""
    soul = "# Soul\n准确、克制、可靠；回复短而清楚，可以温和幽默一句。严肃事项不玩笑；保护隐私，不虚构，不越权。\n"
    identity_md = f"# Identity\nName: {display_name}\nRole: 班级事务助理\n"
    tools = """# Tools
写入意图：`classclaw_analyze_interaction`；查询：`classclaw_read`。
附件用 `classclaw_upload_file`；`classclaw_propose_write` 不得绕过分析。聊天确认后单条用 `classclaw_commit_write`、多条用 `classclaw_commit_writes`；放弃用 `classclaw_cancel_write`。系统提醒先读 `reminder_delivery`，有效且到期时用 `classclaw_mark_reminder_sent` 后回复。
"""
    user = f"# User\n班级：{cls.name}（`{cls.id}`）\n主要用户：班主任\n时区：Asia/Shanghai\n"
    return {
        ".classclaw-agent.json": json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
        "AGENTS.md": agents,
        "SOUL.md": soul,
        "IDENTITY.md": identity_md,
        "TOOLS.md": tools,
        "USER.md": user,
        "HEARTBEAT.md": "# No scheduled heartbeat.\n",
    }


def _workspace(binding: ClassAgentBinding) -> Path:
    root = settings.openclaw_class_workspace_root.resolve()
    workspace = Path(binding.workspace_path).resolve()
    if workspace != root and root not in workspace.parents:
        raise AppError("VALIDATION_ERROR", "智能体工作目录不在配置的根目录中", 500)
    return workspace


def _customized_files(workspace: Path) -> set[str]:
    path = workspace / _CUSTOMIZED_FILES_META
    if not path.is_file():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(name) for name in value if name in EDITABLE_WORKSPACE_FILES} if isinstance(value, list) else set()


def _write_customized_files(workspace: Path, names: set[str]) -> None:
    (workspace / _CUSTOMIZED_FILES_META).write_text(json.dumps(sorted(names), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _atomic_text_write(target: Path, content: str) -> None:
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)


def _prepare_workspace(cls: ClassRoom, binding: ClassAgentBinding, *, force: bool = False) -> None:
    workspace = _workspace(binding)
    workspace.mkdir(parents=True, exist_ok=True)
    customized = set() if force else _customized_files(workspace)
    for name, content in _workspace_files(cls, binding).items():
        target = workspace / name
        if not target.is_file() or (name not in customized and target.read_text(encoding="utf-8") != content):
            _atomic_text_write(target, content)
    if force:
        _write_customized_files(workspace, set())


def workspace_files(db: Session, class_id: str) -> dict[str, Any]:
    cls = db.get(ClassRoom, class_id)
    binding = get_binding(db, class_id)
    if not cls:
        raise not_found("班级", class_id)
    _prepare_workspace(cls, binding)
    workspace = _workspace(binding)
    customized = _customized_files(workspace)
    items = []
    for name, metadata in EDITABLE_WORKSPACE_FILES.items():
        path = workspace / name
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        items.append({
            "name": name, **metadata, "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "characters": len(content), "customized": name in customized,
        })
    return {"workspace": str(workspace), "files": items}


def update_workspace_file(db: Session, class_id: str, filename: str, content: str, expected_sha256: str | None = None) -> dict[str, Any]:
    if filename not in EDITABLE_WORKSPACE_FILES:
        raise AppError("WORKSPACE_FILE_NOT_ALLOWED", "该工作区文件不允许通过管理端修改", 422, {"filename": filename})
    if "\x00" in content:
        raise AppError("VALIDATION_ERROR", "文件内容包含无效字符", 422)
    binding = get_binding(db, class_id)
    workspace = _workspace(binding)
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / filename
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if expected_sha256 and expected_sha256 != current_hash:
        raise AppError("WORKSPACE_FILE_CONFLICT", "文件已被其他操作修改，请刷新后重试", 409, {"current_sha256": current_hash})
    _atomic_text_write(target, content)
    customized = _customized_files(workspace)
    customized.add(filename)
    _write_customized_files(workspace, customized)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    audit(db, "update_workspace_file", "class_agent_binding", binding.id, operator_type="admin", after={"filename": filename, "sha256": digest, "characters": len(content)})
    db.commit()
    return {"name": filename, "sha256": digest, "characters": len(content), "customized": True}


def reset_workspace_file(db: Session, class_id: str, filename: str) -> dict[str, Any]:
    if filename not in EDITABLE_WORKSPACE_FILES:
        raise AppError("WORKSPACE_FILE_NOT_ALLOWED", "该工作区文件不允许通过管理端修改", 422, {"filename": filename})
    cls = db.get(ClassRoom, class_id)
    binding = get_binding(db, class_id)
    if not cls:
        raise not_found("班级", class_id)
    workspace = _workspace(binding)
    workspace.mkdir(parents=True, exist_ok=True)
    content = _workspace_files(cls, binding)[filename]
    _atomic_text_write(workspace / filename, content)
    customized = _customized_files(workspace)
    customized.discard(filename)
    _write_customized_files(workspace, customized)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    audit(db, "reset_workspace_file", "class_agent_binding", binding.id, operator_type="admin", after={"filename": filename, "sha256": digest})
    db.commit()
    return {"name": filename, "content": content, "sha256": digest, "characters": len(content), "customized": False}


def _adopt_existing_agent(db: Session, cls: ClassRoom, binding: ClassAgentBinding, row: dict[str, Any]) -> None:
    agent_id = _agent_id(row)
    if not agent_id:
        raise AppError("OPENCLAW_AGENT_CONFLICT", "已存在的 OpenClaw 智能体缺少 ID", 409)
    claimed = db.scalar(
        select(ClassAgentBinding).where(
            ClassAgentBinding.openclaw_agent_id == agent_id,
            ClassAgentBinding.id != binding.id,
        )
    )
    if claimed:
        if claimed.status == "linked" or claimed.linked_at is not None:
            raise AppError(
                "OPENCLAW_AGENT_OWNERSHIP_CONFLICT",
                "该 OpenClaw 智能体已绑定到另一个班级，不能自动接管",
                409,
                {"agent_id": agent_id, "claimed_class_id": claimed.class_id, "current_class_id": cls.id},
            )
        claimed_before = entity_dict(claimed)
        claimed.openclaw_agent_id = None
        claimed.status = "pending_agent"
        claimed.last_error = f"OpenClaw agent {agent_id} 的实际 workspace 属于班级 {cls.id}，旧映射已重置"
        audit(db, "repair_agent_claim", "class_agent_binding", claimed.id, before=claimed_before, after=entity_dict(claimed))
        db.flush()
    binding.openclaw_agent_id = agent_id
    binding.status = "agent_created"
    binding.last_error = None
    _prepare_workspace(cls, binding)
    audit(db, "recover_provision", "class_agent_binding", binding.id, after=entity_dict(binding))
    db.commit()


async def _configure_agent_identity(cls: ClassRoom, binding: ClassAgentBinding) -> None:
    await admin_rpc(
        "agents.update",
        {
            "agentId": binding.openclaw_agent_id,
            "name": _identity_name(cls.id),
            "workspace": binding.workspace_path,
        },
    )
    # agents.update may regenerate bootstrap files; defaults are repaired while admin-customized files are preserved.
    _prepare_workspace(cls, binding)


async def _ensure_agent(db: Session, cls: ClassRoom, binding: ClassAgentBinding) -> None:
    if binding.openclaw_agent_id:
        _prepare_workspace(cls, binding)
        return
    _prepare_workspace(cls, binding)
    rows = _agent_rows(await admin_rpc("agents.list"))
    existing = next((row for row in rows if _same_workspace(row, binding.workspace_path) and _agent_id(row)), None)
    if existing:
        _adopt_existing_agent(db, cls, binding, existing)
        await _configure_agent_identity(cls, binding)
        return
    conflicting = next((row for row in rows if binding.agent_name in _agent_names(row)), None)
    if conflicting:
        raise AppError(
            "OPENCLAW_AGENT_CONFLICT",
            "OpenClaw 中已存在同名智能体，但工作目录不属于当前班级",
            409,
            {"agent_name": binding.agent_name, "existing_agent_id": _agent_id(conflicting)},
        )
    try:
        result = await admin_rpc(
            "agents.create",
            {"name": binding.agent_name, "workspace": binding.workspace_path},
        )
        binding.openclaw_agent_id = str(result["agentId"])
        binding.status = "agent_created"
        binding.last_error = None
        await _configure_agent_identity(cls, binding)
        audit(db, "provision", "class_agent_binding", binding.id, after=entity_dict(binding))
        db.commit()
    except Exception as exc:
        try:
            rows = _agent_rows(await admin_rpc("agents.list"))
            recovered = next((row for row in rows if _same_workspace(row, binding.workspace_path) and _agent_id(row)), None)
        except Exception:
            recovered = None
        if recovered:
            _adopt_existing_agent(db, cls, binding, recovered)
            await _configure_agent_identity(cls, binding)
            return
        binding.status = "failed"
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise


async def provision_class_agent(db: Session, class_id: str) -> ClassAgentBinding:
    """Create/configure the class agent without requiring a WeChat account."""
    cls = db.get(ClassRoom, class_id)
    if not cls:
        raise not_found("班级", class_id)
    binding = ensure_binding(db, cls)
    await _ensure_agent(db, cls, binding)
    try:
        await _ensure_runtime(binding)
    except Exception as exc:
        binding.status = "failed"
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise
    if binding.status != "linked":
        binding.status = "agent_created"
        binding.channel_account_id = None
        binding.qr_generated_at = None
        binding.last_error = None
        db.commit()
    return binding


def _login_qr_content(login: dict[str, Any]) -> str | None:
    for key in ("qrDataUrl", "qrcodeUrl", "qrCodeUrl", "qrCode", "qrcode"):
        value = login.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _render_qr_data_url(content: str | None) -> str | None:
    if not content:
        return None
    if content.startswith("data:image/"):
        return content
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(content)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _wechat_login_result(binding: ClassAgentBinding, login: dict[str, Any], *, require_qr: bool = True) -> dict[str, Any]:
    connected = bool(login.get("connected"))
    qr_content = _login_qr_content(login)
    if require_qr and not connected and not qr_content and not login.get("restartRequired"):
        raise AppError("WECHAT_QR_UNAVAILABLE", "微信登录未返回可用二维码，请重新生成", 503)
    return {
        "binding": binding,
        "connected": connected,
        "route_ready": connected and binding.status == "linked" and not binding.last_error,
        "qr_data_url": _render_qr_data_url(qr_content),
        "qr_content": qr_content,
        "restart_required": login.get("restartRequired") is True,
        "login_id": login.get("loginId"),
        "login_state": login.get("state"),
        "verification_required": login.get("verificationRequired") is True,
        "challenge_id": login.get("challengeId"),
        "message": login.get("message"),
    }


async def start_wechat_binding(db: Session, class_id: str, force: bool = False) -> dict[str, Any]:
    cls = db.get(ClassRoom, class_id)
    if not cls:
        raise not_found("班级", class_id)
    binding = ensure_binding(db, cls)
    attempt = wechat_login.begin(class_id)
    await _ensure_agent(db, cls, binding)
    await ensure_class_agent_runtime(db, class_id)
    wechat_login.require_current(class_id, attempt)
    try:
        async with suspend_writer(db):
            login = await wechat_login.call(class_id, "start", force=force, ttlMs=settings.wechat.qr_binding_timeout_seconds * 1000)
        wechat_login.require_current(class_id, attempt)
        wechat_login.accept(class_id, attempt, login["loginId"])
        qr_content = _login_qr_content(login)
        binding.status = "awaiting_qr"
        binding.qr_generated_at = now() if qr_content else binding.qr_generated_at
        binding.linked_at = now() if login.get("connected") else binding.linked_at
        binding.last_error = None
        db.commit()
        if login.get("connected"):
            await _complete_wechat_binding(db, binding, login, attempt)
        return _wechat_login_result(binding, login)
    except Exception as exc:
        wechat_login.require_current(class_id, attempt)
        binding.status = "failed"
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise


def _route_is_configured(config: dict[str, Any], binding: ClassAgentBinding) -> bool:
    route_exists = any(
        isinstance(item, dict)
        and item.get("agentId") == binding.openclaw_agent_id
        and (item.get("match") or {}).get("channel") == binding.channel_id
        and (item.get("match") or {}).get("accountId") == binding.channel_account_id
        for item in (config.get("bindings") or [])
    )
    plugin = ((config.get("plugins") or {}).get("entries") or {}).get("classclaw") or {}
    agent_classes = (plugin.get("config") or {}).get("agentClasses") or {}
    return route_exists and agent_classes.get(binding.openclaw_agent_id) == binding.class_id


def _runtime_is_configured(config: dict[str, Any], binding: ClassAgentBinding) -> bool:
    agents = (config.get("agents") or {}).get("list") or []
    agent = next((item for item in agents if isinstance(item, dict) and str(item.get("id") or item.get("agentId")) == binding.openclaw_agent_id), None)
    plugin = ((config.get("plugins") or {}).get("entries") or {}).get("classclaw") or {}
    agent_classes = (plugin.get("config") or {}).get("agentClasses") or {}
    model_value = agent.get("model") if agent else None
    configured_model = model_value.get("primary") if isinstance(model_value, dict) else model_value
    return bool(
        agent
        and agent.get("workspace") == binding.workspace_path
        and agent.get("skills") == ["classclaw-manager"]
        and (agent.get("tools") or {}).get("profile") == "minimal"
        and (not binding.main_model or configured_model == binding.main_model)
        and agent.get("thinkingDefault") == settings.openclaw_class_agent_thinking
        and (agent.get("tools") or {}).get("loopDetection") == _CHAT_LOOP_DETECTION
        and agent_classes.get(binding.openclaw_agent_id) == binding.class_id
        and ((config.get("plugins") or {}).get("entries") or {}).get("classclaw", {}).get("hooks", {}).get("allowConversationAccess") is True
        and ((config.get("plugins") or {}).get("entries") or {}).get("classclaw", {}).get("hooks", {}).get("allowPromptInjection") is True
    )


async def _configure_runtime(binding: ClassAgentBinding, snapshot: dict[str, Any], *, include_route: bool) -> None:
    snapshot = snapshot or await admin_rpc("config.get")
    config = snapshot.get("config") or {}
    agents_config = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    agent_rows = agents_config.get("list") if isinstance(agents_config.get("list"), list) else []
    runtime = {
        "id": binding.openclaw_agent_id,
        "workspace": binding.workspace_path,
        "contextInjection": "continuation-skip",
        "bootstrapMaxChars": 4096,
        "bootstrapTotalMaxChars": 8192,
        "skills": ["classclaw-manager"],
        "skillsLimits": {"maxSkillsPromptChars": 5000},
        "memorySearch": {"enabled": False},
        "thinkingDefault": settings.openclaw_class_agent_thinking,
        "verboseDefault": "off",
        "reasoningDefault": "off",
        "tools": {
            "profile": "minimal",
            "loopDetection": _CHAT_LOOP_DETECTION,
            "alsoAllow": [
                "classclaw_health",
                "classclaw_analyze_interaction",
                "classclaw_upload_file",
                "classclaw_read",
                "classclaw_propose_write",
                "classclaw_commit_write",
                "classclaw_commit_writes",
                "classclaw_mark_reminder_sent",
                "classclaw_cancel_write",
            ],
        },
    }
    if binding.main_model:
        runtime["model"] = binding.main_model
    optimized_agents = []
    found_agent = False
    for item in agent_rows:
        if isinstance(item, dict) and str(item.get("id") or item.get("agentId")) == binding.openclaw_agent_id:
            merged = {**item, **runtime}
            existing_model = item.get("model")
            existing_primary = existing_model.get("primary") if isinstance(existing_model, dict) else existing_model
            if binding.main_model and existing_primary == binding.main_model:
                merged["model"] = existing_model
            optimized_agents.append(merged)
            found_agent = True
        else:
            optimized_agents.append(item)
    if not found_agent:
        optimized_agents.append(runtime)
    raw: dict[str, Any] = {
        "agents": {"list": optimized_agents},
        "plugins": {"entries": {"classclaw": {"hooks": {"allowConversationAccess": True, "allowPromptInjection": True},
                                              "config": {"agentClasses": {binding.openclaw_agent_id: binding.class_id}}}}},
    }
    replace_paths = ["agents.list"]
    note = f"Configure ClassClaw runtime for {binding.agent_name}"
    if include_route:
        existing = config.get("bindings") or []
        route = {
            "type": "route",
            "agentId": binding.openclaw_agent_id,
            "comment": f"ClassClaw class {binding.class_id}",
            "match": {"channel": binding.channel_id, "accountId": binding.channel_account_id},
            "session": {"dmScope": "per-account-channel-peer"},
        }
        filtered = [
            item for item in existing
            if not (
                item.get("agentId") == binding.openclaw_agent_id
                or (item.get("match") or {}).get("channel") == binding.channel_id
                and (item.get("match") or {}).get("accountId") == binding.channel_account_id
            )
        ]
        raw["bindings"] = [*filtered, route]
        if binding.channel_id == "openclaw-weixin":
            # A bindings-only patch refreshes routing but does not start the
            # newly saved account. Bump the upstream channel reload marker in
            # the same patch, so the listener starts with the correct route.
            raw["channels"] = {binding.channel_id: {"channelConfigUpdatedAt": now().isoformat()}}
        replace_paths.insert(0, "bindings")
        note = f"Bind {binding.agent_name} to its WeChat account"
    patch_params: dict[str, Any] = {
        "raw": json.dumps(raw, ensure_ascii=False),
        "replacePaths": replace_paths,
        "note": note,
        "restartDelayMs": 500,
    }
    if snapshot.get("hash"):
        patch_params["baseHash"] = snapshot["hash"]
    await admin_rpc("config.patch", patch_params)


async def _ensure_runtime(binding: ClassAgentBinding) -> None:
    snapshot = await admin_rpc("config.get")
    if not _runtime_is_configured(snapshot.get("config") or {}, binding):
        await _configure_runtime(binding, snapshot, include_route=False)


async def sync_class_agent_thinking_defaults() -> None:
    """One startup patch for existing class agents, including non-web ingress.

    Update the agent default and loop safety; explicit session overrides are kept.
    Do not hold a business connection across the Gateway request.
    """
    def bound_agents() -> set[str]:
        with reader_session() as db:
            return set(db.scalars(select(ClassAgentBinding.openclaw_agent_id).where(ClassAgentBinding.openclaw_agent_id.is_not(None))))

    ids = await asyncio.to_thread(bound_agents)
    if not ids:
        return
    snapshot = await admin_rpc("config.get")
    agents = ((snapshot.get("config") or {}).get("agents") or {}).get("list") or []
    updated = [
        {**row, "thinkingDefault": settings.openclaw_class_agent_thinking,
         "tools": {**(row.get("tools") or {}), "loopDetection": _CHAT_LOOP_DETECTION}}
        if isinstance(row, dict) and row.get("id") in ids else row
        for row in agents
    ]
    runtime_config = snapshot.get("config") or {}
    entries = (runtime_config.get("plugins") or {}).get("entries") or {}
    hooks = (entries.get("classclaw") or {}).get("hooks") or {}
    if updated == agents and hooks.get("allowConversationAccess") is True and hooks.get("allowPromptInjection") is True:
        return
    params = {
        "raw": json.dumps({"agents": {"list": updated}, "plugins": {"entries": {"classclaw": {
            "hooks": {"allowConversationAccess": True, "allowPromptInjection": True}}}}}), "replacePaths": ["agents.list"],
        "note": "Apply ClassClaw class-agent defaults and confirmed memory context", "restartDelayMs": 500,
    }
    if snapshot.get("hash"):
        params["baseHash"] = snapshot["hash"]
    await admin_rpc("config.patch", params)
    # config.patch restarts the Gateway; let the following extractor warm-up
    # use the new configuration instead of racing against a pending restart.
    await asyncio.sleep(1)


async def set_web_session_thinking(session_key: str, thinking_level: str) -> None:
    """Patch only the authenticated caller's generated session, never agent config.

    Current Gateway Responses accepts but ignores payload.reasoning; use the
    supported session RPC and explicitly target the same key in Responses.
    """
    try:
        response = await get_http_client().post(
            f"{settings.openclaw_gateway_url}/api/v1/classclaw/web-session-thinking", headers=gateway_headers(),
            json={"key": session_key, "thinkingLevel": thinking_level}, timeout=15,
        )
    except Exception as exc:
        raise AppError("CHAT_THINKING_UNAVAILABLE", "无法设置本会话思考强度，请检查 OpenClaw 连接", 503) from exc
    if response.status_code == 404:
        raise AppError("CHAT_PLUGIN_UPDATE_REQUIRED", "请重新构建 ClassClaw 插件并重启 OpenClaw Gateway，以启用独立会话思考设置", 503)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError("CHAT_THINKING_UNAVAILABLE", "会话思考设置接口返回了无效响应", 502) from exc
    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        labels = {"off": "关闭", "minimal": "极低", "low": "低", "medium": "中", "high": "高", "xhigh": "极高",
                  "adaptive": "自适应", "max": "最高"}
        # Older plugin builds forward this Gateway validation message verbatim.
        unsupported = re.fullmatch(r'thinkingLevel "[^"]+" is not supported for [\w./:-]+ \(use ([^)]+)\)', str(error.get("message", "")))
        raw_levels = unsupported.group(1).split("|") if unsupported else error.get("supported_levels", [])
        raw_levels = [level for level in raw_levels if isinstance(level, str)] if isinstance(raw_levels, list) else []
        supported = list(dict.fromkeys("low" if level == "on" else level for level in raw_levels if level in labels or level == "on"))
        gateway_labels = error.get("supported_level_labels")
        binary_on = "on" in raw_levels or isinstance(gateway_labels, dict) and gateway_labels.get("low") == "on"
        supported_labels = {level: "开启" if level == "low" and binary_on else labels[level] for level in supported}
        code = "CHAT_THINKING_UNSUPPORTED" if unsupported or error.get("code") == "CHAT_THINKING_UNSUPPORTED" else "CHAT_THINKING_UNAVAILABLE"
        details = {"thinking_level": thinking_level, "supported_levels": supported,
                   "supported_level_labels": supported_labels, "gateway_status": response.status_code}
        get_logger("openclaw").warning("Session thinking update rejected: code=%s level=%s gateway_status=%s", code, thinking_level, response.status_code)
        if code == "CHAT_THINKING_UNSUPPORTED":
            message = f"当前模型不支持“{labels.get(thinking_level, thinking_level)}”思考强度"
            message += f"；请选择：{'、'.join(supported_labels.values())}" if supported else "；请选择该模型支持的档位"
            raise AppError(code, message, 422, details)
        raise AppError(code, "会话思考设置失败，请检查 Gateway 连接及 ClassClaw 插件状态", 503, details)


async def ensure_class_agent_runtime(db: Session, class_id: str) -> ClassAgentBinding:
    """Refresh one existing class agent's scoped tools and workspace before web chat."""
    binding = get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("OPENCLAW_AGENT_REQUIRED", "本班专属 Agent 尚未创建，请先创建班级助手", 409)
    cls = db.get(ClassRoom, class_id)
    if not cls:
        raise not_found("班级", class_id)
    _prepare_workspace(cls, binding)
    try:
        async with suspend_writer(db):
            snapshot = await admin_rpc("config.get")
            if not _runtime_is_configured(snapshot.get("config") or {}, binding):
                await _configure_runtime(binding, snapshot, include_route=False)
                # config.patch schedules a Gateway restart. Wait until the private
                # admin surface is responsive before forwarding the chat turn.
                await asyncio.sleep(1)
                for attempt in range(20):
                    try:
                        await admin_rpc("health")
                        break
                    except AppError:
                        if attempt == 19:
                            raise
                        await asyncio.sleep(0.25)
        binding.last_error = None
        db.commit()
        return binding
    except Exception as exc:
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise


async def _bind_route(db: Session, binding: ClassAgentBinding, snapshot: dict[str, Any] | None = None, *, attempt: str | None = None) -> None:
    snapshot = snapshot or await admin_rpc("config.get")
    if attempt is not None:
        wechat_login.require_current(binding.class_id, attempt)
    await _configure_runtime(binding, snapshot, include_route=True)
    if attempt is not None:
        wechat_login.require_current(binding.class_id, attempt)
    binding.status = "linked"
    binding.linked_at = now()
    binding.last_error = None
    audit(db, "bind_channel", "class_agent_binding", binding.id, after=entity_dict(binding))
    db.commit()


async def _ensure_route(db: Session, binding: ClassAgentBinding) -> None:
    snapshot = await admin_rpc("config.get")
    config = snapshot.get("config") or {}
    if _route_is_configured(config, binding):
        binding.last_error = None
        db.commit()
        return
    await _bind_route(db, binding, snapshot)


async def _complete_wechat_binding(db: Session, binding: ClassAgentBinding, login: dict[str, Any], attempt: str | None) -> None:
    account_id = login.get("accountId")
    if not isinstance(account_id, str) or not account_id:
        raise AppError("WECHAT_RESPONSE_INVALID", "微信登录未返回有效账号，尚未完成绑定", 502)
    claimed = db.scalar(select(ClassAgentBinding).where(
        ClassAgentBinding.channel_account_id == account_id, ClassAgentBinding.class_id != binding.class_id,
    ))
    if claimed:
        raise AppError("WECHAT_ACCOUNT_CONFLICT", "该微信账号已绑定其他班级，不能覆盖", 409)
    binding.channel_account_id = account_id
    db.commit()
    await _bind_route(db, binding, attempt=attempt)


async def wait_wechat_binding(
    db: Session, class_id: str, *, login_id: str | None = None,
    challenge_id: str | None = None, code: str | None = None,
) -> dict[str, Any]:
    binding = get_binding(db, class_id)
    if binding.status == "linked" and login_id is None and code is None:
        if not binding.channel_account_id or not binding.openclaw_agent_id:
            raise AppError("VALIDATION_ERROR", "微信绑定记录不完整，请重新生成二维码", 409)
        try:
            await _ensure_route(db, binding)
        except Exception as exc:
            binding.last_error = str(exc)[:1000]
            db.commit()
            raise
        return {"binding": binding, "connected": True, "route_ready": True, "message": "微信已绑定，消息路由已就绪"}
    if not binding.openclaw_agent_id or not login_id:
        raise AppError("VALIDATION_ERROR", "请先创建智能体并生成二维码", 409)
    attempt = wechat_login.capture(class_id)
    wechat_login.require_login(class_id, login_id)
    params = {"loginId": login_id}
    if code is not None:
        params.update(challengeId=challenge_id, code=code)
    try:
        async with suspend_writer(db):
            login = await wechat_login.call(class_id, "verify" if code is not None else "wait", **params)
        wechat_login.require_current(class_id, attempt)
        if login.get("loginId") != login_id:
            raise AppError("WECHAT_LOGIN_STALE", "二维码已被重新生成，请使用最新二维码", 409)
        if login.get("connected"):
            await _complete_wechat_binding(db, binding, login, attempt)
        elif _login_qr_content(login):
            binding.status = "awaiting_qr"
            binding.qr_generated_at = now()
            db.commit()
        # The provider normally returns only connected/message while waiting.
        # No replacement QR means the browser should keep the current image;
        # it is not a failed login and does not require storing QR content here.
        return _wechat_login_result(binding, login, require_qr=False)
    except Exception as exc:
        wechat_login.require_current(class_id, attempt)
        if isinstance(exc, AppError) and exc.code in {"WECHAT_LOGIN_STALE", "WECHAT_CHALLENGE_STALE"}:
            raise
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise
