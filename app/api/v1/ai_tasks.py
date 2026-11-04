from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.responses import ok
from app.core.security import require_cancel_authentication
from app.services import ai_tasks

router = APIRouter(tags=["AI 任务"])


@router.post("/ai/tasks/{task_id}/cancel", dependencies=[Depends(require_cancel_authentication)])
async def ai_task_cancel(request: Request, task_id: str):
    return ok(request, ai_tasks.cancel(request, task_id), "AI 任务取消请求已接收")
