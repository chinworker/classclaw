from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from sqlalchemy import func, select

from app.config import settings
from app.database import Base
from app.models.entities import ClassAgentBinding, ClassRoom, Student, SystemSetting, User, UserSession
from app.models.usage import AiUsageRecord
from app.services import admin_console, openclaw_bridge, openclaw_provisioning, openclaw_usage
from app.services import logs as log_service
from app.utils.time import now


def _admin_header() -> dict[str, str]:
    assert settings.api_token
    return {"Authorization": f"Bearer {settings.api_token}", "X-ClassClaw-Surface": "web"}


def _create_class_fixture(db, *, name: str, grade: str, owner_user_id: str | None = None) -> ClassRoom:
    cls = ClassRoom(name=name, grade=grade, owner_user_id=owner_user_id)
    db.add(cls)
    db.flush()
    openclaw_provisioning.ensure_binding(db, cls)
    db.commit()
    return cls


def test_admin_console_overview_settings_and_usage(client, db, usage_db):
    overview = client.get("/api/v1/admin/overview", headers=_admin_header())
    assert overview.status_code == 200
    assert overview.json()["data"]["openclaw"]["ready"] is True

    settings_response = client.get("/api/v1/admin/settings", headers=_admin_header())
    assert settings_response.status_code == 200
    rows = settings_response.json()["data"]
    keys = {item["key"] for item in rows}
    assert {"feature.file_analysis", "admin.usage_window_days"} <= keys
    assert all(item["source"] == "startup_config" and item["restart_required"] is True for item in rows)

    changed = client.put(
        "/api/v1/admin/settings/admin.usage_window_days",
        headers=_admin_header(),
        json={"value": 90},
    )
    assert changed.status_code == 404
    assert db.scalar(select(SystemSetting.value_json).where(SystemSetting.key == "admin.usage_window_days")) is None

    usage_db.add(AiUsageRecord(operation="event", model="openclaw/main", input_tokens=120, output_tokens=30, total_tokens=150, cached_input_tokens=20))
    usage_db.commit()
    usage = client.get("/api/v1/admin/usage?days=7", headers=_admin_header())
    assert usage.status_code == 200
    assert usage.json()["data"]["totals"]["total_tokens"] == 150


def test_admin_class_creation_requires_onboarding(client, db):
    blocked = client.post(
        "/api/v1/admin/classes",
        headers=_admin_header(),
        json={"name": "控制台班", "grade": "高二"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "WEB_ONBOARDING_REQUIRED"
    assert blocked.json()["error"]["details"]["onboarding_url"] == "/app/#/onboarding"
    assert db.scalar(select(func.count()).select_from(ClassRoom)) == 0


def test_admin_updates_and_assigns_class(client, db):
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "consoleteacher"}).json()["data"]
    cls = _create_class_fixture(db, name="控制台班", grade="高二", owner_user_id=user["id"])
    class_id = cls.id
    assert db.get(ClassRoom, class_id).owner_user_id == user["id"]
    assert db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == class_id)) is not None

    renamed = client.patch(f"/api/v1/classes/{class_id}", headers=_admin_header(), json={"name": "控制台班 2"})
    assert renamed.status_code == 200
    unassigned = client.patch(f"/api/v1/admin/classes/{class_id}/owner", headers=_admin_header(), json={"owner_user_id": None})
    assert unassigned.status_code == 200
    assert db.get(ClassRoom, class_id).owner_user_id is None


