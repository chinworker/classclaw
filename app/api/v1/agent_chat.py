from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.schemas.agent_chat import ClassAgentModelUpdate
from app.services import admin_console, agent_chat, agent_models, ai_tasks

router = APIRouter(tags=["班级 Agent 网页对话"])


@router.get("/classes/{class_id}/agent-chat/models")
async def class_agent_models(request: Request, class_id: str, db: Annotated[Session, Depends(get_db)]):
    require_owned_class(request, class_id)
    return ok(request, await agent_models.class_agent_model_settings(db, class_id))


@router.patch("/classes/{class_id}/agent-chat/models")
async def class_agent_models_update(
    request: Request,
    class_id: str,
    body: ClassAgentModelUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    result = await agent_models.update_class_agent_models(db, class_id, body, operator_id=principal.user_id)
    return ok(request, result, "班级 Agent 模型配置已更新")


@router.post("/classes/{class_id}/agent-chat/transcriptions")
async def class_agent_transcription(
    request: Request,
    class_id: str,
    audio: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
):
    require_owned_class(request, class_id)
    async with ai_tasks.track(request) as cancelled:
        result = await agent_models.transcribe_for_class(db, class_id, audio, cancelled=cancelled)
    return ok(request, result, "语音已转成文字")


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
