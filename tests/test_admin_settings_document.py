from __future__ import annotations

import json
import stat
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import CONFIG_ENVIRONMENT_VARIABLES, ConfigurationError, load_settings
from app.core.errors import AppError
from app.models.entities import AuditLog
from app.models.usage import AiUsageRecord
from app.services import config_document, openclaw_provisioning
from app.services import logs as log_service
from app.utils.time import now
from tests.helpers import teacher_for_class


@pytest.fixture
def document_file(tmp_path, monkeypatch):
    for name in [*CONFIG_ENVIRONMENT_VARIABLES.values(), "OPENCLAW_STATE_DIR", "CLASSCLAW_CONFIG_FILE"]:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config" / "classclaw.toml"
    path.parent.mkdir()
    path.write_text('# 配置\n[server]\nport = 8000\n', encoding="utf-8")
    path.chmod(0o640)
    monkeypatch.setattr(config_document, "CONFIG_PATH", path)
    monkeypatch.setattr(config_document, "settings", load_settings(path))
    return path


def snapshot(client):
    result = client.get("/api/v1/admin/settings/document")
    assert result.status_code == 200, result.text
    return result.json()["data"]


def save(client, state=None, **body):
    state = state or snapshot(client)
    return client.patch("/api/v1/admin/settings/document", json={
        "base_hash": state["config_hash"], "base_document_hash": state["document_hash"], **body,
    })


def test_document_read_no_credentials_and_live_hash(client, document_file, monkeypatch):
    monkeypatch.setenv("CLASSCLAW_OPENCLAW_GATEWAY_TOKEN", "very-private-value")
    response = client.get("/api/v1/admin/settings/document")
    assert response.headers["cache-control"] == "no-store"
    assert "very-private-value" not in response.text
    data = response.json()["data"]
    assert data["toml_text"] == document_file.read_text()
    assert data["config_hash"] == load_settings(document_file).config_hash
    assert data["layout"]["spans"][0]["values"] == {"server.port": 8000}


def test_save_backup_permissions_and_key_only_audit(client, db, document_file):
    before = snapshot(client)
    response = save(client, before, changes={"server.port": 8001})
    assert response.status_code == 200, response.text
    assert load_settings(document_file).server.port == 8001
    data = response.json()["data"]
    assert data["new_config_hash"] != before["config_hash"]
    assert len(list(document_file.parent.glob("*.bak-*"))) == 1
    assert stat.S_IMODE(document_file.stat().st_mode) == 0o640
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "config_update"))
    assert audit.after_json == {"paths": ["server.port"], "old_config_hash": before["config_hash"], "new_config_hash": data["new_config_hash"]}
    assert audit.before_json is None
    assert "8001" not in json.dumps(audit.after_json)
    after = snapshot(client)
    assert after["active_config_hash"] == before["config_hash"]
    assert after["config_hash"] == data["new_config_hash"]


def test_stale_hash_conflicts(client, document_file):
    old = snapshot(client)
    assert save(client, old, changes={"server.port": 8001}).status_code == 200
    assert save(client, old, changes={"server.port": 8002}).status_code == 409
    old = snapshot(client)
    document_file.write_text(document_file.read_text() + "# external comment\n")
    assert save(client, old, changes={"server.port": 8003}).status_code == 409


@pytest.mark.parametrize(("body", "status", "code"), [
    ({"toml_text": '[server]\nport = "bad'}, 422, "CONFIG_TOML_INVALID"),
    ({"changes": {"openclaw.gateway_token": "hidden"}}, 403, "CONFIG_SECRET_FORBIDDEN"),
    ({"toml_text": '[bootstrap]\ndefault_admin_password = "hidden"\n'}, 403, "CONFIG_SECRET_FORBIDDEN"),
    ({"changes": {"server.port": 70000}}, 422, "VALIDATION_ERROR"),
    ({"toml_text": "[server]\nport = 70000\n"}, 422, "VALIDATION_ERROR"),
    ({"toml_text": '[server]\n"un=known" = 1\n'}, 422, "VALIDATION_ERROR"),
    ({"changes": {"storage.attachment_dir": ""}}, 422, "VALIDATION_ERROR"),
    ({"changes": {"server.unknown": True}}, 422, "VALIDATION_ERROR"),
    ({"changes": {}, "toml_text": ""}, 422, "VALIDATION_ERROR"),
])
def test_invalid_documents_never_write(client, document_file, body, status, code):
    original = document_file.read_text()
    response = save(client, **body)
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    if code == "CONFIG_TOML_INVALID":
        assert error["details"]["errors"][0]["line"] == 2
    if body == {"changes": {"server.port": 70000}}:
        assert error["details"]["errors"][0]["config_path"] == "server.port"
    if body == {"toml_text": "[server]\nport = 70000\n"}:
        assert error["details"]["errors"][0]["line"] == 2
    if body == {"toml_text": '[server]\n"un=known" = 1\n'}:
        assert error["details"]["errors"][0]["config_path"] == "server.un=known"
        assert error["details"]["errors"][0]["line"] == 2
    assert document_file.read_text() == original
    assert not list(document_file.parent.glob("*.bak-*"))