def test_feature_switch_blocks_the_feature(client, sample, monkeypatch):
    cls, _other, students = sample
    test_settings = replace(
        settings,
        features=settings.features.model_copy(update={"event_ai": False}),
    )
    monkeypatch.setattr(admin_console, "settings", test_settings)
    response = client.post(
        f"/api/v1/classes/{cls.id}/student-events/analyze",
        headers=_admin_header(),
        json={"student_id": students[0].id, "event_date": "2026-08-31", "content": "迟到"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_legacy_database_setting_does_not_override_startup_config(client, db):
    db.add(SystemSetting(key="feature.file_analysis", value_json=False))
    db.commit()
    rows = client.get("/api/v1/admin/settings", headers=_admin_header()).json()["data"]
    value = next(item["value"] for item in rows if item["key"] == "feature.file_analysis")
    assert value is settings.features.file_analysis


def test_admin_initialize_factory_resets_everything_and_recreates_defaults(client, db, usage_db, monkeypatch, tmp_path):
    test_settings = replace(
        settings,
        attachment_dir=tmp_path / "attachments",
        log_file=tmp_path / "logs" / "classclaw.log",
        openclaw_class_workspace_root=tmp_path / "workspaces",
        openclaw_state_dir=tmp_path / "openclaw-state",
    )
    monkeypatch.setattr(admin_console, "settings", test_settings)
    monkeypatch.setattr(openclaw_bridge, "settings", test_settings)
    monkeypatch.setattr(openclaw_provisioning, "settings", test_settings)

    async def extractor_ready(*, force: bool = False) -> bool:
        return True

    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_ready)
    runtime_patches: list[dict] = []

    async def fake_rpc(method: str, params: dict | None = None):
        if method == "config.get":
            return {
                "hash": "reset-hash",
                "config": {
                    "agents": {"list": [
                        {"id": test_settings.openclaw_agent_id},
                        {"id": test_settings.openclaw_extractor_agent_id},
                        {"id": "classclaw-reset-agent"},
                    ]},
                    "bindings": [
                        {"agentId": "classclaw-reset-agent", "match": {"channel": test_settings.openclaw_wechat_channel, "accountId": "reset-account"}},
                    ],
                    "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {"classclaw-reset-agent": "pending"}}}}},
                },
            }
        if method == "channels.logout":
            return {"cleared": True, "loggedOut": True}
        if method == "config.patch":
            runtime_patches.append(params or {})
            return {"ok": True}
        raise AssertionError(method)

    async def cancel_wechat(class_id: str, action: str, **params):
        assert action == "cancel"
        return {"cancelled": True}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_rpc)
    monkeypatch.setattr("app.services.wechat_login.call", cancel_wechat)
    login = client.post("/api/v1/auth/login", json={"username": settings.default_admin_username, "password": settings.default_admin_password})
    assert login.status_code == 200
    old_admin = db.scalar(select(User).where(User.role == "admin"))
    old_admin_id = old_admin.id
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "initialteacher"}).json()["data"]
    cls = _create_class_fixture(db, name="待初始化班", grade="初一", owner_user_id=user["id"])
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == cls.id))
    binding.openclaw_agent_id = "classclaw-reset-agent"
    binding.channel_account_id = "reset-account"
    db.add(Student(class_id=cls.id, student_no="1", name="测试生", tags=[], status="active"))
    db.add(SystemSetting(key="feature.file_analysis", value_json=False))
    usage_db.add(AiUsageRecord(operation="reset-test", model="openclaw/main", input_tokens=10, output_tokens=5, total_tokens=15))
    usage_db.commit()
    db.commit()
    assert db.scalar(select(func.count()).select_from(UserSession)) == 1
    test_settings.attachment_dir.mkdir(parents=True, exist_ok=True)
    (test_settings.attachment_dir / "old.txt").write_text("old attachment", encoding="utf-8")
    test_settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    test_settings.log_file.write_text("old log marker\n", encoding="utf-8")
    for agent_id in (test_settings.openclaw_agent_id, test_settings.openclaw_extractor_agent_id):
        sessions = test_settings.openclaw_state_dir / "agents" / agent_id / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "old.jsonl").write_text("old session", encoding="utf-8")
    class_agent_root = test_settings.openclaw_state_dir / "agents" / "classclaw-reset-agent"
    class_agent_root.mkdir(parents=True, exist_ok=True)
    (class_agent_root / "old-state.json").write_text("old", encoding="utf-8")

    response = client.post(
        "/api/v1/admin/system/initialize",
        headers=_admin_header(),
        json={"confirmation": "INITIALIZE"},
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["status"] == "completed"
    assert result["deleted_counts"]["ai_usage_records"] == 1
    assert usage_db.scalar(select(func.count()).select_from(AiUsageRecord)) == 0
    assert result["deleted_classes"] == 1
    assert [item["kind"] for item in result["default_agents"]] == ["main", "extractor"]
    patched_agents = json.loads(runtime_patches[0]["raw"])["agents"]["list"]
    assert [item["id"] for item in patched_agents] == [test_settings.openclaw_agent_id, test_settings.openclaw_extractor_agent_id]
    for table in Base.metadata.sorted_tables:
        expected = 1 if table.name == "users" else 0
        assert db.scalar(select(func.count()).select_from(table)) == expected
    new_admin = db.scalar(select(User).where(User.role == "admin"))
    assert new_admin.id != old_admin_id
    assert new_admin.username == settings.default_admin_username
    assert db.get(User, user["id"]) is None
    assert not (test_settings.attachment_dir / "old.txt").exists()
    assert "old log marker" not in test_settings.log_file.read_text(encoding="utf-8")
    assert "ClassClaw 输入清洗规范" in (test_settings.openclaw_class_workspace_root / "_extractor" / "AGENTS.md").read_text(encoding="utf-8")
    for agent_id in (test_settings.openclaw_agent_id, test_settings.openclaw_extractor_agent_id):
        assert not (test_settings.openclaw_state_dir / "agents" / agent_id / "sessions").exists()
    assert not class_agent_root.exists()


def test_admin_initialize_keeps_database_when_openclaw_cleanup_fails(client, db, monkeypatch, tmp_path):
    test_settings = replace(
        settings,
        attachment_dir=tmp_path / "attachments",
        log_file=tmp_path / "logs" / "classclaw.log",
        openclaw_class_workspace_root=tmp_path / "workspaces",
        openclaw_state_dir=tmp_path / "openclaw-state",
    )
    monkeypatch.setattr(admin_console, "settings", test_settings)
    monkeypatch.setattr(openclaw_bridge, "settings", test_settings)
    monkeypatch.setattr(openclaw_provisioning, "settings", test_settings)

    async def extractor_ready(*, force: bool = False) -> bool:
        return True

    async def unavailable_rpc(method: str, params: dict | None = None):
        raise RuntimeError("gateway unavailable")

    async def cancel_wechat(class_id: str, action: str, **params):
        assert action == "cancel"
        return {"cancelled": True}

    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_ready)
    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", unavailable_rpc)
    monkeypatch.setattr("app.services.wechat_login.call", cancel_wechat)
    assert client.post(
        "/api/v1/auth/login",
        json={"username": settings.default_admin_username, "password": settings.default_admin_password},
    ).status_code == 200
    cls = _create_class_fixture(db, name="初始化失败保留班", grade="高一")
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == cls.id))
    binding.openclaw_agent_id = "classclaw-unavailable-agent"
    db.commit()
    from pathlib import Path
    marker = Path(binding.workspace_path) / "preserve.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep workspace on failure")
    attachment = test_settings.attachment_dir / "preserve.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("keep attachment on failure")

    response = client.post(
        "/api/v1/admin/system/initialize",
        headers=_admin_header(),
        json={"confirmation": "INITIALIZE"},
    )
    assert marker.read_text() == "keep workspace on failure"
    assert attachment.read_text() == "keep attachment on failure"
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SYSTEM_INITIALIZATION_INCOMPLETE"
    assert response.json()["error"]["details"]["database_preserved_for_retry"] is True
    assert db.get(ClassRoom, cls.id) is not None
    assert db.scalar(select(func.count()).select_from(User)) >= 1


