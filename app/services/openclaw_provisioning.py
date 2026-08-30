from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import qrcode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import ClassAgentBinding, ClassRoom
from app.services.common import audit, entity_dict
from app.services.http_client import get_http_client
from app.utils.time import now


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.openclaw_gateway_token:
        headers["Authorization"] = f"Bearer {settings.openclaw_gateway_token}"
    return headers


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
            headers=_headers(),
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


def class_id_for_agent(db: Session, agent_id: str) -> str | None:
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.openclaw_agent_id == agent_id))
    return binding.class_id if binding else None


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
自然语言、微信、OCR、语音和附件先调用 `classclaw_analyze_interaction`；不要猜测缺失数据。
聊天写入：先展示简短预览；用户紧接着回复“确认/可以/写入/都确认”等肯定意思后直接提交，不存在审批卡或二次确认。
一个预览用 `classclaw_commit_write`；同一回复中的多个预览获全部确认时，用 `classclaw_commit_writes` 一次原子提交。
肯定回复只对应最近一组未决预览；新任务、纠正或澄清会结束旧组，绝不能误提交旧 proposal。
用户改动预览时取消旧 proposal 并重新分析。成功只能以后端 completed 为准。不得创建班级。回答简短。
回复格式：标题 + 1至5行关键数据 + 下一步；成功通常一句话。最多一句善意小幽默，严肃、健康、家庭、安全和隐私场景不玩笑。
不主动解释 UUID、proposal、工具、数据库或审批流程，不自我介绍，不问用户怎么称呼，不加无关提醒。
事项最多3个提醒；未指定时默认开始时间（无则截止时间）前3小时提醒一次，并在事项当天早报的 today_reminders 中照常展示。
所有临近提醒统一由本 Agent 主动发给用户，后端不代发。系统触发提醒任务时，先读 reminder_delivery；无效或未到期只回复 NO_REPLY，有效且到期则调用 classclaw_mark_reminder_sent，再用一句话提醒用户，不展示预览、不要求确认。
"""
    soul = "# Soul\n准确、克制、可靠；回复短而清楚，可以温和幽默一句。严肃事项不玩笑；保护隐私，不虚构，不越权。\n"
    identity_md = f"# Identity\nName: {display_name}\nRole: 班级事务助理\nEmoji: 🏫\n"
    tools = """# Tools
普通输入：`classclaw_analyze_interaction`；查询：`classclaw_read`。
附件先 `classclaw_upload_file`。聊天明确确认后单条用 `classclaw_commit_write`、多条用 `classclaw_commit_writes`；无需额外审批。放弃时用 `classclaw_cancel_write`。系统提醒任务先读 `reminder_delivery`，有效且到期时用 `classclaw_mark_reminder_sent` 后主动发一句提醒。
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


def _prepare_workspace(cls: ClassRoom, binding: ClassAgentBinding) -> None:
    root = settings.openclaw_class_workspace_root.resolve()
    workspace = Path(binding.workspace_path).resolve()
    if workspace != root and root not in workspace.parents:
        raise AppError("VALIDATION_ERROR", "智能体工作目录不在配置的根目录中", 500)
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in _workspace_files(cls, binding).items():
        target = workspace / name
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")


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
            "emoji": "🏫",
        },
    )
    # agents.update may regenerate bootstrap files, so our compact policy wins last.
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
            {"name": binding.agent_name, "workspace": binding.workspace_path, "emoji": "🏫"},
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


def _wechat_login_result(binding: ClassAgentBinding, login: dict[str, Any]) -> dict[str, Any]:
    connected = bool(login.get("connected"))
    qr_content = _login_qr_content(login)
    if not connected and not qr_content:
        raise AppError("WECHAT_QR_UNAVAILABLE", "微信登录未返回可用二维码，请重新生成", 503)
    return {
        "binding": binding,
        "connected": connected,
        "route_ready": connected and binding.status == "linked" and not binding.last_error,
        "qr_data_url": _render_qr_data_url(qr_content),
        "qr_content": qr_content,
        "message": login.get("message"),
    }


