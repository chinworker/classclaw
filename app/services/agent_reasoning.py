from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

import httpx

from app.services.http_client import get_http_client

ThinkingHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def _relay(response: httpx.Response, selected: asyncio.Future, on_thinking: ThinkingHandler) -> None:
    """Forward only text for the exact Responses run; never tool/session metadata."""
    data: list[str] = []
    size = total = 0
    try:
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                value = line[5:].lstrip(" ")
                size += len(value)
                if size > 1_048_576:
                    return
                data.append(value)
            elif line == "" and data:
                event = json.loads("\n".join(data))
                data, size = [], 0
                if not isinstance(event, dict) or event.get("type") not in {"thinking", "done"}:
                    continue
                run_id = await selected
                if not run_id:
                    return
                if event.get("run_id") != run_id:
                    continue
                if event["type"] == "done":
                    return
                text = event.get("text")
                if not isinstance(text, str):
                    return
                total += len(text)
                if total > 1_048_576:
                    return
                if text:
                    await on_thinking({"state": "delta", "text": text})
    except (httpx.HTTPError, ValueError):
        # Reasoning previews are optional. A failed preview must never change
        # whether the independent answer stream is considered completed.
        return


@asynccontextmanager
async def observe_reasoning(*, gateway_url: str, headers: dict[str, str], session_key: str, timeout: float,
                            on_thinking: ThinkingHandler | None):
    """Subscribe before inference, then bind to response.created's run ID.

    No reasoning text is persisted or logged. Closing the answer, cancelling or
    navigating through logout closes this subscription and its reader task.
    """
    async with AsyncExitStack() as stack:
        response = None
        if on_thinking is not None:
            try:
                async with asyncio.timeout(3):
                    response = await stack.enter_async_context(get_http_client().stream(
                        "POST", f"{gateway_url}/api/v1/classclaw/web-chat-reasoning", headers=headers,
                        json={"key": session_key}, timeout=timeout,
                    ))
            except (TimeoutError, httpx.HTTPError):
                pass
        if response is None or response.status_code != 200 or not response.headers.get("content-type", "").startswith("text/event-stream"):
            yield lambda _run_id: None
            return
        selected = asyncio.get_running_loop().create_future()

        def select_run(run_id: str) -> None:
            if not selected.done() and isinstance(run_id, str) and run_id:
                selected.set_result(run_id)

        task = asyncio.create_task(_relay(response, selected, on_thinking))
        try:
            yield select_run
        finally:
            if not selected.done():
                selected.set_result(None)
            # The lifecycle end event normally precedes response.completed.
            # Allow queued previews to drain, without making completion depend on them.
            try:
                async with asyncio.timeout(0.25):
                    await task
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise
