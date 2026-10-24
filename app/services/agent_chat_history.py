from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.database import suspend_writer
from app.models.entities import AgentChatHiddenConversation
from app.services import openclaw_provisioning
from app.services.common import audit

_SAFE_AGENT = re.compile(r"[a-zA-Z0-9_-]{1,100}\Z")
_MAX_INDEX_BYTES = 4 * 1024 * 1024
_MAX_HISTORY_BYTES = 16 * 1024 * 1024
_TITLE_BYTES = 256 * 1024


def conversation_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AppError("VALIDATION_ERROR", "对话标识无效", 422) from exc


def require_visible(db: Session, class_id: str, owner_id: str, identifier: str) -> None:
    if db.scalar(select(AgentChatHiddenConversation.id).where(
        AgentChatHiddenConversation.class_id == class_id,
        AgentChatHiddenConversation.owner_user_id == owner_id,
        AgentChatHiddenConversation.conversation_id == conversation_id(identifier),
    )):
        raise AppError("CHAT_REMOVED", "这段对话已从你的对话列表删除，请新建对话", 410)


def _unavailable() -> AppError:
    return AppError("CHAT_HISTORY_UNAVAILABLE", "暂时无法读取历史对话，请稍后重试或检查服务器会话目录", 503)


def _directory(agent_id: str) -> Path:
    if not _SAFE_AGENT.fullmatch(agent_id):
        raise _unavailable()
    root = settings.openclaw_state_dir.expanduser().resolve()
    path = root / "agents" / agent_id / "sessions"
    if any(item.is_symlink() for item in (root / "agents", path.parent, path)) or path.resolve() != path:
        raise _unavailable()
    return path


def _read(path: Path, limit: int, *, prefix: bool = False) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise _unavailable()
    try:
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
    except (OSError, ValueError) as exc:
        raise _unavailable() from exc
    if len(value) > limit and not prefix:
        raise AppError("CHAT_HISTORY_TOO_LARGE", "历史对话超过读取上限，请联系管理员导出或整理会话", 413)
    return value[:limit]


def _index(agent_id: str) -> tuple[Path, dict[str, Any]]:
    directory = _directory(agent_id)
    path = directory / "sessions.json"
    if not path.exists():
        return directory, {}
    try:
        value = json.loads(_read(path, _MAX_INDEX_BYTES))
    except (ValueError, UnicodeError) as exc:
        raise _unavailable() from exc
    if not isinstance(value, dict):
        raise _unavailable()
    return directory, value


def _session_file(directory: Path, row: dict[str, Any]) -> Path:
    # Resolve only a file directly inside this bound Agent's sessions directory.
    try:
        identifier = conversation_id(row.get("sessionId"))
    except AppError as exc:
        raise _unavailable() from exc
    raw = row.get("sessionFile")
    candidate = Path(raw) if isinstance(raw, str) and raw else directory / f"{identifier}.jsonl"
    if not candidate.is_absolute():
        candidate = directory / candidate
    if candidate.is_symlink() or candidate.resolve().parent != directory or candidate.name != f"{identifier}.jsonl":
        raise _unavailable()
    return candidate


def _date(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, tz=ZoneInfo(settings.timezone)).isoformat()
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(settings.timezone))
        return parsed.isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _messages(raw: bytes) -> list[dict[str, Any]]:
    messages = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue  # A writer may currently be appending the last JSONL line.
        if not isinstance(record, dict) or record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        role = message["role"]
        if role == "assistant" and (message.get("stopReason") in ("toolUse", "tool_calls")
                                    or message.get("channel") in ("analysis", "commentary")):
            continue
        content = message.get("content")
        if isinstance(content, list):
            # Intermediate tool chatter, arguments, results and thinking are never a visible reply.
            if role == "assistant" and any(isinstance(item, dict) and item.get("type") in ("toolCall", "tool_use", "tool_call") for item in content):
                continue
            text = "\n".join(item["text"] for item in content if isinstance(item, dict)
                             and item.get("type") in ("text", "input_text", "output_text") and isinstance(item.get("text"), str))
            if role == "user" and not text and content:
                text = "（此消息包含附件）"
        else:
            text = content if isinstance(content, str) else ""
        if not text.strip():
            continue
        item = {"id": hashlib.sha256(line).hexdigest()[:24], "role": role, "text": text,
                "timestamp": _date(record.get("timestamp") or message.get("timestamp"))}
        if role == "assistant" and message.get("stopReason") not in ("stop", "end_turn"):
            item["incomplete"] = True
        messages.append(item)
    return messages


