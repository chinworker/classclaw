from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.config import BASE_DIR, ConfigurationError, load_settings, settings
from app.services.semester_calendar import current_or_next_semester
from scripts.check_config import _security_checks


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_example_configuration_is_valid():
    loaded = load_settings(BASE_DIR / "config" / "classclaw.example.toml", environ={})

    assert loaded.config_file == BASE_DIR / "config" / "classclaw.example.toml"
    assert loaded.features.file_analysis is True
    assert loaded.web.brand.name == "ClassClaw"
    assert loaded.openclaw_timeout_seconds == 120
    assert loaded.server.host == "127.0.0.1"
    assert loaded.server.port == 8000
    # 媒体转发默认不接入：网页只能看到设备状态，不能声称有画面。
    assert loaded.classroom.media_provider == "none"
    assert loaded.classroom.media_base_url == ""
    assert loaded.classroom.command_ttl_seconds > loaded.classroom.display_seconds_max * loaded.classroom.speak_repeat_max


def test_toml_paths_are_resolved_from_configuration_directory(tmp_path):
    config = _write_config(
        tmp_path / "classclaw.toml",
        """
[storage]
database_url = "sqlite:///runtime/classclaw.db"
attachment_dir = "runtime/attachments"
class_workspace_root = "runtime/workspaces"
openclaw_state_dir = "runtime/openclaw-state"

[runtime]
log_file = "runtime/logs/classclaw.log"
""",
    )

    loaded = load_settings(config, environ={})

    assert loaded.database_url == f"sqlite:///{tmp_path / 'runtime' / 'classclaw.db'}"
    assert loaded.attachment_dir == (tmp_path / "runtime" / "attachments").resolve()
    assert loaded.log_file == (tmp_path / "runtime" / "logs" / "classclaw.log").resolve()
    assert loaded.openclaw_class_workspace_root == (tmp_path / "runtime" / "workspaces").resolve()
    assert loaded.openclaw_state_dir == (tmp_path / "runtime" / "openclaw-state").resolve()


def test_environment_overrides_toml_and_keeps_legacy_names(tmp_path):
    config = _write_config(
        tmp_path / "classclaw.toml",
        """
[features]
file_analysis = true

[web]
usage_window_days = 30

[storage]
openclaw_state_dir = "runtime/openclaw-state"
""",
    )

    loaded = load_settings(
        config,
        environ={
            "CLASSCLAW_FEATURE_FILE_ANALYSIS": "off",
            "CLASSCLAW_WEB_USAGE_WINDOW_DAYS": "90",
            "CLASSCLAW_LOG_LEVEL": "warning",
            "CLASSCLAW_SERVER_HOST": "127.0.0.1",
            "CLASSCLAW_SERVER_PORT": "9000",
            "CLASSCLAW_SERVER_ACCESS_LOG": "yes",
            "OPENCLAW_STATE_DIR": str(tmp_path / "legacy-openclaw-state"),
        },
    )

    assert loaded.features.file_analysis is False
    assert loaded.web.usage_window_days == 90
    assert loaded.log_level == "WARNING"
    assert loaded.server.host == "127.0.0.1"
    assert loaded.server.port == 9000
    assert loaded.server.access_log is True
    assert loaded.openclaw_state_dir == tmp_path / "legacy-openclaw-state"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("unknown = true\n", "Extra inputs are not permitted"),
        ('[runtime]\ntimezone = "Mars/Base"\n', "不是有效时区"),
        ('[openclaw]\ngateway_url = "localhost:18789"\n', "完整的 http:// 或 https:// URL"),
        ('[openclaw]\ngateway_url = "http://user:secret@localhost:18789"\n', "不能包含账号、密码"),
        (
            "[openclaw]\ntimeout_seconds = 120\n[web]\nai_request_timeout_seconds = 60\n",
            "不能小于 openclaw.timeout_seconds",
        ),
        (
            "[storage]\nattachment_dir = \"runtime/shared\"\nclass_workspace_root = \"runtime/shared/agents\"\n",
            "不能重叠",
        ),
        (
            "[openclaw]\ntimeout_seconds = 20\n[wechat]\ngateway_start_timeout_seconds = 30\n",
            "不能大于 openclaw.timeout_seconds",
        ),
    ],
)
def test_invalid_configuration_fails_before_startup(tmp_path, body, message):
    config = _write_config(tmp_path / "classclaw.toml", body)

    with pytest.raises(ConfigurationError, match=message):
        load_settings(config, environ={})


def test_explicit_missing_configuration_fails(tmp_path):
    missing = tmp_path / "missing.toml"

    with pytest.raises(ConfigurationError, match="指定的配置文件不存在"):
        load_settings(missing, environ={})


def test_invalid_environment_value_has_actionable_name(tmp_path):
    config = _write_config(tmp_path / "classclaw.toml", "")

    with pytest.raises(ConfigurationError, match="CLASSCLAW_FEATURE_REMINDERS"):
        load_settings(config, environ={"CLASSCLAW_FEATURE_REMINDERS": "maybe"})


def test_config_hash_does_not_depend_on_secret_values(tmp_path):
    config = _write_config(tmp_path / "classclaw.toml", "")

    first = load_settings(config, environ={"CLASSCLAW_API_TOKEN": "a" * 32, "CLASSCLAW_OPENCLAW_GATEWAY_TOKEN": "b" * 32})
    second = load_settings(config, environ={"CLASSCLAW_API_TOKEN": "c" * 32, "CLASSCLAW_OPENCLAW_GATEWAY_TOKEN": "d" * 32})

    assert first.config_hash == second.config_hash


def test_public_app_config_exposes_only_browser_safe_values(client):
    response = client.get("/api/v1/app-config", headers={"Authorization": ""})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    data = response.json()["data"]
    assert data["config_hash"] == settings.config_hash
    assert data["brand"]["name"] == settings.web.brand.name
    assert data["features"] == settings.features.model_dump()
    assert data["runtime"] == {"timezone": settings.timezone}
    assert data["semester_defaults"] == current_or_next_semester().as_json()
    assert "server" not in data
    serialized = response.text.lower()
    for forbidden in ("api_token", "gateway_token", "password", "database_url", "attachment_dir", "workspace_root", "channel"):
        assert forbidden not in serialized


def test_deployment_check_rejects_example_secrets_without_exposing_values():
    test_settings = replace(
        settings,
        api_token="replace-with-a-long-random-token",
        openclaw_gateway_token="replace-with-openclaw-gateway-token",
        default_admin_password="32767",
    )

    rows = _security_checks(test_settings)

    assert all(row["safe"] is False for row in rows)
    serialized = str(rows)
    assert "replace-with-a-long-random-token" not in serialized
    assert "replace-with-openclaw-gateway-token" not in serialized


def test_ice_environment_json_and_validation(tmp_path):
    config = _write_config(tmp_path / "classclaw.toml", "")
    loaded = load_settings(config, environ={"CLASSCLAW_CLASSROOM_MEDIA_ICE_SERVERS": '[{"urls":"stun:example.test:3478"}]'})
    assert loaded.classroom.media_ice_servers == [{"urls": "stun:example.test:3478"}]
    for value in ('not-json', '[{"urls":"http://example.test"}]', '{}'):
        with pytest.raises(ConfigurationError):
            load_settings(config, environ={"CLASSCLAW_CLASSROOM_MEDIA_ICE_SERVERS": value})
