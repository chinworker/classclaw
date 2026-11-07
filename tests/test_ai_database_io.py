from __future__ import annotations

import asyncio
import sys
import threading
import uuid
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
from app.main import app as application
from app.main import app_error_handler
from app.models.entities import AuditLog, ClassAgentBinding, ClassOnboardingSession, ClassRoom, DeletionOperation, InteractionAnalysis
from app.schemas.auth import UserCreate
from app.services import accounts, admin_console, agent_models, agent_thinking, ai_tasks, openclaw_bridge, openclaw_provisioning


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


@pytest.mark.parametrize(("path", "body", "service", "action"), [
    ("/admin/openclaw/config", {"responses_enabled": True}, admin_console, "gateway_config_patch"),
    ("/admin/openclaw/config/raw", {"patch": {"gateway.http.endpoints.responses.enabled": True}, "base_hash": "old"},
     admin_console, "gateway_config_patch"),
    ("/classes/{class_id}/agent-chat/models", {"main_model": "test/new"}, agent_models, "update_agent_models"),
    ("/classes/{class_id}/agent-chat/thinking", {"thinking_default": "low"}, agent_thinking, "update_agent_thinking"),
])
def test_gateway_config_wait_allows_writes_then_audits_with_writer(managed_database, monkeypatch, path, body, service, action):
    from app.config import settings

    async def run():
        patch_started = asyncio.Event()
        gateway_ready = asyncio.Event()
        real_audit = service.audit

        async def rpc(method, _params=None):
            assert not database._write_lock.locked()
            if method == "config.get":
                return {"hash": "old", "config": {"agents": {"list": [{"id": "test-agent", "model": "test/old"}]}}}
            if method == "models.list":
                return {"models": [{"id": "new", "provider": "test"}]}
            if method == "models.authStatus":
                return {"providers": []}
            if method == "agents.list":
                return {"agents": [{"id": "test-agent", "thinkingLevels": [{"id": "low"}]}]}
            assert method == "config.patch"
            patch_started.set()
            await gateway_ready.wait()
            return {"hash": "new"}

        def audit(db, *args, **kwargs):
            assert not db.info.get("read_only")
            assert database._write_lock.locked()
            return real_audit(db, *args, **kwargs)

        def concurrent_write():
            with database.writer_session() as db:
                db.get(ClassRoom, managed_database).name = "网关等待期间修改"
                db.commit()

        monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
        monkeypatch.setattr(service, "audit", audit)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test",
                                     headers={"Authorization": f"Bearer {settings.api_token}"}) as client:
            pending = asyncio.create_task(client.patch("/api/v1" + path.format(class_id=managed_database), json=body))
            try:
                await asyncio.wait_for(patch_started.wait(), timeout=2)
                await asyncio.wait_for(asyncio.to_thread(concurrent_write), timeout=2)
            finally:
                gateway_ready.set()
                result = await asyncio.wait_for(pending, timeout=2)
        assert result.status_code == 200, result.text
        with database.reader_session() as db:
            assert db.get(ClassRoom, managed_database).name == "网关等待期间修改"
            row = db.scalars(select(AuditLog).where(AuditLog.action == action)).one()
            if action == "gateway_config_patch":
                assert row.after_json == {"paths": ["gateway.http.endpoints.responses.enabled"],
                                          "old_config_hash": "old", "new_config_hash": "new"}
            else:
                binding = db.scalars(select(ClassAgentBinding).where(ClassAgentBinding.class_id == managed_database)).one()
                assert row.entity_id == binding.id
                assert all(getattr(binding, name) == value for name, value in body.items())

    asyncio.run(run())


def test_task_cancellation_needs_no_writer_and_survives_pending_account_deletion(managed_database):
    with database.writer_session() as db:
        user = accounts.create_teacher(db, UserCreate(username="cancelling-user"))
        token, _ = accounts.issue_session(db, user)
        db.add(DeletionOperation(target_type="user", target_id=user.id, phase="business", status="failed"))
        db.commit()
        user_id = user.id

    async def run():
        from app.core.security import Principal

        task_id = str(uuid.uuid4())
        request = Request({"type": "http", "headers": [(ai_tasks.AI_TASK_ID_HEADER.lower().encode(), task_id.encode())]})
        request.state.principal = Principal(user_id, "cancelling-user", "head_teacher", None)
        async with ai_tasks.track(request, check_disconnect=False) as cancelled:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
                url = f"/api/v1/ai/tasks/{task_id}/cancel"
                assert (await client.post(url)).status_code == 401
                assert not await cancelled()
                with database.writer_session():
                    response = await asyncio.wait_for(client.post(url, headers={"Authorization": f"Bearer {token}"}), timeout=2)
                    assert response.status_code == 200, response.text
                    assert response.json()["data"]["state"] == "running"
                    assert await cancelled()

    asyncio.run(run())


