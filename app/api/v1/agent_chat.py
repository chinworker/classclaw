from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import ThinkingLevel
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.schemas.agent_chat import ClassAgentModelUpdate
from app.services import admin_console, agent_chat, agent_models, agent_thinking, ai_tasks

router = APIRouter(tags=["班级 Agent 网页对话"])


@router.get("/classes/{class_id}/agent-chat/thinking")
async def class_agent_thinking(request: Request, class_id: str, db: Annotated[Session, Depends(get_db)]):
    require_owned_class(request, class_id)
    response = ok(request, await agent_thinking.class_thinking_options(db, class_id))
    response.headers["Cache-Control"] = "no-store"
    return response


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
    thinking_level: Annotated[ThinkingLevel | None, Form()] = None,
    stream: Annotated[bool, Form()] = False,
):
    require_owned_class(request, class_id)
    uploads = files or []
    if uploads:
        admin_console.require_feature("feature.file_analysis")
    principal = principal_from_request(request)
    sender_id = principal.user_id or principal.username

    async def run(on_delta=None):
        with agent_chat.conversation_turn(class_id, sender_id, conversation_id):
            # StreamingResponse owns ASGI disconnect events. Its cancellation
            # closes the producer; do not have two consumers race on receive().
            async with ai_tasks.track(request, check_disconnect=not stream) as cancelled:
                return await agent_chat.send_message(
                    db, class_id=class_id, conversation_id=conversation_id, text=text, uploads=uploads,
                    sender_id=sender_id, requested_by=principal.username, cancelled=cancelled,
                    thinking_level=thinking_level, on_delta=on_delta,
                )

    if not stream:
        return ok(request, await run(), "班级 Agent 已回复")

    request_id = request.state.request_id

    def frame(event, data=None, error=None):
        payload = {"success": error is None, "request_id": request_id}
        if error is not None:
            payload["error"] = {"code": error.code, "message": error.message, "details": error.details}
        else:
            payload.update(data=data, message="班级 Agent 已回复" if event == "done" else "")
        return f"event: {event}\ndata: {json.dumps(jsonable_encoder(payload), ensure_ascii=False)}\n\n"

    async def events():
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        started = time.monotonic()
        producer = asyncio.create_task(run(queue.put))
        next_delta = None
        outcome = "interrupted"
        try:
            yield ": connected\n\n"
            while True:
                next_delta = asyncio.create_task(queue.get())
                completed, _ = await asyncio.wait({producer, next_delta}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
                if next_delta in completed:
                    yield frame("delta", {"text": next_delta.result()})
                else:
                    next_delta.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_delta
                next_delta = None
                if producer.done():
                    while not queue.empty():
                        yield frame("delta", {"text": queue.get_nowait()})
                    result = producer.result()
                    outcome = "completed"
                    yield frame("done", result)
                    break
                if not completed:
                    yield ": keepalive\n\n"
        except AppError as exc:
            outcome = exc.code
            yield frame("error", error=exc)
        except Exception as exc:
            outcome = "failed"
            get_logger("agent_chat").error("Chat stream failed: %s", type(exc).__name__, extra={"request_id": request_id})
            yield frame("error", error=AppError("INTERNAL_ERROR", "回复处理失败；涉及写入时请先核对结果再重试", 500))
        finally:
            if next_delta is not None:
                next_delta.cancel()
                with suppress(asyncio.CancelledError):
                    await next_delta
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await producer
            get_logger("agent_chat").info(
                "Chat request outcome=%s", outcome,
                extra={"request_id": request_id, "duration_ms": round((time.monotonic() - started) * 1000, 2)},
            )

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
