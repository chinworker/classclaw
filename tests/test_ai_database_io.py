from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app import database
from app.api.v1 import agent_chat, interactions
from app.core.errors import AppError
from app.core.security import require_authenticated
from app.main import app_error_handler
from app.models.entities import ClassAgentBinding, ClassOnboardingSession, ClassRoom, InteractionAnalysis
from app.services import agent_models, openclaw_bridge, openclaw_provisioning


@pytest.fixture
def managed_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'isolated.db'}"
    writer = database.build_engine(url, single_connection=True)
    reader = database.build_engine(url)
    database.Base.metadata.create_all(writer)
    monkeypatch.setattr(database, "_write_lock", threading.Lock())
    for name, engine in (("_write_sessionmaker", writer), ("_read_sessionmaker", reader)):
        monkeypatch.setattr(database, name, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    with database.request_writer_session() as db:
        cls = ClassRoom(name="原班名", grade="高一")
        db.add(cls)
        db.flush()
        db.add(ClassAgentBinding(class_id=cls.id, openclaw_agent_id="test-agent", agent_name="测试助手",
                                 workspace_path=str(tmp_path / "workspace"), status="agent_created", speech_model="test/stt"))
        db.commit()
        class_id = cls.id
    yield class_id
    writer.dispose()
    reader.dispose()


def test_io_releases_lock_and_connection_then_refreshes_orm(managed_database):
    async def run():
        with database.request_writer_session() as db:
            cls = db.get(ClassRoom, managed_database)
            assert cls.name == "原班名"
            async with database.suspend_writer(db):
                # This is the same one-connection pool as the outer request.
                with database.request_writer_session() as callback_db:
                    callback_db.get(ClassRoom, managed_database).name = "已修改"
                    callback_db.commit()
            assert cls.name == "已修改"
            assert database._write_lock.locked()
        assert not database._write_lock.locked()
    asyncio.run(run())


@pytest.mark.parametrize("state", ["pending", "flushed", "bulk"])
def test_io_never_implicitly_commits_pending_writes(managed_database, state):
    async def run():
        with database.request_writer_session() as db:
            if state == "bulk":
                db.execute(update(ClassRoom).where(ClassRoom.id == managed_database).values(name="不应提交"))
            else:
                db.get(ClassRoom, managed_database).name = "不应提交"
                if state == "flushed":
                    db.flush()
            with pytest.raises(RuntimeError, match="pending writes"):
                async with database.suspend_writer(db):
                    pytest.fail("Must not enter I/O with uncommitted writes")
            db.rollback()
            assert db.get(ClassRoom, managed_database).name == "原班名"
    asyncio.run(run())


def test_io_forbids_database_access_while_unlocked(managed_database):
    async def run():
        with database.request_writer_session() as db:
            async with database.suspend_writer(db):
                with pytest.raises(RuntimeError, match="Database access is forbidden"):
                    db.scalar(select(ClassRoom))
            assert db.get(ClassRoom, managed_database)
    asyncio.run(run())


def test_io_cancellation_reacquires_without_blocking_event_loop(managed_database):
    async def run():
        entered = asyncio.Event()
        callback_entered = threading.Event()
        callback_release = threading.Event()

        async def request():
            with database.request_writer_session() as db:
                async with database.suspend_writer(db):
                    entered.set()
                    await asyncio.Event().wait()

        def callback():
            with database.request_writer_session():
                callback_entered.set()
                assert callback_release.wait(2)

        task = asyncio.create_task(request())
        await entered.wait()
        worker = asyncio.create_task(asyncio.to_thread(callback))
        assert await asyncio.to_thread(callback_entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.03)
        task.cancel()  # Also cover cancellation while reacquiring the lock.
        assert not task.done()
        callback_release.set()
        await worker
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not database._write_lock.locked()
    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_web_chat_allows_nested_agent_analysis_and_other_writes(managed_database, monkeypatch, streaming):
    """Use real routing, auth and get_db, not the normal overridden DB fixture."""
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.middleware("http")
    async def context(request: Request, call_next):
        request.state.request_id = "test-ai-callback"
        return await call_next(request)

    for router in (agent_chat.router, interactions.router):
        app.include_router(router, prefix="/api/v1", dependencies=[Depends(require_authenticated)])

    @app.post("/probe/write")
    def write(db: Annotated[Session, Depends(database.get_db)]):
        db.get(ClassRoom, managed_database).name = "回调已写入"
        db.commit()
        return {"ok": True}

    async def connected(*_args, **_kwargs):
        return {"gateway_live": True, "plugin_ready": True}

    async def runtime(*_args, **_kwargs):
        return {"config": {}}

    async def extractor_off():
        return False

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", runtime)
    monkeypatch.setattr(openclaw_provisioning, "set_web_session_thinking", runtime)
    monkeypatch.setattr(openclaw_provisioning, "_runtime_is_configured", lambda *_args: True)
    monkeypatch.setattr(openclaw_provisioning, "_prepare_workspace", lambda *_args: None)

    async def run():
        pending = []
        from app.config import settings

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": f"Bearer {settings.api_token}"}) as client:
            class Gateway:
                async def post(self, _url, *, json, **_kwargs):
                    chat = json["user"].startswith("classclaw-web-chat:")
                    operation = client.post("/api/v1/interaction-analyses", json={
                        "channel": "web", "external_message_id": "isolated-message", "class_id": managed_database, "text": "测试",
                    }) if chat else client.post("/probe/write")
                    callback = asyncio.create_task(operation)
                    pending.append(callback)
                    done, _ = await asyncio.wait({callback}, timeout=1)
                    if not done:
                        raise httpx.ReadTimeout("Agent callback is blocked by the outer writer")
                    assert callback.result().status_code in {200, 201}
                    text = "已完成" if chat else '{"status":"no_action","operations":[],"confidence":1,"reasons":["无需写入"]}'
                    return httpx.Response(200, json={"id": "resp-test", "output": [{"content": [{"type": "output_text", "text": text}]}]})

            monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Gateway())
            async def streaming_gateway(body, *, on_delta, **_kwargs):
                await on_delta("处理中")
                return (await Gateway().post("", json=body)).json()

            monkeypatch.setattr(openclaw_bridge, "request_stream", streaming_gateway)
            try:
                response = await client.post(f"/api/v1/classes/{managed_database}/agent-chat/messages", data={
                    "conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "测试", "stream": str(streaming).lower(),
                })
                assert response.status_code == 200, response.text
                if streaming:
                    assert "event: delta" in response.text and "event: done" in response.text
                    assert '"reply": "已完成"' in response.text
                else:
                    assert response.json()["data"]["reply"] == "已完成"
            finally:
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        with database.reader_session() as db:
            assert db.get(ClassRoom, managed_database).name == "回调已写入"
            assert db.scalar(select(InteractionAnalysis)).status == "no_action"
        assert not database._write_lock.locked()
    asyncio.run(run())


