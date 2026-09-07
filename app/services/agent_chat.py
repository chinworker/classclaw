from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.entities import Attachment
from app.services import openclaw_bridge, openclaw_provisioning, operations

MAX_CHAT_FILES = 8
MAX_CHAT_TEXT_CHARS = 20_000


def _conversation_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AppError("VALIDATION_ERROR", "对话标识无效，请新建对话后重试", 422) from exc


def _message_text(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > MAX_CHAT_TEXT_CHARS:
        raise AppError(
            "VALIDATION_ERROR",
            f"单条消息不能超过 {MAX_CHAT_TEXT_CHARS} 个字符",
            422,
            {"max_characters": MAX_CHAT_TEXT_CHARS},
        )
    return text


def _attachment_view(attachment: Attachment) -> dict[str, Any]:
    return {
        "name": attachment.original_name,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
    }


async def send_message(
    db: Session,
    *,
    class_id: str,
    conversation_id: str,
    text: str | None,
    uploads: list[UploadFile],
    sender_id: str,
    requested_by: str,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Send one authenticated web turn to the class-scoped OpenClaw agent."""
    normalized_conversation_id = _conversation_id(conversation_id)
    normalized_text = _message_text(text)
    if not normalized_text and not uploads:
        raise AppError("VALIDATION_ERROR", "请输入消息或选择文件", 422)
    if len(uploads) > MAX_CHAT_FILES:
        raise AppError("VALIDATION_ERROR", f"每条消息最多上传 {MAX_CHAT_FILES} 个文件", 422)

    binding = await openclaw_provisioning.ensure_class_agent_runtime(db, class_id)

    message_id = str(uuid.uuid4())
    source_message_id = f"web:{normalized_conversation_id}:{message_id}"
    attachments: list[Attachment] = []
    for upload in uploads:
        attachment = operations.save_attachment(db, upload, source_message_id, "网页班级 Agent 对话")
        operations.link_attachment(db, attachment.id, "class", class_id)
        attachments.append(attachment)

    result = await openclaw_bridge.chat_with_class_agent(
        db=db,
        agent_id=binding.openclaw_agent_id,
        class_id=class_id,
        conversation_id=normalized_conversation_id,
        message_id=message_id,
        sender_id=sender_id,
        requested_by=requested_by,
        text=normalized_text,
        attachments=attachments,
        model_override=binding.image_model if any((item.mime_type or "").startswith("image/") for item in attachments) else None,
        cancelled=cancelled,
    )
    return {
        "conversation_id": normalized_conversation_id,
        "message_id": message_id,
        "reply": result["reply"],
        "response_id": result.get("response_id"),
        "agent_name": binding.agent_name,
        "attachments": [_attachment_view(item) for item in attachments],
    }
