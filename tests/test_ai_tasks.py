from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import Request

from app import database
from app.core.errors import AppError
from app.core.security import Principal
from app.services import ai_tasks, openclaw_bridge


def _request(*, task_id: str | None = None, user_id: str = "user-1") -> Request:
    headers = []
    if task_id:
        headers.append((ai_tasks.AI_TASK_ID_HEADER.lower().encode(), task_id.encode()))
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": headers})
    request.state.principal = Principal(user_id, user_id, "head_teacher", "class-1")

    async def connected() -> bool:
        return False

    request.is_disconnected = connected  # type: ignore[method-assign]
    return request


@pytest.fixture(autouse=True)
def _reset_registry():
    ai_tasks.reset_for_tests()
    yield
    ai_tasks.reset_for_tests()


def test_explicit_cancel_stops_running_backend_operation():
    async def scenario() -> None:
        task_id = str(uuid.uuid4())
        original_request = _request(task_id=task_id)
        cancel_request = _request()
        operation_started = asyncio.Event()

        async def slow_operation() -> dict:
            operation_started.set()
            await asyncio.Event().wait()
            return {}

        async with ai_tasks.track(original_request) as cancelled:
            running = asyncio.create_task(openclaw_bridge._await_unless_cancelled(slow_operation(), cancelled))
            await operation_started.wait()
            result = ai_tasks.cancel(cancel_request, task_id)
            assert result["state"] == "running"
            with pytest.raises(AppError) as caught:
                await asyncio.wait_for(running, timeout=1)
            assert caught.value.code == "REQUEST_CANCELLED"

    asyncio.run(scenario())


def test_cancel_before_route_registration_is_not_lost():
    async def scenario() -> None:
        task_id = str(uuid.uuid4())
        request = _request(task_id=task_id)
        result = ai_tasks.cancel(_request(), task_id)
        assert result["state"] == "pending"
        async with ai_tasks.track(request) as cancelled:
            assert await cancelled() is True

    asyncio.run(scenario())


def test_other_user_cannot_cancel_active_task():
    async def scenario() -> None:
        task_id = str(uuid.uuid4())
        async with ai_tasks.track(_request(task_id=task_id, user_id="owner")):
            with pytest.raises(AppError) as caught:
                ai_tasks.cancel(_request(user_id="other"), task_id)
            assert caught.value.code == "AI_TASK_NOT_FOUND"

    asyncio.run(scenario())


def test_cancel_endpoint_accepts_authenticated_web_request(client):
    task_id = str(uuid.uuid4())
    response = client.post(f"/api/v1/ai/tasks/{task_id}/cancel")
    assert response.status_code == 200
    assert response.json()["data"] == {"task_id": task_id, "cancelled": True, "state": "pending"}


def test_cancel_endpoint_uses_read_session_despite_post_method():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ai/tasks/example/cancel",
            "headers": [],
            "route": SimpleNamespace(name="ai_task_cancel"),
        }
    )
    dependency = database.get_db(request)
    session = next(dependency)
    try:
        assert session.bind is database.read_engine
    finally:
        dependency.close()


def test_cancel_endpoint_stops_event_ai_route(client, sample, monkeypatch):
    cls, _, students = sample
    task_id = str(uuid.uuid4())
    started = threading.Event()

    async def analyze(_db, _class_id, _student_id, _event_date, _content, _subject, *, cancelled=None):
        started.set()

        async def slow_operation() -> dict:
            await asyncio.Event().wait()
            return {}

        return await openclaw_bridge._await_unless_cancelled(slow_operation(), cancelled)

    monkeypatch.setattr(openclaw_bridge, "analyze_student_event", analyze)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(
            client.post,
            f"/api/v1/classes/{cls.id}/student-events/analyze",
            json={"student_id": students[0].id, "event_date": "2026-09-03", "content": "今天迟到", "subject": None},
            headers={ai_tasks.AI_TASK_ID_HEADER: task_id},
        )
        assert started.wait(timeout=1)
        cancellation = client.post(f"/api/v1/ai/tasks/{task_id}/cancel")
        response = running.result(timeout=2)

    assert cancellation.status_code == 200
    assert cancellation.json()["data"]["state"] == "running"
    assert response.status_code == 499
    assert response.json()["error"]["code"] == "REQUEST_CANCELLED"