def test_file_analysis_rejects_draft_changed_during_model_call(managed_database, monkeypatch):
    with database.request_writer_session() as db:
        draft = ClassOnboardingSession(status="draft", current_step="class_info", revision=1,
            draft_json={}, field_evidence_json={}, warnings_json=[])
        db.add(draft)
        db.commit()
        draft_id = draft.id

    async def connected():
        return {"gateway_live": True, "plugin_ready": True}

    async def extractor_off():
        return False

    class Gateway:
        async def post(self, *_args, **_kwargs):
            with database.request_writer_session() as other:
                changed = other.get(ClassOnboardingSession, draft_id)
                changed.revision = 2
                changed.draft_json = {"class_info": {"name": "人工修改", "grade": "高一"}}
                other.commit()
            return httpx.Response(200, json={"output_text": '{"draft_patch":{"class_info":{"name":"AI修改","grade":"高一"}},"confidence":1,"reasons":["信息完整"]}'})

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Gateway())

    async def run():
        with database.request_writer_session() as db:
            with pytest.raises(AppError) as caught:
                await openclaw_bridge._analyze_onboarding(db, draft_id, "class_info", 1, raw_text="测试")
            assert caught.value.code == "PENDING_CONFIRMATION_REQUIRED"
            assert db.get(ClassOnboardingSession, draft_id).draft_json["class_info"]["name"] == "人工修改"
    asyncio.run(run())


