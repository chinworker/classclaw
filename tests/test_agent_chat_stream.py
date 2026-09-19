from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.config import ConfigurationError, load_settings, settings
from app.core.errors import AppError
from app.models.entities import ClassAgentBinding, InteractionAnalysis
from app.schemas.domain import InteractionAnalyzeCreate
from app.services import agent_chat, agent_stream, interactions, openclaw_bridge, openclaw_provisioning

CONVERSATION = "97d646a7-153d-4370-aa17-bc49d3ba5593"


def sse(value):
    return f"data: {json.dumps(value, ensure_ascii=False)}\r\n\r\n".encode()


def final(text="完整回复"):
    return {"id": "response-1", "status": "completed", "output": [{"content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_route_delivers_deltas_final_metadata_and_private_thinking(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = ClassAgentBinding(class_id=cls.id, openclaw_agent_id="class-agent", agent_name="班级助手", workspace_path=str(tmp_path))
    db.add(binding)
    db.commit()
    captured = {}

    async def runtime(*args):
        return binding

    async def chat(**kwargs):
        captured.update(kwargs)
        await kwargs["on_thinking"]({"state": "started", "level": "high"})
        await kwargs["on_thinking"]({"state": "delta", "text": "思考内容"})
        await kwargs["on_delta"]("第一段")
        await kwargs["on_delta"]("第二段")
        return {"reply": "第一段第二段", "response_id": "response-1"}

    monkeypatch.setattr(openclaw_provisioning, "ensure_class_agent_runtime", runtime)
    monkeypatch.setattr(openclaw_bridge, "chat_with_class_agent", chat)
    response = client.post(f"/api/v1/classes/{cls.id}/agent-chat/messages", data={
        "conversation_id": CONVERSATION, "text": "测试", "stream": "true", "thinking_level": "high",
    })
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = frames(response)
    assert events[0]["data"] == {"state": "started", "level": "high"}
    assert events[1]["data"] == {"state": "delta", "text": "思考内容"}
    assert [e["data"].get("text") for e in events[2:-1]] == ["第一段", "第二段"]
    assert events[-1]["data"]["reply"] == "第一段第二段"
    assert events[-1]["data"]["conversation_id"] == CONVERSATION
    assert all(e["request_id"] == response.headers["x-request-id"] for e in events)
    assert captured["thinking_level"] == "high"
    assert not agent_chat._active_conversations


def test_stream_failure_after_delta_is_not_a_successful_answer(client, sample, monkeypatch):
    async def send(*args, **kwargs):
        await kwargs["on_delta"]("部分回复")
        raise AppError("OPENCLAW_TIMEOUT", "请核对结果", 504)

    monkeypatch.setattr(agent_chat, "send_message", send)
    response = client.post(f"/api/v1/classes/{sample[0].id}/agent-chat/messages", data={
        "conversation_id": CONVERSATION, "text": "测试", "stream": "true",
    })
    events = frames(response)
    assert events[0]["data"]["text"] == "部分回复"
    assert events[-1]["success"] is False
    assert events[-1]["error"]["code"] == "OPENCLAW_TIMEOUT"
    assert "event: done" not in response.text
    assert not agent_chat._active_conversations


def test_thinking_input_is_validated_before_gateway_and_requires_auth(client, sample, monkeypatch):
    async def should_not_run(*args, **kwargs):
        pytest.fail("Invalid input must not reach Gateway")

    monkeypatch.setattr(agent_chat, "send_message", should_not_run)
    path = f"/api/v1/classes/{sample[0].id}/agent-chat/messages"
    invalid = client.post(path, data={"conversation_id": CONVERSATION, "text": "test", "thinking_level": "arbitrary", "stream": "true"})
    assert invalid.status_code == 422
    denied = client.post(path, data={"conversation_id": CONVERSATION, "text": "test", "stream": "true"}, headers={"Authorization": ""})
    assert denied.status_code == 401


def test_gateway_stream_emits_before_completion_and_never_forwards_reasoning(monkeypatch):
    async def run():
        release = asyncio.Event()
        saw_delta = asyncio.Event()
        closed = []
        deltas = []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({"type": "response.reasoning_text.delta", "delta": "private thinking"})
                chunk = sse({"type": "response.output_text.delta", "delta": "你好"})
                for byte in chunk:
                    yield bytes([byte])
                await release.wait()
                yield sse({"type": "response.completed", "response": final()})

            async def aclose(self):
                closed.append(True)

        async def delta(text):
            deltas.append(text)
            saw_delta.set()

        async def handle(request):
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(agent_stream, "get_http_client", lambda: client)
            task = asyncio.create_task(agent_stream.request_stream({}, gateway_url="http://gateway.test", headers={}, timeout=2, on_delta=delta, request_id="test"))
            await asyncio.wait_for(saw_delta.wait(), 1)
            assert not task.done()
            assert deltas == ["你好"]
            release.set()
            assert (await task)["usage"]["total_tokens"] == 14
        assert closed == [True]

    asyncio.run(run())


@pytest.mark.parametrize(("tail", "code"), [
    (b"", "OPENCLAW_STREAM_INTERRUPTED"),
    (b"data: [DONE]\n\n", "OPENCLAW_STREAM_INTERRUPTED"),
    (b"data: broken-json\n\n", "OPENCLAW_INVALID_RESPONSE"),
    (sse({"type": "response.failed", "response": {"status": "failed"}}), "OPENCLAW_PROCESSING_FAILED"),
    (sse({"type": "response.incomplete", "response": {"status": "incomplete"}}), "OPENCLAW_PROCESSING_FAILED"),
])
def test_gateway_truncated_or_failed_stream_does_not_succeed(monkeypatch, tail, code):
    async def run():
        async def delta(_text):
            pass

        transport = httpx.MockTransport(lambda _r: httpx.Response(200, headers={"content-type": "text/event-stream"},
            content=sse({"type": "response.output_text.delta", "delta": "部分"}) + tail))
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(agent_stream, "get_http_client", lambda: client)
            with pytest.raises(AppError) as caught:
                await agent_stream.request_stream({}, gateway_url="http://gateway.test", headers={}, timeout=1, on_delta=delta, request_id=None)
            assert caught.value.code == code

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [True, False])
def test_stream_cancellation_and_absolute_timeout_close_upstream(monkeypatch, cancel):
    async def run():
        entered = asyncio.Event()
        closed = asyncio.Event()

        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                closed.set()

        async def delta(_text):
            pass

        transport = httpx.MockTransport(lambda _r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Waiting()))
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(agent_stream, "get_http_client", lambda: client)
            task = asyncio.create_task(agent_stream.request_stream({}, gateway_url="http://gateway.test", headers={}, timeout=.05, on_delta=delta, request_id=None))
            await entered.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(AppError) as caught:
                    await task
                assert caught.value.code == "OPENCLAW_TIMEOUT"
            assert closed.is_set()

    asyncio.run(run())


def test_session_thinking_patches_only_the_session_key(monkeypatch):
    calls = []

    class Client:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(openclaw_provisioning, "get_http_client", lambda: Client())
    asyncio.run(openclaw_provisioning.set_web_session_thinking("agent:a:openresponses-user:owner:conversation-a", "high"))
    assert calls == [(f"{settings.openclaw_gateway_url}/api/v1/classclaw/web-session-thinking", {
        "key": "agent:a:openresponses-user:owner:conversation-a", "thinkingLevel": "high",
    })]


def test_default_thinking_sync_changes_only_bound_agents_and_not_session_overrides(db, sample, monkeypatch):
    from contextlib import contextmanager

    binding = ClassAgentBinding(class_id=sample[0].id, openclaw_agent_id="bound-agent", agent_name="测试", workspace_path="/tmp/unused")
    db.add(binding)
    db.commit()

    @contextmanager
    def reader():
        yield db

    calls = []
    agents = [{"id": "bound-agent", "model": "provider/model", "thinkingDefault": "off"},
              {"id": "classclaw-extractor", "thinkingDefault": "off"}, {"id": "unrelated", "thinkingDefault": "medium"}]

    async def rpc(method, params=None):
        calls.append((method, params))
        return {"hash": "hash-1", "config": {"agents": {"list": agents}}} if method == "config.get" else {}

    monkeypatch.setattr(openclaw_provisioning, "reader_session", reader)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    monkeypatch.setattr(openclaw_provisioning, "settings", replace(settings, openclaw_class_agent_thinking="high"))
    asyncio.run(openclaw_provisioning.sync_class_agent_thinking_defaults())
    assert [call[0] for call in calls] == ["config.get", "config.patch"]
    patch = json.loads(calls[1][1]["raw"])
    assert patch["agents"]["list"][0] == {**agents[0], "thinkingDefault": "high", "tools": {"loopDetection": openclaw_provisioning._CHAT_LOOP_DETECTION}}
    assert patch["agents"]["list"][1:] == agents[1:]
    assert calls[1][1]["baseHash"] == "hash-1"


def test_same_conversation_runs_are_exclusive_but_other_owners_are_independent():
    with agent_chat.conversation_turn("class", "owner-a", CONVERSATION):
        with pytest.raises(AppError, match="本对话正在回复"), agent_chat.conversation_turn("class", "owner-a", CONVERSATION):
            pass
        with agent_chat.conversation_turn("class", "owner-b", CONVERSATION):
            pass
    assert not agent_chat._active_conversations


def test_configurable_default_has_env_precedence_and_safe_browser_projection(client, tmp_path, monkeypatch):
    config = tmp_path / "settings.toml"
    config.write_text('[openclaw]\nclass_agent_thinking = "medium"\n', encoding="utf-8")
    assert load_settings(config, environ={}).openclaw_class_agent_thinking == "medium"
    loaded = load_settings(config, environ={"CLASSCLAW_OPENCLAW_CLASS_AGENT_THINKING": "high"})
    assert loaded.openclaw_class_agent_thinking == "high"
    with pytest.raises(ConfigurationError):
        load_settings(config, environ={"CLASSCLAW_OPENCLAW_CLASS_AGENT_THINKING": "unknown"})
    monkeypatch.setattr("app.api.v1.configuration.settings", replace(settings, openclaw_class_agent_thinking="high"))
    assert client.get("/api/v1/app-config").json()["data"]["agent_chat"]["default_thinking_level"] == "high"


@pytest.mark.parametrize("status", ["failed", "analyzing"])
def test_analysis_does_not_treat_failed_or_in_progress_idempotency_records_as_success(db, sample, status):
    row = InteractionAnalysis(class_id=sample[0].id, channel="web", input_kind="natural_language", status=status,
                              idempotency_key="web:one-message", attachment_ids_json=[])
    db.add(row)
    db.commit()
    with pytest.raises(AppError) as caught:
        asyncio.run(interactions.analyze(db, InteractionAnalyzeCreate(class_id=sample[0].id, channel="web", external_message_id="one-message", text="重试")))
    assert caught.value.code == ("INTERACTION_ANALYSIS_FAILED" if status == "failed" else "INTERACTION_ANALYSIS_IN_PROGRESS")