async def start_wechat_binding(db: Session, class_id: str, force: bool = False) -> dict[str, Any]:
    cls = db.get(ClassRoom, class_id)
    if not cls:
        raise not_found("班级", class_id)
    binding = ensure_binding(db, cls)
    await _ensure_agent(db, cls, binding)
    account_alias = binding.channel_account_id or f"class-{re.sub(r'[^a-zA-Z0-9_-]', '-', class_id)[:36]}"
    try:
        login = await admin_rpc(
            "web.login.start",
            {"accountId": account_alias, "force": force, "timeoutMs": 30_000, "verbose": False},
        )
        binding.channel_account_id = str(login.get("accountId") or account_alias)
        qr_content = _login_qr_content(login)
        binding.status = "linked" if login.get("connected") else "awaiting_qr"
        binding.qr_generated_at = now() if qr_content else binding.qr_generated_at
        binding.linked_at = now() if login.get("connected") else binding.linked_at
        binding.last_error = None
        db.commit()
        if login.get("connected"):
            await _bind_route(db, binding)
        return _wechat_login_result(binding, login)
    except Exception as exc:
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
    return bool(
        agent
        and agent.get("workspace") == binding.workspace_path
        and agent.get("skills") == ["classclaw-manager"]
        and (agent.get("tools") or {}).get("profile") == "minimal"
        and agent_classes.get(binding.openclaw_agent_id) == binding.class_id
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
        "thinkingDefault": "off",
        "verboseDefault": "off",
        "reasoningDefault": "off",
        "tools": {
            "profile": "minimal",
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
    optimized_agents = []
    found_agent = False
    for item in agent_rows:
        if isinstance(item, dict) and str(item.get("id") or item.get("agentId")) == binding.openclaw_agent_id:
            optimized_agents.append({**item, **runtime})
            found_agent = True
        else:
            optimized_agents.append(item)
    if not found_agent:
        optimized_agents.append(runtime)
    raw: dict[str, Any] = {
        "agents": {"list": optimized_agents},
        "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {binding.openclaw_agent_id: binding.class_id}}}}},
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


async def _bind_route(db: Session, binding: ClassAgentBinding, snapshot: dict[str, Any] | None = None) -> None:
    snapshot = snapshot or await admin_rpc("config.get")
    await _configure_runtime(binding, snapshot, include_route=True)
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


async def wait_wechat_binding(db: Session, class_id: str, current_qr_data_url: str | None = None) -> dict[str, Any]:
    binding = get_binding(db, class_id)
    if binding.status == "linked":
        if not binding.channel_account_id or not binding.openclaw_agent_id:
            raise AppError("VALIDATION_ERROR", "微信绑定记录不完整，请重新生成二维码", 409)
        try:
            await _ensure_route(db, binding)
        except Exception as exc:
            binding.last_error = str(exc)[:1000]
            db.commit()
            raise
        return {"binding": binding, "connected": True, "route_ready": True, "message": "微信已绑定，消息路由已就绪"}
    if not binding.channel_account_id or not binding.openclaw_agent_id:
        raise AppError("VALIDATION_ERROR", "请先创建智能体并生成二维码", 409)
    params: dict[str, Any] = {"accountId": binding.channel_account_id, "timeoutMs": 15_000}
    if current_qr_data_url:
        # OpenClaw validates this field as a PNG data URL. Older web clients sent
        # the provider's raw QR content, so normalize it here for compatibility.
        params["currentQrDataUrl"] = _render_qr_data_url(current_qr_data_url)
    try:
        login = await admin_rpc("web.login.wait", params)
        if login.get("connected"):
            binding.channel_account_id = str(login.get("accountId") or binding.channel_account_id)
            await _bind_route(db, binding)
        elif _login_qr_content(login):
            binding.status = "awaiting_qr"
            binding.qr_generated_at = now()
            db.commit()
        return _wechat_login_result(binding, login)
    except Exception as exc:
        binding.last_error = str(exc)[:1000]
        db.commit()
        raise
