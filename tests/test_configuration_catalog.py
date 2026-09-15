from __future__ import annotations

import json
from dataclasses import replace

from app.config import CONFIG_ENVIRONMENT_VARIABLES, ConfigDocument, load_settings, settings
from app.models.entities import ClassAgentBinding
from app.services import admin_console, configuration_catalog, openclaw_provisioning


def _leaf_paths(value: dict, prefix: str = "") -> set[str]:
    result = set()
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        result.update(_leaf_paths(item, path) if isinstance(item, dict) else {path})
    return result


def test_catalog_covers_every_supported_startup_field_once():
    data = configuration_catalog.catalog(settings)
    paths = [row["config_path"] for row in data["items"]]
    assert len(paths) == len(set(paths))
    assert set(paths) == _leaf_paths(ConfigDocument().model_dump()) == set(CONFIG_ENVIRONMENT_VARIABLES)
    sections = {row["id"]: row["category"] for row in data["sections"]}
    for row in data["items"]:
        assert sections[row["section"]] == row["category"]
        assert row["label"] and row["description"] and row["env_var"]
        assert row["editable"] is (not row["value_source"].startswith("environment:"))
        assert row["restart_required"] is True
    assert {row["category"] for row in data["items"]} == {"classclaw", "openclaw"}
    rows = {row["config_path"]: row for row in data["items"]}
    assert rows["openclaw.session_cleanup_hours"]["type"] == "number"
    assert rows["server.port"]["minimum"] == 1
    assert rows["server.port"]["maximum"] == 65535


def test_catalog_reports_actual_startup_sources_and_resolved_values(tmp_path, monkeypatch):
    path = tmp_path / "classclaw.toml"
    path.write_text('[server]\nport = 9001\n[storage]\nattachment_dir = "files"\n[web.brand]\nname = "测试品牌"\n', encoding="utf-8")
    loaded = load_settings(path, environ={"CLASSCLAW_SERVER_PORT": "9002", "OPENCLAW_STATE_DIR": str(tmp_path / "state")})
    # Changes on disk or to process env after loading must not mislabel the running values.
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLASSCLAW_WEB_NAME", "尚未重启的新值")
    data = configuration_catalog.catalog(loaded)
    rows = {row["config_path"]: row for row in data["items"]}
    assert rows["server.port"]["value"] == 9002
    assert rows["server.port"]["value_source"] == "environment:CLASSCLAW_SERVER_PORT"
    assert rows["web.brand.name"]["value"] == "测试品牌"
    assert rows["web.brand.name"]["value_source"] == "file"
    assert rows["storage.attachment_dir"]["value"] == str(tmp_path / "files")
    assert rows["runtime.timezone"]["value_source"] == "default"
    assert rows["storage.openclaw_state_dir"]["value_source"] == "environment:OPENCLAW_STATE_DIR"
    assert data["config_file"] == str(path)


def test_catalog_is_admin_only_and_never_returns_credentials(client, monkeypatch):
    secrets = {"api_token": "private-service-token-ABCD", "openclaw_gateway_token": "private-gateway-token-EFGH", "default_admin_password": "private-password-IJKL"}
    monkeypatch.setattr(admin_console, "settings", replace(settings, **secrets))

    async def unavailable(*args, **kwargs):
        raise AssertionError("Startup configuration must not depend on Gateway availability")

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", unavailable)
    response = client.get("/api/v1/admin/settings/catalog")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert all(row["configured"] is True for row in response.json()["data"]["credentials"])
    for secret in secrets.values():
        assert secret not in response.text
        assert secret[-4:] not in response.text
    for row in response.json()["data"]["credentials"]:
        assert "value" not in row
    assert client.get("/api/v1/admin/settings/catalog", headers={"Authorization": ""}).status_code == 401
    client.post("/api/v1/admin/users", json={"username": "configreader"})
    token = client.post("/api/v1/auth/login", json={"username": "configreader", "password": "32767"}).json()["data"]["access_token"]
    denied = client.get("/api/v1/admin/settings/catalog", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_agent_catalog_includes_unprovisioned_classes_and_resolves_models(client, db, sample, monkeypatch):
    cls, other, _ = sample
    binding = ClassAgentBinding(
        class_id=cls.id, openclaw_agent_id="class-agent", agent_name="班级助手", workspace_path="/tmp/class-agent",
        main_model="outdated/model", image_model="provider/vision", speech_model="provider/speech", status="active",
    )
    db.add(binding)
    db.commit()

    async def rpc(method, params=None):
        assert method == "config.get"
        return {"config": {"agents": {"defaults": {"model": "provider/default"}, "list": [{"id": "class-agent", "model": "provider/actual"}]}}}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.get("/api/v1/admin/openclaw/agents/catalog")
    assert response.status_code == 200
    rows = {row["identifier"]: row for row in response.json()["data"]}
    assert rows[cls.id]["model_summary"] == {"main_model": "provider/actual", "inherits_main": False, "image_model": "provider/vision", "speech_model": "provider/speech"}
    assert rows[other.id]["present"] is False
    assert rows[other.id]["binding"] is None
    assert rows[other.id]["status"] == "pending_agent"
    assert rows["main"]["model_summary"]["main_model"] == "provider/default"


def test_gateway_summary_projects_provider_names_without_secrets(client, monkeypatch):
    monkeypatch.setattr(admin_console, "settings", replace(settings, openclaw_gateway_token="secret-gateway-XYZT"))

    async def rpc(method, params=None):
        return {
            "path": "/tmp/openclaw.json", "hash": "test-hash",
            "config": {
                "models": {"providers": {"example": {"apiKey": "secret-provider", "baseUrl": "https://secret-address"}}},
                "agents": {"defaults": {"model": {"primary": "example/main"}, "imageModel": "example/image"}},
                "gateway": {"auth": {"token": "secret-gateway-XYZT"}},
            },
        }

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.get("/api/v1/admin/openclaw/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["gateway"]["token_configured"] is True
    assert data["file_config"] == {"path": "/tmp/openclaw.json", "default_model": "example/main", "default_image_model": "example/image", "providers": ["example"]}
    for forbidden in ("secret-provider", "secret-address", "secret-gateway", "XYZT", "apiKey", "token_masked"):
        assert forbidden not in json.dumps(data)
