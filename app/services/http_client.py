from __future__ import annotations

import httpx

from app.config import settings


def gateway_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.openclaw_gateway_token:
        headers["Authorization"] = f"Bearer {settings.openclaw_gateway_token}"
    return headers

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # Gateway is always a directly connected local/trusted endpoint.
        # macOS system proxies can otherwise incorrectly route 127.0.0.1
        # traffic; *_PROXY handling is therefore opt-in via runtime.http_trust_env.
        _client = httpx.AsyncClient(trust_env=settings.http_trust_env)
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
