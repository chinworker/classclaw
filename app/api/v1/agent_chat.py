from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.services import admin_console, agent_chat, ai_tasks

router = APIRouter(tags=["班级 Agent 网页对话"])


@router.post("/classes/{class_id}/agent-chat/messages")
async def class_agent_chat_message(
    request: Request,
    class_id: str,
    conversation_id: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    text: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    require_owned_class(request, class_id)
    uploads = files or []
    if uploads:
        admin_console.require_feature("feature.file_analysis")
    principal = principal_from_request(request)
    async with ai_tasks.track(request) as cancelled:
        result = await agent_chat.send_message(
            db,
            class_id=class_id,
            conversation_id=conversation_id,
            text=text,
            uploads=uploads,
            sender_id=principal.user_id or principal.username,
            requested_by=principal.username,
            cancelled=cancelled,
        )
    return ok(request, result, "班级 Agent 已回复")