def _prefix(agent_id: str, class_id: str, owner_id: str) -> str:
    return f"agent:{agent_id}:openresponses-user:classclaw-web-chat:{class_id}:{owner_id}:".lower()


def _list(agent_id: str, class_id: str, owner_id: str, hidden: set[str], offset: int, limit: int) -> dict:
    directory, index = _index(agent_id)
    prefix = _prefix(agent_id, class_id, owner_id)
    candidates = []
    for key, row in index.items():
        if not key.startswith(prefix) or not isinstance(row, dict):
            continue
        try:
            identifier = conversation_id(key[len(prefix):])
        except AppError:
            continue
        if identifier not in hidden:
            candidates.append((identifier, row))
    candidates.sort(key=lambda item: _date(item[1].get("updatedAt")) or "", reverse=True)
    items = []
    for identifier, row in candidates[offset:offset + limit]:
        path = _session_file(directory, row)
        visible = _messages(_read(path, _TITLE_BYTES, prefix=True))
        first = next((item for item in visible if item["role"] == "user"), None)
        items.append({"id": identifier, "title": first["text"][:40] if first else "历史对话",
                      "created_at": (first or {}).get("timestamp") or _date(row.get("sessionStartedAt")) or _date(row.get("updatedAt")),
                      "updated_at": _date(row.get("updatedAt"))})
    return {"items": items, "has_more": offset + limit < len(candidates)}


async def list_conversations(db: Session, class_id: str, owner_id: str, *, offset: int, limit: int) -> dict:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        return {"items": [], "has_more": False}
    hidden = set(db.scalars(select(AgentChatHiddenConversation.conversation_id).where(
        AgentChatHiddenConversation.class_id == class_id, AgentChatHiddenConversation.owner_user_id == owner_id,
    )))
    async with suspend_writer(db):
        return await asyncio.to_thread(_list, binding.openclaw_agent_id, class_id, owner_id, hidden, offset, limit)


async def conversation_messages(db: Session, class_id: str, owner_id: str, identifier: str, *, offset: int, limit: int) -> dict:
    identifier = conversation_id(identifier)
    require_visible(db, class_id, owner_id, identifier)
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("NOT_FOUND", "对话不存在", 404)

    def read() -> dict:
        directory, index = _index(binding.openclaw_agent_id)
        key = _prefix(binding.openclaw_agent_id, class_id, owner_id) + identifier
        row = index.get(key)
        if not isinstance(row, dict):
            raise AppError("NOT_FOUND", "对话不存在", 404)
        visible = _messages(_read(_session_file(directory, row), _MAX_HISTORY_BYTES))
        end = max(0, len(visible) - offset)
        start = max(0, end - limit)
        return {"conversation_id": identifier, "messages": visible[start:end], "has_more": start > 0}

    async with suspend_writer(db):
        return await asyncio.to_thread(read)


def remove_conversation(db: Session, class_id: str, owner_id: str, identifier: str) -> dict:
    identifier = conversation_id(identifier)
    existing = db.scalar(select(AgentChatHiddenConversation).where(
        AgentChatHiddenConversation.class_id == class_id, AgentChatHiddenConversation.owner_user_id == owner_id,
        AgentChatHiddenConversation.conversation_id == identifier,
    ))
    if not existing:
        db.add(AgentChatHiddenConversation(class_id=class_id, owner_user_id=owner_id, conversation_id=identifier))
        audit(db, "remove_chat_conversation", "class", class_id, operator_id=owner_id, after={"conversation_id": identifier})
        db.commit()
    return {"conversation_id": identifier, "removed": True}
