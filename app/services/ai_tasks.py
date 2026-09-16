from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Request

from app.core.errors import AppError
from app.core.security import principal_from_request

AI_TASK_ID_HEADER = "X-ClassClaw-AI-Task-ID"
_PENDING_TTL_SECONDS = 60.0


@dataclass(slots=True)
class _ActiveTask:
    owner: str
    event: asyncio.Event
    class_id: str | None = None


_active_tasks: dict[str, _ActiveTask] = {}
_pending_cancellations: dict[str, tuple[str, float]] = {}


def _owner(request: Request) -> str:
    principal = principal_from_request(request)
    if principal.user_id:
        return f"user:{principal.user_id}"
    return f"service:{principal.username}"


def _task_id(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise AppError("VALIDATION_ERROR", "AI 任务标识无效", 422) from exc


def _prune_pending(now: float) -> None:
    expired = [task_id for task_id, (_, created_at) in _pending_cancellations.items() if now - created_at >= _PENDING_TTL_SECONDS]
    for task_id in expired:
        _pending_cancellations.pop(task_id, None)


def cancel(request: Request, task_id: str) -> dict[str, object]:
    """Signal one browser-owned AI task without waiting for its HTTP request to disconnect."""
    normalized = _task_id(task_id)
    assert normalized is not None
    owner = _owner(request)
    checked_at = time.monotonic()
    _prune_pending(checked_at)
    active = _active_tasks.get(normalized)
    if active:
        if active.owner != owner:
            raise AppError("AI_TASK_NOT_FOUND", "未找到可取消的 AI 任务", 404)
        active.event.set()
        return {"task_id": normalized, "cancelled": True, "state": "running"}
    # The browser can send the cancellation request just before the original
    # upload/request reaches its route handler. Remember that race briefly.
    _pending_cancellations[normalized] = (owner, checked_at)
    return {"task_id": normalized, "cancelled": True, "state": "pending"}


@asynccontextmanager
async def track(request: Request, *, class_id: str | None = None, check_disconnect: bool = True) -> AsyncIterator[Callable[[], Awaitable[bool]]]:
    """Register an AI request and expose a cancellation callback to OpenClaw calls."""
    # Browser cancellation IDs are optional; deletion still needs every run.
    normalized = _task_id(request.headers.get(AI_TASK_ID_HEADER)) or str(uuid.uuid4())

    owner = _owner(request)
    checked_at = time.monotonic()
    _prune_pending(checked_at)
    previous = _active_tasks.get(normalized)
    if previous:
        raise AppError("AI_TASK_CONFLICT", "AI 任务标识已被占用，请等待原任务结束", 409)
    entry = _ActiveTask(owner=owner, event=asyncio.Event(), class_id=class_id)
    pending = _pending_cancellations.pop(normalized, None)
    if pending and pending[0] == owner:
        entry.event.set()
    _active_tasks[normalized] = entry

    async def cancelled() -> bool:
        return entry.event.is_set() or (check_disconnect and await request.is_disconnected())

    try:
        yield cancelled
    finally:
        if _active_tasks.get(normalized) is entry:
            _active_tasks.pop(normalized, None)


def has_active(*, class_id: str | None = None, user_id: str | None = None) -> bool:
    """Report whether a tracked AI request still owns the given class or account.

    Deletion uses this to refuse destroying resources an in-flight request may
    still write to. Without a scope there is nothing to match, so it is False.
    """
    if class_id is None and user_id is None:
        return False
    owner = f"user:{user_id}" if user_id else None
    for task in _active_tasks.values():
        if class_id is not None and task.class_id != class_id:
            continue
        if owner is not None and task.owner != owner:
            continue
        return True
    return False


def reset_for_tests() -> None:
    _active_tasks.clear()
    _pending_cancellations.clear()
