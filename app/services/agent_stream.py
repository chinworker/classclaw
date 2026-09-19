from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger
from app.services.http_client import get_http_client

DeltaHandler = Callable[[str], Awaitable[None]]


def completed_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AppError("OPENCLAW_INVALID_RESPONSE", "班级助手返回的响应格式不正确", 502)
    if payload.get("status") != "completed":
        raise AppError("OPENCLAW_PROCESSING_FAILED", "班级助手未能完整完成回复；涉及写入时请先核对结果再重试", 502)
    return payload


async def request_stream(
    body: dict[str, Any], *, gateway_url: str, headers: dict[str, str], timeout: float,
    on_delta: DeltaHandler, request_id: str | None,
    on_response_id: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Consume only public answer deltas, never reasoning/tool arguments.

    A terminal completed response is mandatory; EOF or a failed event cannot
    turn a partial answer into a successful write acknowledgement.
    """
    started = time.monotonic()
    first_delta: float | None = None
    outcome = "failed"
    try:
        async with asyncio.timeout(timeout):
            async with get_http_client().stream(
                "POST", f"{gateway_url}/v1/responses", headers=headers, json={**body, "stream": True}, timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    raise AppError("OPENCLAW_PROCESSING_FAILED", f"班级助手服务返回 HTTP {response.status_code}", 502)
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type == "application/json":
                    # Some older proxies buffer the response despite stream=true.
                    await response.aread()
                    result = completed_response(response.json())
                    if on_response_id:
                        on_response_id(result.get("id"))
                    outcome = "completed"
                    return result
                if content_type != "text/event-stream":
                    raise AppError("OPENCLAW_INVALID_RESPONSE", "班级助手未返回有效的流式响应", 502)
                data: list[str] = []
                size = 0
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        value = line[5:].lstrip(" ")
                        size += len(value)
                        if size > 1_048_576:
                            raise AppError("OPENCLAW_INVALID_RESPONSE", "班级助手单条响应过大", 502)
                        data.append(value)
                    elif line == "" and data:
                        raw = "\n".join(data)
                        data, size = [], 0
                        if raw == "[DONE]":
                            break
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise ValueError("Invalid stream event")
                        kind = event.get("type")
                        if kind in {"response.created", "response.in_progress"} and on_response_id:
                            resource = event.get("response")
                            if isinstance(resource, dict):
                                on_response_id(resource.get("id"))
                        elif kind == "response.output_text.delta":
                            delta = event.get("delta")
                            if not isinstance(delta, str):
                                raise ValueError("Invalid answer delta")
                            if delta:
                                if first_delta is None:
                                    first_delta = time.monotonic() - started
                                await on_delta(delta)
                        elif kind in {"response.completed", "response.failed", "response.incomplete"}:
                            if kind != "response.completed":
                                raise AppError("OPENCLAW_PROCESSING_FAILED", "班级助手未能完整完成回复；涉及写入时请先核对结果再重试", 502)
                            result = completed_response(event.get("response"))
                            if on_response_id:
                                on_response_id(result.get("id"))
                            outcome = "completed"
                            return result
                        elif kind == "error":
                            raise AppError("OPENCLAW_PROCESSING_FAILED", "班级助手处理失败；涉及写入时请先核对结果再重试", 502)
                raise AppError("OPENCLAW_STREAM_INTERRUPTED", "回复连接提前结束；涉及写入时请先核对结果再重试", 502)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise AppError("OPENCLAW_TIMEOUT", "班级助手响应超时，尚未收到完整结果；涉及写入时请先核对结果再重试", 504) from exc
    except httpx.RequestError as exc:
        raise AppError("OPENCLAW_CONNECTION_FAILED", "班级助手连接中断，请先核对已执行的操作再重试", 502) from exc
    except ValueError as exc:
        raise AppError("OPENCLAW_INVALID_RESPONSE", "班级助手返回了无法解析的流式响应", 502) from exc
    finally:
        get_logger("openclaw").info(
            "Chat stream outcome=%s first_delta_ms=%s", outcome, round(first_delta * 1000, 2) if first_delta is not None else None,
            extra={"request_id": request_id, "duration_ms": round((time.monotonic() - started) * 1000, 2)},
        )