def test_session_cleanup_does_not_hold_writer_and_still_requires_admin(managed_database, monkeypatch):
    from app.config import settings

    with database.writer_session() as db:
        user = accounts.create_teacher(db, UserCreate(username="cleanup-teacher"))
        token, _ = accounts.issue_session(db, user)
        db.commit()

    async def cleanup(*, enforce):
        assert enforce is False
        # A writer already held by another operation must not block this request.
        assert database._write_lock.locked()
        return {"ok": True, "enforce": enforce}

    async def run():
        monkeypatch.setattr(openclaw_bridge, "run_session_cleanup", cleanup)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            url = "/api/v1/admin/openclaw/sessions/cleanup?enforce=false"
            assert (await client.post(url, headers={"Authorization": f"Bearer {token}"})).status_code == 403
            with database.writer_session():
                response = await asyncio.wait_for(
                    client.post(url, headers={"Authorization": f"Bearer {settings.api_token}"}), timeout=2,
                )
                assert response.status_code == 200, response.text
                assert response.json()["data"] == {"ok": True, "enforce": False}

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

    async def runtime(method, *_args, **_kwargs):
        if method == "agents.list":
            assert not database._write_lock.locked()
            return {"agents": [{"id": "test-agent", "thinkingLevels": [{"id": "off", "label": "off"}], "thinkingDefault": "off"}]}
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


def test_file_analysis_rejects_draft_changed_during_model_call(managed_database, monkeypatch, tmp_path):
    with database.request_writer_session() as db:
        draft = ClassOnboardingSession(status="draft", current_step="class_info", revision=1,
            draft_json={}, field_evidence_json={}, warnings_json=[])
        db.add(draft)
        db.commit()
        draft_id = draft.id

    source = tmp_path / "class.csv"
    source.write_text("班名,年级\n高一一班,高一", encoding="utf-8")
    attachment = SimpleNamespace(id="test-file", original_name=source.name, mime_type="text/csv")
    request_thread = threading.get_ident()
    original_input_part = openclaw_bridge._input_part

    def input_part(*args):
        assert threading.get_ident() != request_thread
        assert not database._write_lock.locked()
        return original_input_part(*args)

    async def connected():
        return {"gateway_live": True, "plugin_ready": True}

    async def extractor_off():
        return False

    class Gateway:
        async def post(self, _url, *, json, **_kwargs):
            assert json["input"][0]["content"][1]["source"]["filename"] == source.name
            with database.request_writer_session() as other:
                changed = other.get(ClassOnboardingSession, draft_id)
                changed.revision = 2
                changed.draft_json = {"class_info": {"name": "人工修改", "grade": "高一"}}
                other.commit()
            return httpx.Response(200, json={"output_text": '{"draft_patch":{"class_info":{"name":"AI修改","grade":"高一"}},"confidence":1,"reasons":["信息完整"]}'})

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Gateway())
    monkeypatch.setattr(openclaw_bridge, "_attachment_path", lambda _item: source)
    monkeypatch.setattr(openclaw_bridge, "_input_part", input_part)

    async def run():
        with database.request_writer_session() as db:
            with pytest.raises(AppError) as caught:
                await openclaw_bridge.analyze_onboarding_files(db, draft_id, "class_info", [attachment], 1)
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

    async def rpc(method, *args, **kwargs):
        if method == "agents.list":
            assert not database._write_lock.locked()
            return {"agents": [{"id": "test-agent", "thinkingLevels": [{"id": "off", "label": "off"}], "thinkingDefault": "off"}]}
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

    async def rpc(_class_id, action, **_params):
        assert action == "wait"
        with database.request_writer_session() as other:
            other.get(ClassRoom, managed_database).name = "扫码期间可保存"
            other.commit()
        return {"connected": False, "qrDataUrl": "weixin://test-login", "loginId": "22222222-2222-2222-2222-222222222222"}

    monkeypatch.setattr("app.services.wechat_login.call", rpc)

    async def run():
        with database.request_writer_session() as db:
            result = await openclaw_provisioning.wait_wechat_binding(db, managed_database, login_id="22222222-2222-2222-2222-222222222222")
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