def test_admin_edits_and_deletes_teacher_without_deleting_class(client, db):
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "oldname", "display_name": "旧名称"}).json()["data"]
    cls = _create_class_fixture(db, name="保留数据班", grade="初二", owner_user_id=user["id"])

    changed = client.patch(
        f"/api/v1/admin/users/{user['id']}", headers=_admin_header(),
        json={"username": "newname", "display_name": "新名称", "is_active": True},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["username"] == "newname"
    assert client.post("/api/v1/auth/login", json={"username": "oldname", "password": "32767"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "newname", "password": "32767"}).status_code == 200

    deleted = client.delete(f"/api/v1/admin/users/{user['id']}", headers=_admin_header())
    assert deleted.status_code == 200
    assert db.get(User, user["id"]) is None
    assert db.get(ClassRoom, cls.id) is not None
    assert db.get(ClassRoom, cls.id).owner_user_id is None


def test_agent_studio_runtime_workspace_usage_and_logs(client, db, monkeypatch, tmp_path):
    test_settings = replace(settings, openclaw_class_workspace_root=tmp_path, openclaw_state_dir=tmp_path)
    monkeypatch.setattr(admin_console, "settings", test_settings)
    monkeypatch.setattr(openclaw_bridge, "settings", test_settings)
    monkeypatch.setattr(openclaw_provisioning, "settings", test_settings)
    monkeypatch.setattr(openclaw_usage, "settings", test_settings)
    cls = _create_class_fixture(db, name="Agent Studio 班", grade="高一")
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == cls.id))
    binding.openclaw_agent_id = "classclaw-test-agent"
    binding.workspace_path = str(tmp_path / "agent-workspace")
    db.commit()
    patches: list[dict] = []
    runtime_rows = [
        {
            "id": binding.openclaw_agent_id, "workspace": binding.workspace_path,
            "model": "provider/model-a", "thinkingDefault": "low",
            "skills": ["classclaw-manager"], "tools": {"profile": "minimal"},
        },
        {"id": test_settings.openclaw_agent_id, "workspace": str(tmp_path / "main"), "model": "provider/model-a"},
        {"id": test_settings.openclaw_extractor_agent_id, "workspace": str(tmp_path / "_extractor"), "model": "provider/model-a"},
    ]

    async def fake_rpc(method: str, params: dict | None = None):
        if method == "config.get":
            return {
                "hash": "config-hash",
                "config": {"agents": {"defaults": {"workspace": str(tmp_path / "main")}, "list": runtime_rows}},
            }
        if method == "models.list":
            return {"models": [{"id": "provider/model-a", "name": "Model A", "provider": "provider", "available": True}]}
        if method == "config.patch":
            patches.append(params or {})
            return {"ok": True}
        if method == "agents.update":
            return {"ok": True}
        if method == "usage.cost":
            return {"totals": {"input": 100, "output": 20, "totalTokens": 120, "totalCost": 0.01}, "daily": []}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_rpc)
    admin_console._agent_usage_cache.clear()

    catalog_response = client.get("/api/v1/admin/openclaw/agents/catalog", headers=_admin_header())
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()["data"]
    assert [item["kind"] for item in catalog[:2]] == ["main", "extractor"]
    assert {item["kind"] for item in catalog} == {"main", "extractor", "class"}

    main_settings = client.get("/api/v1/admin/openclaw/agents/main/settings", headers=_admin_header())
    assert main_settings.status_code == 200
    assert main_settings.json()["data"]["agent"]["agent_id"] == test_settings.openclaw_agent_id
    main_agents_file = next(item for item in main_settings.json()["data"]["workspace"]["files"] if item["name"] == "AGENTS.md")
    assert main_agents_file["resettable"] is False
    main_update = client.put(
        "/api/v1/admin/openclaw/agents/main/workspace/AGENTS.md", headers=_admin_header(),
        json={"content": "# Main custom\n", "expected_sha256": main_agents_file["sha256"]},
    )
    assert main_update.status_code == 200
    main_reset = client.post("/api/v1/admin/openclaw/agents/main/workspace/AGENTS.md/reset", headers=_admin_header(), json={})
    assert main_reset.status_code == 409

    extractor_settings = client.get("/api/v1/admin/openclaw/agents/extractor/settings", headers=_admin_header())
    assert extractor_settings.status_code == 200
    extractor_agents_file = next(item for item in extractor_settings.json()["data"]["workspace"]["files"] if item["name"] == "AGENTS.md")
    extractor_update = client.put(
        "/api/v1/admin/openclaw/agents/extractor/workspace/AGENTS.md", headers=_admin_header(),
        json={"content": "# Extractor custom\n", "expected_sha256": extractor_agents_file["sha256"]},
    )
    assert extractor_update.status_code == 200
    extractor_reset = client.post("/api/v1/admin/openclaw/agents/extractor/workspace/AGENTS.md/reset", headers=_admin_header(), json={})
    assert extractor_reset.status_code == 200
    assert "ClassClaw 输入清洗规范" in extractor_reset.json()["data"]["content"]

    settings_response = client.get(f"/api/v1/admin/openclaw/agents/{cls.id}/settings", headers=_admin_header())
    assert settings_response.status_code == 200
    data = settings_response.json()["data"]
    assert data["runtime"]["provider"] == "provider"
    agents_file = next(item for item in data["workspace"]["files"] if item["name"] == "AGENTS.md")

    workspace_update = client.put(
        f"/api/v1/admin/openclaw/agents/{cls.id}/workspace/AGENTS.md", headers=_admin_header(),
        json={"content": "# Custom\n专业配置。\n", "expected_sha256": agents_file["sha256"]},
    )
    assert workspace_update.status_code == 200
    openclaw_provisioning._prepare_workspace(db.get(ClassRoom, cls.id), binding)
    assert (tmp_path / "agent-workspace" / "AGENTS.md").read_text(encoding="utf-8") == "# Custom\n专业配置。\n"

    runtime_update = client.patch(
        f"/api/v1/admin/openclaw/agents/{cls.id}", headers=_admin_header(),
        json={"model": "provider/model-a", "thinking_default": "high", "model_params": {"temperature": 0.2}},
    )
    assert runtime_update.status_code == 409
    assert runtime_update.json()["error"]["code"] == "CLASS_AGENT_THINKING_MANAGED"
    runtime_update = client.patch(
        f"/api/v1/admin/openclaw/agents/{cls.id}", headers=_admin_header(),
        json={"model": "provider/model-a", "model_params": {"temperature": 0.2}},
    )
    assert runtime_update.status_code == 200
    patched_config = json.loads(patches[-1]["raw"])
    agent_config = next(item for item in patched_config["agents"]["list"] if item["id"] == binding.openclaw_agent_id)
    assert agent_config["thinkingDefault"] == "low"
    assert agent_config["params"]["temperature"] == 0.2

    session_dir = tmp_path / "agents" / binding.openclaw_agent_id / "sessions"
    session_dir.mkdir(parents=True)
    today = now().date().isoformat()
    transcript = [
        {"type": "message", "timestamp": f"{today}T09:00:00+08:00", "message": {"role": "user", "content": [{"type": "text", "text": "not retained"}]}},
        {"type": "message", "timestamp": f"{today}T09:00:02+08:00", "message": {"role": "assistant", "durationMs": 1250, "usage": {"input": 100, "output": 20}, "content": [{"type": "text", "text": "not retained"}]}},
    ]
    (session_dir / "session.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in transcript), encoding="utf-8")
    usage = client.get("/api/v1/admin/usage/agents?days=30", headers=_admin_header())
    assert usage.status_code == 200
    usage_rows = usage.json()["data"]
    assert {item["kind"] for item in usage_rows} == {"main", "extractor", "class"}
    class_usage = next(item for item in usage_rows if item["kind"] == "class")
    assert class_usage["totals"]["totalTokens"] == 120
    assert class_usage["calls"] == 1
    assert class_usage["latency"]["p95Ms"] == 1250
    assert class_usage["latency"]["p50Ms"] == 1250
    assert class_usage["daily"][0]["model_calls"] == 1

    log_path = tmp_path / "classclaw.log"
    log_path.write_text(json.dumps({"timestamp": "2026-08-31T10:00:00+08:00", "level": "ERROR", "logger": "classclaw.test", "message": "example failure"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(log_service, "settings", SimpleNamespace(log_file=log_path))
    logs = client.get("/api/v1/admin/logs?source=classclaw&level=ERROR&q=example", headers=_admin_header())
    assert logs.status_code == 200
    assert logs.json()["data"]["items"][0]["message"] == "example failure"
    limited = log_service.openclaw_logs_payload(
        {"lines": [json.dumps({"level": "INFO", "message": f"line {index}"}) for index in range(10)]},
        limit=2,
    )
    assert len(limited["items"]) == 2
