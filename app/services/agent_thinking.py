from __future__ import annotations

import asyncio
from typing import Any, get_args

from sqlalchemy.orm import Session

from app.config import ThinkingLevel, settings
from app.core.errors import AppError
from app.database import suspend_writer
from app.services import openclaw_provisioning

_LEVELS = set(get_args(ThinkingLevel))
_OPTIONS_TIMEOUT_SECONDS = 10
_LABELS = {"off": "关闭", "on": "开启", "minimal": "极低", "low": "低", "medium": "中", "high": "高",
           "xhigh": "极高", "adaptive": "自适应", "max": "最高"}


async def agent_thinking_options(agent_id: str) -> dict[str, Any]:
    # Use the Gateway's resolved provider/model/runtime policy, including binary
    # display labels. Do not infer capabilities from a model name or reasoning flag.
    try:
        async with asyncio.timeout(_OPTIONS_TIMEOUT_SECONDS):
            payload = await openclaw_provisioning.admin_rpc("agents.list")
    except TimeoutError as exc:
        raise AppError("CHAT_THINKING_OPTIONS_UNAVAILABLE", "读取当前模型的思考选项超时，请稍后重试", 503) from exc
    rows = payload.get("agents", []) if isinstance(payload, dict) else []
    rows = rows if isinstance(rows, list) else []
    row = next((item for item in rows if isinstance(item, dict) and item.get("id") == agent_id), None)
    if row is None:
        raise AppError("OPENCLAW_AGENT_NOT_FOUND", "OpenClaw 配置中找不到本班 Agent", 409)
    levels = {}
    raw_levels = row.get("thinkingLevels")
    for item in raw_levels if isinstance(raw_levels, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in _LEVELS:
            continue
        label = item.get("label")
        label = label.strip()[:60] if isinstance(label, str) and label.strip() else item["id"]
        levels[item["id"]] = _LABELS.get(label, label)
    if not levels:
        raise AppError("CHAT_THINKING_OPTIONS_UNAVAILABLE", "OpenClaw 未返回当前模型的思考选项，请检查 Gateway 版本和模型配置", 503)
    configured = settings.openclaw_class_agent_thinking
    default = configured if configured in levels else row.get("thinkingDefault")
    default = default if isinstance(default, str) and default in levels else None
    model = row.get("model")
    model = model.get("primary") if isinstance(model, dict) else model
    return {
        "agent_id": agent_id,
        "model": model if isinstance(model, str) else None,
        "levels": [{"id": key, "label": value} for key, value in levels.items()],
        "default_level": default,
        "configured_default_level": configured,
        "default_adjusted": default != configured,
    }


async def class_thinking_options(db: Session, class_id: str) -> dict[str, Any]:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("OPENCLAW_AGENT_REQUIRED", "本班专属 Agent 尚未创建", 409)
    async with suspend_writer(db):
        return await agent_thinking_options(binding.openclaw_agent_id)


async def resolve_thinking_level(agent_id: str, requested: str | None) -> str:
    profile = await agent_thinking_options(agent_id)
    labels = {item["id"]: item["label"] for item in profile["levels"]}
    level = requested if requested is not None else profile["default_level"]
    if level not in labels:
        selected = _LABELS.get(requested, requested) if requested else "配置默认"
        raise AppError(
            "CHAT_THINKING_UNSUPPORTED", f"当前模型不支持“{selected}”思考强度；请选择：{'、'.join(labels.values())}", 422,
            {"thinking_level": requested, "model": profile["model"], "supported_levels": list(labels), "supported_level_labels": labels},
        )
    return level
