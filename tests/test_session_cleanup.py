from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.core.errors import AppError
from app.services import openclaw_bridge as bridge
from dataclasses import replace


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


def test_cleanup_builds_cli_args(monkeypatch):
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        return FakeProc(0, stdout=b"archived 12 sessions")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(bridge.cleanup_openclaw_sessions(enforce=True))
    assert captured["args"][:3] == ["openclaw", "sessions", "cleanup"]
    assert "--enforce" in captured["args"]
    assert result["ok"] is True
    assert "archived 12 sessions" in result["output"]

    result = asyncio.run(bridge.cleanup_openclaw_sessions(enforce=False))
    assert "--dry-run" in captured["args"]
    assert result["ok"] is True


def test_cleanup_missing_cli_raises(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("openclaw")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(AppError):
        asyncio.run(bridge.cleanup_openclaw_sessions())


def test_auto_cleanup_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(bridge, "settings", replace(settings, openclaw_session_cleanup_hours=0))
    assert asyncio.run(bridge.maybe_auto_session_cleanup()) is None


def test_auto_cleanup_skipped_when_gateway_down(monkeypatch):
    monkeypatch.setattr(bridge, "_auto_cleanup_checked_at", None)

    async def down(force: bool = False):
        return {"gateway_live": False}

    monkeypatch.setattr(bridge, "connection_status", down)
    assert asyncio.run(bridge.maybe_auto_session_cleanup()) is None
    assert bridge._auto_cleanup_checked_at is None


def test_auto_cleanup_runs_when_due(monkeypatch):
    monkeypatch.setattr(bridge, "_auto_cleanup_checked_at", None)
    calls = []

    async def live(force: bool = False):
        return {"gateway_live": True}

    async def fake_run(enforce: bool = True):
        calls.append(enforce)
        return {"ok": True}

    monkeypatch.setattr(bridge, "connection_status", live)
    monkeypatch.setattr(bridge, "run_session_cleanup", fake_run)
    result = asyncio.run(bridge.maybe_auto_session_cleanup())
    assert result == {"ok": True}
    assert calls == [True]
    assert bridge._auto_cleanup_checked_at is not None


def test_admin_session_cleanup_endpoints(client, monkeypatch):
    async def fake_run(enforce: bool = True):
        return {"ok": True, "enforce": enforce, "exit_code": 0, "output": "archived 12 sessions"}

    monkeypatch.setattr(bridge, "run_session_cleanup", fake_run)
    response = client.post("/api/v1/admin/openclaw/sessions/cleanup?enforce=false")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["enforce"] is False

    response = client.get("/api/v1/admin/openclaw/sessions/cleanup")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["interval_hours"] == settings.openclaw_session_cleanup_hours