def test_stream_disconnect_closes_run_and_releases_writer(managed_database, monkeypatch):
    """Exercise ASGI disconnect (not a buffered TestClient response) with real leases."""
    from urllib.parse import urlencode

    from app.config import settings
    from app.services.agent_chat import _active_conversations

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(agent_chat.router, prefix="/api/v1", dependencies=[Depends(require_authenticated)])

    async def connected(*args, **kwargs):
        return {"gateway_live": True, "plugin_ready": True}

    async def rpc(*args, **kwargs):
        return {"config": {}}

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    monkeypatch.setattr(openclaw_provisioning, "set_web_session_thinking", rpc)
    monkeypatch.setattr(openclaw_provisioning, "_runtime_is_configured", lambda *_args: True)
    monkeypatch.setattr(openclaw_provisioning, "_prepare_workspace", lambda *_args: None)

    async def run():
        disconnected = asyncio.Event()
        saw_delta = asyncio.Event()
        upstream_closed = asyncio.Event()

        async def streaming_gateway(_body, *, on_delta, **_kwargs):
            try:
                assert not database._write_lock.locked()
                await on_delta("部分回复")
                await asyncio.Event().wait()
            finally:
                upstream_closed.set()

        monkeypatch.setattr(openclaw_bridge, "request_stream", streaming_gateway)
        body = urlencode({"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "测试", "stream": "true"}).encode()
        delivered_request = False

        async def receive():
            nonlocal delivered_request
            if not delivered_request:
                delivered_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and b"event: delta" in message.get("body", b""):
                saw_delta.set()

        path = f"/api/v1/classes/{managed_database}/agent-chat/messages"
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "server": ("test", 80), "client": ("127.0.0.1", 1234), "state": {"request_id": "stream-disconnect"},
                 "headers": [(b"authorization", f"Bearer {settings.api_token}".encode()),
                             (b"content-type", b"application/x-www-form-urlencoded"), (b"content-length", str(len(body)).encode())]}
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(saw_delta.wait(), 2)
            assert not database._write_lock.locked()
            disconnected.set()
            await asyncio.wait_for(task, 2)
            assert upstream_closed.is_set()
            assert not database._write_lock.locked()
            assert not _active_conversations
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_transcription_does_not_hold_writer(managed_database, monkeypatch):
    async def transcribe(*_args, **_kwargs):
        with database.request_writer_session() as other:
            other.get(ClassRoom, managed_database).name = "录音期间可保存"
            other.commit()
        return {"text": "测试", "model": "test/stt"}

    monkeypatch.setattr(agent_models, "transcribe_upload", transcribe)

    async def run():
        with database.request_writer_session() as db:
            result = await agent_models.transcribe_for_class(db, managed_database, SimpleNamespace())
            assert result["text"] == "测试"
            assert db.get(ClassRoom, managed_database).name == "录音期间可保存"
    asyncio.run(run())


def test_wechat_poll_does_not_hold_writer(managed_database, monkeypatch):
    with database.request_writer_session() as db:
        binding = db.scalar(select(ClassAgentBinding))
        binding.status = "awaiting_qr"
        binding.channel_account_id = "test-account"
        db.commit()

    async def rpc(method, _params):
        assert method == "web.login.wait"
        with database.request_writer_session() as other:
            other.get(ClassRoom, managed_database).name = "扫码期间可保存"
            other.commit()
        return {"connected": False, "qrDataUrl": "weixin://test-login"}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)

    async def run():
        with database.request_writer_session() as db:
            result = await openclaw_provisioning.wait_wechat_binding(db, managed_database)
            assert result["connected"] is False
            assert db.get(ClassRoom, managed_database).name == "扫码期间可保存"
    asyncio.run(run())


def test_cancelled_transcription_reaps_subprocess():
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        task = asyncio.create_task(agent_models._finish_process(process, timeout_seconds=60))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None
    asyncio.run(run())


@pytest.mark.parametrize(("error", "code", "status"), [
    (httpx.ReadTimeout(""), "OPENCLAW_TIMEOUT", 504),
    (httpx.ConnectError("private-url-and-token"), "OPENCLAW_CONNECTION_FAILED", 502),
])
def test_gateway_transport_errors_are_actionable_and_redacted(monkeypatch, error, code, status):
    class Gateway:
        async def post(self, *_args, **_kwargs):
            raise error

    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Gateway())
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_bridge._request_responses({}, headers={}, label="班级助手"))
    assert caught.value.code == code
    assert caught.value.status_code == status
    assert caught.value.details["error_type"] == type(error).__name__
    assert "private-url-and-token" not in str(caught.value.details)


@pytest.mark.parametrize("body", [b"not-json", b"[]"])
def test_gateway_invalid_payload_is_reported(monkeypatch, body):
    class Gateway:
        async def post(self, *_args, **_kwargs):
            return httpx.Response(200, content=body)

    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Gateway())
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_bridge._request_responses({}, headers={}, label="班级助手"))
    assert caught.value.code == "OPENCLAW_INVALID_RESPONSE"
