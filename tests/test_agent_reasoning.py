from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services import agent_reasoning, agent_stream


def sse(value):
    return f"data: {json.dumps(value)}\n\n".encode()


def test_reasoning_subscribes_before_inference_and_matches_exact_response_run(monkeypatch):
    async def run():
        calls, thinking, answers = [], [], []

        async def handle(request):
            calls.append(request.url.path)
            assert request.headers["Authorization"] == "Bearer private-token"
            if request.url.path.endswith("web-chat-reasoning"):
                assert json.loads(request.content) == {"key": "owned-session"}
                data = b"".join(sse(item) for item in [
                    {"type": "ready"},
                    {"type": "thinking", "run_id": "old-run", "text": "old private content"},
                    {"type": "thinking", "run_id": "current-run", "text": "思考内容"},
                    {"type": "tool", "run_id": "current-run", "text": "private tool args"},
                    {"type": "done", "run_id": "current-run"},
                ])
            else:
                assert calls[0].endswith("web-chat-reasoning")
                data = b"".join(sse(item) for item in [
                    {"type": "response.created", "response": {"id": "current-run"}},
                    {"type": "response.output_text.delta", "delta": "正文"},
                    {"type": "response.completed", "response": {"id": "current-run", "status": "completed"}},
                ])
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)

        async def on_thinking(event):
            thinking.append(event)

        async def on_delta(text):
            answers.append(text)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(agent_reasoning, "get_http_client", lambda: client)
            monkeypatch.setattr(agent_stream, "get_http_client", lambda: client)
            headers = {"Authorization": "Bearer private-token"}
            async with agent_reasoning.observe_reasoning(
                gateway_url="http://gateway.test", headers=headers, session_key="owned-session", timeout=1, on_thinking=on_thinking,
            ) as select_run:
                result = await agent_stream.request_stream({}, gateway_url="http://gateway.test", headers=headers, timeout=1,
                                                          on_delta=on_delta, request_id=None, on_response_id=select_run)
        assert result["status"] == "completed"
        assert thinking == [{"state": "delta", "text": "思考内容"}]
        assert answers == ["正文"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing", "connection", "invalid", "no_run"])
def test_reasoning_unavailable_or_unmatched_does_not_fail_or_leak_into_the_answer(monkeypatch, failure):
    async def run():
        async def handle(request):
            if failure == "connection":
                raise httpx.ConnectError("failed")
            data = b"data: invalid\n\n" if failure == "invalid" else sse({"type": "thinking", "run_id": "run-1", "text": "private"})
            return httpx.Response(404 if failure == "missing" else 200, headers={"content-type": "text/event-stream"}, content=data)

        async def unexpected(_event):
            pytest.fail("Unavailable/unmatched reasoning must not be forwarded")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            monkeypatch.setattr(agent_reasoning, "get_http_client", lambda: client)
            async with agent_reasoning.observe_reasoning(
                gateway_url="http://gateway.test", headers={}, session_key="owned", timeout=1, on_thinking=unexpected,
            ) as select_run:
                if failure != "no_run":
                    select_run("run-1")

    asyncio.run(run())


def test_cancellation_closes_reasoning_reader_and_http_stream(monkeypatch):
    async def run():
        listening, closed = asyncio.Event(), asyncio.Event()

        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                listening.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                closed.set()

        async def on_thinking(_event):
            pass

        async def listen():
            async with agent_reasoning.observe_reasoning(
                gateway_url="http://gateway.test", headers={}, session_key="owned", timeout=1, on_thinking=on_thinking,
            ):
                await asyncio.Event().wait()

        transport = httpx.MockTransport(lambda _request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Waiting()))
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(agent_reasoning, "get_http_client", lambda: client)
            task = asyncio.create_task(listen())
            await listening.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set()

    asyncio.run(run())


def test_disabled_reasoning_never_opens_a_subscription(monkeypatch):
    async def run():
        monkeypatch.setattr(agent_reasoning, "get_http_client", lambda: pytest.fail("Thinking off must not subscribe"))
        async with agent_reasoning.observe_reasoning(gateway_url="http://gateway.test", headers={}, session_key="owned", timeout=1,
                                                    on_thinking=None) as select_run:
            select_run("run-1")

    asyncio.run(run())


def test_cancellation_while_draining_preview_is_not_swallowed_as_success(monkeypatch):
    async def run():
        draining = asyncio.Event()

        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                draining.set()
                await asyncio.Event().wait()
                yield b""

        async def on_thinking(_event):
            pass

        async def answer():
            async with agent_reasoning.observe_reasoning(
                gateway_url="http://gateway.test", headers={}, session_key="owned", timeout=1, on_thinking=on_thinking,
            ) as select_run:
                select_run("run-1")
            pytest.fail("Cancellation during preview cleanup must still cancel the answer")

        transport = httpx.MockTransport(lambda _request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Waiting()))
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(agent_reasoning, "get_http_client", lambda: client)
            task = asyncio.create_task(answer())
            await draining.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