def test_env_locked_changes_and_deletion_rejected_but_unchanged_allowed(client, document_file, monkeypatch):
    monkeypatch.setenv("CLASSCLAW_SERVER_PORT", "9000")
    state = snapshot(client)
    assert "server.port" in state["env_locked_paths"]
    for body in ({"changes": {"server.port": 8000}}, {"toml_text": ""}):
        result = save(client, state, **body)
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "CONFIG_ENV_OVERRIDDEN"
    response = save(client, state, toml_text=state["toml_text"] + '[features]\nfile_analysis = false\n')
    assert response.status_code == 200, response.text


def test_check_is_dry_and_preserves_relative_path_basis(client, document_file):
    original = document_file.read_text()
    response = client.post("/api/v1/admin/settings/check", json={"toml_text": '[storage]\nattachment_dir = "../files"\n'})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    diff = next(row for row in data["diff"] if row["path"] == "storage.attachment_dir")
    assert diff["new"] == str(document_file.parent.parent / "files")
    assert data["values"]["storage.attachment_dir"] == "../files"
    assert document_file.read_text() == original
    assert len(list(document_file.parent.iterdir())) == 1


def test_reload_failure_restores_original(client, document_file, monkeypatch):
    state = snapshot(client)
    original = document_file.read_text()
    real = config_document.load_settings

    def fail_written(*args, **kwargs):
        if "toml_text" not in kwargs:
            raise ConfigurationError("simulated post-write failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(config_document, "load_settings", fail_written)
    response = save(client, state, changes={"server.port": 8001})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "CONFIG_WRITE_FAILED"
    assert document_file.read_text() == original


def test_rollback_restores_latest_and_audits(client, db, document_file):
    original = document_file.read_text()
    assert save(client, changes={"server.port": 8001}).status_code == 200
    state = snapshot(client)
    response = client.post("/api/v1/admin/settings/rollback", json={"base_hash": state["config_hash"]})
    assert response.status_code == 200, response.text
    assert document_file.read_text() == original
    assert db.scalar(select(AuditLog).where(AuditLog.action == "config_rollback")) is not None


def test_symlink_escape_rejected(client, document_file, tmp_path):
    target = tmp_path / "outside.toml"
    target.write_text("# outside\n")
    document_file.unlink()
    document_file.symlink_to(target)
    assert client.get("/api/v1/admin/settings/document").status_code == 403
    assert target.read_text() == "# outside\n"


def test_missing_document_created_atomically(client, document_file):
    document_file.unlink()
    assert snapshot(client)["toml_text"] is None
    assert save(client, changes={"server.port": 8010}).status_code == 200
    assert load_settings(document_file).server.port == 8010


def test_layout_handles_multiline_and_inline_tables(client, document_file):
    source = 'server = { port = 9001 }\n[web.brand]\nlogin_description = """hello\nworld"""\n'
    response = client.post("/api/v1/admin/settings/check", json={"toml_text": source})
    assert response.status_code == 200, response.text
    spans = response.json()["data"]["layout"]["spans"]
    assert spans[0]["root"] == "server"
    assert spans[1]["values"] == {"web.brand.login_description": "hello\nworld"}
    assert source[spans[1]["start"]:spans[1]["end"]].startswith("login_description")


@pytest.mark.parametrize("patch", [{"gateway.auth.token": "bad"}, {"session.dmScope": {}}, {"session.dmScope": None},
                                    {"session.dmScope": "bad"}, {"plugins.entries.classclaw.enabled": 1}])
def test_gateway_whitelist_rejects_invalid_without_rpc(client, monkeypatch, patch):
    async def fail(*args, **kwargs):
        raise AssertionError("Invalid input must not reach Gateway")
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fail)
    result = client.patch("/api/v1/admin/openclaw/config/raw", json={"patch": patch, "base_hash": "hash"})
    assert result.status_code == 422


def test_gateway_redaction_patch_hash_conflict_and_audit(client, db, monkeypatch):
    calls = []

    async def rpc(method, params=None):
        if method == "config.get":
            return {"hash": "h1", "raw": "secret-raw-value", "config": {
                "gateway": {"auth": {"token": "secret-token-value"}},
                "models": {"providers": [{"apiKey": "secret-key-value", "credentials": {"v": "private"}}]},
            }}
        calls.append(params)
        return {"hash": "h2"}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    data = client.get("/api/v1/admin/openclaw/config/raw")
    for value in ("secret-raw-value", "secret-token-value", "secret-key-value", "private"):
        assert value not in data.text
    body = {"patch": {"session.dmScope": "per-account-channel-peer"}, "base_hash": "h1"}
    assert client.patch("/api/v1/admin/openclaw/config/raw", json=body).status_code == 200
    assert calls[0]["baseHash"] == "h1"
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "gateway_config_patch"))
    assert audit.after_json["new_config_hash"] == "h2"
    assert "per-account-channel-peer" not in json.dumps(audit.after_json)
    body["base_hash"] = "stale"
    assert client.patch("/api/v1/admin/openclaw/config/raw", json=body).status_code == 409

    async def race(method, params=None):
        if method == "config.get":
            return {"hash": "h1"}
        raise AppError("OPENCLAW_ADMIN_UNAVAILABLE", "config changed; stale baseHash", 503)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", race)
    body["base_hash"] = "h1"
    assert client.patch("/api/v1/admin/openclaw/config/raw", json=body).status_code == 409


