from __future__ import annotations

from app.services import http_client


def test_shared_http_client_does_not_proxy_local_gateway(monkeypatch):
    captured: dict = {}

    class FakeClient:
        is_closed = False

    def build_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(http_client, "_client", None)
    monkeypatch.setattr(http_client.httpx, "AsyncClient", build_client)
    assert isinstance(http_client.get_http_client(), FakeClient)
    assert captured == {"trust_env": False}
