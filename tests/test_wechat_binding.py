from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime

import httpx
import pytest

from app.core.errors import AppError
from app.services import openclaw_provisioning, wechat_login
from tests.helpers import make_binding, teacher_for_class

LOGIN_ID = "22222222-2222-2222-2222-222222222222"
CHALLENGE_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture()
def pending_binding(db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = make_binding(db, cls.id, tmp_path / "class-agent",
                           agent_name="测试 Agent", openclaw_agent_id=f"classclaw-{cls.id}",
                           channel_account_id=f"class-{cls.id}", status="awaiting_qr")
    async def runtime(*_args):
        return binding
    monkeypatch.setattr(openclaw_provisioning, "ensure_class_agent_runtime", runtime)
    return binding


def test_wait_without_replacement_qr_is_pending_not_an_error(client, db, pending_binding, monkeypatch):
    async def rpc(class_id, action, **params):
        assert action == "wait" and class_id == pending_binding.class_id
        assert params == {"loginId": LOGIN_ID}
        return {"connected": False, "message": "等待扫码确认", "loginId": LOGIN_ID}

    monkeypatch.setattr(wechat_login, "call", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["connected"] is False
    assert result["restart_required"] is False
    assert result["qr_data_url"] is None
    assert result["binding"]["status"] == "awaiting_qr"
    db.refresh(pending_binding)
    assert pending_binding.last_error is None


@pytest.mark.parametrize("message", ["二维码已过期，请重新生成。", "当前没有进行中的登录，请先发起登录。", "登录超时，请重试。"])
def test_expired_provider_session_requests_explicit_regeneration(client, pending_binding, monkeypatch, message):
    async def rpc(_class_id, action, **_params):
        assert action == "wait"
        return {"connected": False, "message": message, "restartRequired": True, "loginId": LOGIN_ID}

    monkeypatch.setattr(wechat_login, "call", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 200
    assert response.json()["data"]["restart_required"] is True
    assert response.json()["data"]["connected"] is False


def test_start_still_rejects_missing_initial_qr(client, pending_binding, monkeypatch):
    async def ensure_agent(*_args):
        return None

    async def rpc(_class_id, action, **_params):
        assert action == "start"
        return {"connected": False, "message": "无二维码", "loginId": LOGIN_ID}

    monkeypatch.setattr(openclaw_provisioning, "_ensure_agent", ensure_agent)
    monkeypatch.setattr(wechat_login, "call", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/start", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WECHAT_QR_UNAVAILABLE"


def test_wait_can_replace_qr_without_persisting_its_content(client, db, pending_binding, monkeypatch):
    async def rpc(_class_id, action, **_params):
        assert action == "wait"
        return {"connected": False, "qrDataUrl": "weixin://test-new-qr", "loginId": LOGIN_ID}

    monkeypatch.setattr(wechat_login, "call", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 200
    assert response.json()["data"]["qr_data_url"].startswith("data:image/png;base64,")
    db.refresh(pending_binding)
    assert pending_binding.status == "awaiting_qr"
    assert pending_binding.qr_generated_at is not None
    assert "weixin://test-new-qr" not in str(pending_binding.__dict__)


def test_teacher_can_manage_own_binding_but_not_another_class(client, db, sample, pending_binding, monkeypatch):
    owned, other, _ = sample
    headers = teacher_for_class(client, owned, db, "qr-teacher")

    async def ensure_agent(*_args):
        return None

    async def rpc(_class_id, action, **_params):
        assert action in {"start", "wait", "verify"}
        return {"connected": False, "qrDataUrl": "weixin://own-class-only", "loginId": LOGIN_ID}

    monkeypatch.setattr(openclaw_provisioning, "_ensure_agent", ensure_agent)
    monkeypatch.setattr(wechat_login, "call", rpc)
    assert client.get(f"/api/v1/classes/{owned.id}/agent-binding", headers=headers).status_code == 200
    started = client.post(f"/api/v1/classes/{owned.id}/agent-binding/start", json={}, headers=headers)
    assert started.status_code == 200
    assert started.json()["data"]["qr_data_url"].startswith("data:image/png;base64,")
    assert client.post(f"/api/v1/classes/{owned.id}/agent-binding/wait", json={"login_id": LOGIN_ID}, headers=headers).status_code == 200
    verification = {"login_id": LOGIN_ID, "challenge_id": CHALLENGE_ID, "code": "0012"}
    assert client.post(f"/api/v1/classes/{owned.id}/agent-binding/verify", json=verification, headers=headers).status_code == 200
    assert client.post(f"/api/v1/classes/{other.id}/agent-binding/verify", json=verification, headers=headers).status_code == 403

    for suffix, method in [("", "GET"), ("/provision", "POST"), ("/start", "POST"), ("/wait", "POST")]:
        result = client.request(method, f"/api/v1/classes/{other.id}/agent-binding{suffix}", headers=headers,
                                **({"json": {}} if method == "POST" else {}))
        assert result.status_code == 403
        assert result.json()["error"]["code"] == "CLASS_ACCESS_DENIED"


def test_verification_is_scoped_and_never_saved_or_echoed(client, db, pending_binding, monkeypatch):
    captured = {}

    async def call(class_id, action, **params):
        captured.update(class_id=class_id, action=action, **params)
        return {"loginId": LOGIN_ID, "connected": False, "state": "waiting", "verificationRequired": False}

    monkeypatch.setattr(wechat_login, "call", call)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/verify",
                           json={"login_id": LOGIN_ID, "challenge_id": CHALLENGE_ID, "code": "001234567"})
    assert response.status_code == 200
    assert captured == {"class_id": pending_binding.class_id, "action": "verify", "loginId": LOGIN_ID,
                        "challengeId": CHALLENGE_ID, "code": "001234567"}
    assert "001234567" not in response.text
    db.refresh(pending_binding)
    assert "001234567" not in str(pending_binding.__dict__)


@pytest.mark.parametrize("patch", [{"code": "SENSITIVE-invalid"}, {"login_id": "invalid", "code": "987654321"}, {"challenge_id": None}])
def test_verification_validation_does_not_echo_secret_inputs(client, pending_binding, patch):
    body = {"login_id": LOGIN_ID, "challenge_id": CHALLENGE_ID, "code": "987654321", **patch}
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/verify", json=body)
    assert response.status_code == 422
    assert body["code"] not in response.text
    assert '"input"' not in response.text


def test_verification_obeys_feature_gate(client, pending_binding, monkeypatch):
    def disabled(_key):
        raise AppError("FEATURE_DISABLED", "微信绑定已关闭", 403)

    monkeypatch.setattr("app.services.admin_console.require_feature", disabled)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/verify",
                           json={"login_id": LOGIN_ID, "challenge_id": CHALLENGE_ID, "code": "1234"})
    assert response.status_code == 403


def test_old_wait_cannot_overwrite_regenerated_binding(client, db, pending_binding, monkeypatch):
    old = wechat_login.begin(pending_binding.class_id)
    wechat_login.accept(pending_binding.class_id, old, LOGIN_ID)

    async def call(class_id, _action, **_params):
        new = wechat_login.begin(class_id)
        wechat_login.accept(class_id, new, CHALLENGE_ID)
        return {"loginId": LOGIN_ID, "connected": True, "accountId": "old-wx-account"}

    monkeypatch.setattr(wechat_login, "call", call)
    previous = pending_binding.channel_account_id
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WECHAT_LOGIN_STALE"
    db.refresh(pending_binding)
    assert pending_binding.channel_account_id == previous
    assert pending_binding.status == "awaiting_qr"
    assert pending_binding.last_error is None


def test_old_wait_is_rejected_while_a_new_start_is_still_preparing(client, pending_binding):
    wechat_login.begin(pending_binding.class_id)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WECHAT_LOGIN_STALE"


def test_scan_cannot_reassign_another_class_account(client, db, sample, pending_binding, monkeypatch, tmp_path):
    make_binding(db, sample[1].id, tmp_path / "other", agent_name="other",
                 channel_account_id="already-owned", status="linked")

    async def call(*_args, **_kwargs):
        return {"loginId": LOGIN_ID, "connected": True, "accountId": "already-owned"}

    monkeypatch.setattr(wechat_login, "call", call)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WECHAT_ACCOUNT_CONFLICT"


@pytest.mark.parametrize("rebind", [False, True])
def test_successful_login_reloads_listener_with_class_route(client, db, pending_binding, monkeypatch, rebind):
    account_id = "new-wx-account"
    existing_route = {"agentId": "other-agent", "match": {"channel": "openclaw-weixin", "accountId": "other-account"}}
    class_route = {"agentId": pending_binding.openclaw_agent_id,
                   "match": {"channel": "openclaw-weixin", "accountId": account_id}}
    config = {
        "bindings": [existing_route, *([class_route] if rebind else [])],
        "channels": {
            "openclaw-weixin": {"channelConfigUpdatedAt": "2026-09-12T12:00:00+08:00",
                                "accounts": {"other-account": {"enabled": False}}, "routeTag": 7},
            "other-channel": {"enabled": True},
        },
    }
    original = deepcopy(config)
    patches = []

    async def login(*_args, **_kwargs):
        return {"loginId": LOGIN_ID, "connected": True, "accountId": account_id}

    async def rpc(method, params=None):
        if method == "config.get":
            return {"hash": "before-login", "config": config}
        assert method == "config.patch"
        patches.append(params)
        return {"ok": True}

    monkeypatch.setattr(wechat_login, "call", login)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 200
    assert response.json()["data"]["route_ready"] is True
    assert len(patches) == 1
    params = patches[0]
    patch = json.loads(params["raw"])
    marker = patch["channels"]["openclaw-weixin"]["channelConfigUpdatedAt"]
    assert datetime.fromisoformat(marker).tzinfo is not None
    assert marker != config["channels"]["openclaw-weixin"]["channelConfigUpdatedAt"]
    assert patch["bindings"][0] == existing_route
    assert patch["bindings"][1]["match"] == class_route["match"]
    assert patch["bindings"][1]["agentId"] == pending_binding.openclaw_agent_id
    assert len(patch["bindings"]) == 2
    assert params["baseHash"] == "before-login"
    # Merge the reload marker; never replace the channel or its account settings.
    assert patch["channels"] == {"openclaw-weixin": {"channelConfigUpdatedAt": marker}}
    assert params["replacePaths"] == ["bindings", "agents.list"]
    assert config == original
    db.refresh(pending_binding)
    assert pending_binding.channel_account_id == account_id
    assert pending_binding.status == "linked"


def test_failed_route_and_listener_patch_does_not_report_ready(client, db, pending_binding, monkeypatch):
    async def login(*_args, **_kwargs):
        return {"loginId": LOGIN_ID, "connected": True, "accountId": "new-wx-account"}

    async def rpc(method, params=None):
        if method == "config.get":
            return {"hash": "before-login", "config": {}}
        assert method == "config.patch"
        raise AppError("OPENCLAW_ADMIN_UNAVAILABLE", "配置版本冲突", 503)

    monkeypatch.setattr(wechat_login, "call", login)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={"login_id": LOGIN_ID})
    assert response.status_code == 503
    db.refresh(pending_binding)
    assert pending_binding.status == "awaiting_qr"
    assert pending_binding.linked_at is None


def test_checking_existing_route_does_not_restart_listener(client, db, pending_binding, monkeypatch):
    pending_binding.status = "linked"
    db.commit()
    calls = []

    async def rpc(method, params=None):
        calls.append(method)
        assert method == "config.get"
        return {"config": {
            "bindings": [{"agentId": pending_binding.openclaw_agent_id,
                          "match": {"channel": "openclaw-weixin", "accountId": pending_binding.channel_account_id}}],
            "plugins": {"entries": {"classclaw": {"config": {
                "agentClasses": {pending_binding.openclaw_agent_id: pending_binding.class_id},
            }}}},
        }}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{pending_binding.class_id}/agent-binding/wait", json={})
    assert response.status_code == 200
    assert response.json()["data"]["route_ready"] is True
    assert calls == ["config.get"]


@pytest.mark.parametrize("status, payload, expected", [
    (404, {}, "WECHAT_PLUGIN_UPDATE_REQUIRED"),
    (409, {"ok": False, "error": {"code": "WECHAT_LOGIN_STALE", "message": "SECRET QR and code"}}, "WECHAT_LOGIN_STALE"),
    (500, {"ok": False, "error": ["SECRET"]}, "WECHAT_LOGIN_UNAVAILABLE"),
    (200, {"ok": True, "payload": {"loginId": "invalid"}}, "WECHAT_RESPONSE_INVALID"),
])
def test_private_transport_handles_old_plugin_and_malformed_responses_without_secret_leaks(monkeypatch, status, payload, expected):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))) as http:
            monkeypatch.setattr(wechat_login, "get_http_client", lambda: http)
            with pytest.raises(AppError) as error:
                await wechat_login.call("11111111-1111-1111-1111-111111111111", "wait", loginId=LOGIN_ID)
            assert error.value.code == expected
            assert "SECRET" not in str(error.value)
    asyncio.run(run())


def test_private_transport_uses_backend_auth_and_fixed_endpoint(monkeypatch):
    def response(request):
        assert request.url.path == "/api/v1/classclaw/wechat-login"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert b'"action":"verify"' in request.content
        return httpx.Response(200, json={"ok": True, "payload": {"loginId": LOGIN_ID, "connected": False}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            monkeypatch.setattr(wechat_login, "get_http_client", lambda: http)
            result = await wechat_login.call("11111111-1111-1111-1111-111111111111", "verify", loginId=LOGIN_ID, challengeId=CHALLENGE_ID, code="00123")
            assert result["connected"] is False
    asyncio.run(run())