def test_gateway_legacy_config_patch_audits_and_translates_conflict(client, db, monkeypatch):
    calls = []

    async def rpc(method, params=None):
        if method == "config.get":
            return {"hash": "h1", "config": {"session": {"dmScope": "per-peer"}}}
        calls.append(params)
        return {"hash": "h2"}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    body = {"dm_scope": "per-account-channel-peer", "responses_enabled": True}
    response = client.patch("/api/v1/admin/openclaw/config", json=body)
    assert response.status_code == 200, response.text
    assert calls[0]["baseHash"] == "h1"
    assert sorted(calls[0]["replacePaths"]) == ["gateway.http.endpoints.responses.enabled", "session.dmScope"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "gateway_config_patch"))
    assert audit.after_json["paths"] == ["gateway.http.endpoints.responses.enabled", "session.dmScope"]
    assert audit.after_json["old_config_hash"] == "h1"
    assert audit.after_json["new_config_hash"] == "h2"
    assert "per-account-channel-peer" not in json.dumps(audit.after_json)

    async def race(method, params=None):
        if method == "config.get":
            return {"hash": "h1"}
        raise AppError("OPENCLAW_ADMIN_UNAVAILABLE", "config changed; stale baseHash", 503)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", race)
    assert client.patch("/api/v1/admin/openclaw/config", json=body).status_code == 409


def test_request_log_filter_redaction_increment_and_partial_lines(client, document_file, monkeypatch, tmp_path):
    path = tmp_path / "requests.log"
    lines = [{"timestamp": "2026-09-15T09:00:00+08:00", "level": "ERROR", "request_id": key,
              "message": "Authorization: Bearer private-bearer token=private-token", "password": "private-password"}
             for key in ("abc", "abc2")]
    path.write_text("".join(json.dumps(row) + "\n" for row in lines))
    monkeypatch.setattr(log_service, "settings", replace(config_document.settings, log_file=path))
    response = client.get("/api/v1/admin/logs?request_id=abc")
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert "private-" not in response.text
    assert data["items"][0]["request_id"] == "abc"
    result = log_service.classclaw_logs(limit=20, cursor=data["cursor"], file_id=data["file_id"])
    assert result["items"] == []
    with path.open("a") as stream:
        stream.write(json.dumps({"request_id": "abc", "message": "new"}))
    assert log_service.classclaw_logs(limit=20, cursor=data["cursor"])["items"] == []
    with path.open("a") as stream:
        stream.write("\n")
    assert log_service.classclaw_logs(limit=20, cursor=data["cursor"])["items"][0]["message"] == "new"
    gateway = log_service.openclaw_logs_payload({"lines": [json.dumps(row) for row in lines]}, limit=20, request_id="abc")
    assert len(gateway["items"]) == 1
    assert "private-" not in json.dumps(gateway)


def test_teacher_denied_all_new_endpoints(client, sample, db, document_file):
    headers = teacher_for_class(client, sample[0], db, "blocked-admin-config")
    for method, path, body in [
        ("GET", "/settings/document", None), ("POST", "/settings/check", {"toml_text": ""}),
        ("PATCH", "/settings/document", {"base_hash": "h", "toml_text": ""}),
        ("POST", "/settings/rollback", {"base_hash": "h"}), ("GET", "/openclaw/config/raw", None),
        ("PATCH", "/openclaw/config/raw", {"base_hash": "h", "patch": {"session.dmScope": "main"}}),
    ]:
        result = client.request(method, f"/api/v1/admin{path}", headers=headers, **({"json": body} if body else {}))
        assert result.status_code == 403


def test_usage_daily_requests_match_summary_and_overview_reports_today(client, usage_db, document_file):
    current = now()
    usage_db.add_all([
        AiUsageRecord(operation="chat", total_tokens=120, created_at=current),
        AiUsageRecord(operation="extract", total_tokens=90, created_at=current - timedelta(days=1)),
    ])
    usage_db.commit()
    usage = client.get("/api/v1/admin/usage?days=7").json()["data"]
    assert usage["totals"]["ai_requests"] == sum(row["requests"] for row in usage["daily"]) == 2
    assert [row["total_tokens"] for row in usage["daily"]] == [90, 120]
    overview = client.get("/api/v1/admin/overview").json()["data"]
    assert overview["health"]["usage_database"] is True
    assert overview["counts"]["today_tokens"] == 120
