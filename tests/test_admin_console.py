from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from sqlalchemy import func, select

from app.config import settings
from app.models.entities import AiUsageRecord, ClassAgentBinding, ClassRoom, Student, SystemSetting, User
from app.services import admin_console, logs as log_service, openclaw_provisioning, openclaw_usage
from app.utils.time import now


def _admin_header() -> dict[str, str]:
    assert settings.api_token
    return {"Authorization": f"Bearer {settings.api_token}", "X-ClassClaw-Surface": "web"}


def test_admin_console_overview_settings_and_usage(client, db):
    overview = client.get("/api/v1/admin/overview", headers=_admin_header())
    assert overview.status_code == 200
    assert overview.json()["data"]["openclaw"]["ready"] is True

    settings_response = client.get("/api/v1/admin/settings", headers=_admin_header())
    assert settings_response.status_code == 200
    keys = {item["key"] for item in settings_response.json()["data"]}
    assert {"feature.file_analysis", "admin.usage_window_days"} <= keys

    changed = client.put(
        "/api/v1/admin/settings/admin.usage_window_days",
        headers=_admin_header(),
        json={"value": 90},
    )
    assert changed.status_code == 200
    assert db.scalar(select(SystemSetting.value_json).where(SystemSetting.key == "admin.usage_window_days")) == 90

    db.add(AiUsageRecord(operation="event", model="openclaw/main", input_tokens=120, output_tokens=30, total_tokens=150, cached_input_tokens=20))
    db.commit()
    usage = client.get("/api/v1/admin/usage?days=7", headers=_admin_header())
    assert usage.status_code == 200
    assert usage.json()["data"]["totals"]["total_tokens"] == 150


def test_admin_creates_updates_and_assigns_class(client, db):
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "consoleteacher"}).json()["data"]
    created = client.post(
        "/api/v1/admin/classes",
        headers=_admin_header(),
        json={"name": "控制台班", "grade": "高二", "owner_user_id": user["id"], "provision_agent": False},
    )
    assert created.status_code == 201
    class_id = created.json()["data"]["class"]["id"]
    assert db.get(ClassRoom, class_id).owner_user_id == user["id"]
    assert db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == class_id)) is not None

    renamed = client.patch(f"/api/v1/classes/{class_id}", headers=_admin_header(), json={"name": "控制台班 2"})
    assert renamed.status_code == 200
    unassigned = client.patch(f"/api/v1/admin/classes/{class_id}/owner", headers=_admin_header(), json={"owner_user_id": None})
    assert unassigned.status_code == 200
    assert db.get(ClassRoom, class_id).owner_user_id is None


def test_feature_switch_blocks_the_feature(client, sample):
    cls, _other, students = sample
    disabled = client.put(
        "/api/v1/admin/settings/feature.event_ai",
        headers=_admin_header(),
        json={"value": False},
    )
    assert disabled.status_code == 200
    response = client.post(
        f"/api/v1/classes/{cls.id}/student-events/analyze",
        headers=_admin_header(),
        json={"student_id": students[0].id, "event_date": "2026-08-31", "content": "迟到"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_admin_initialize_deletes_class_data_and_preserves_accounts(client, db):
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "initialteacher"}).json()["data"]
    created = client.post(
        "/api/v1/admin/classes",
        headers=_admin_header(),
        json={"name": "待初始化班", "grade": "初一", "owner_user_id": user["id"], "provision_agent": False},
    ).json()["data"]
    cls = created["class"]
    db.add(Student(class_id=cls["id"], student_no="1", name="测试生", tags=[], status="active"))
    db.commit()

    response = client.post(
        "/api/v1/admin/system/initialize",
        headers=_admin_header(),
        json={"confirmation": "INITIALIZE"},
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["status"] == "completed"
    assert result["deleted_classes"] == 1
    assert db.scalar(select(func.count()).select_from(ClassRoom)) == 0
    assert db.scalar(select(func.count()).select_from(Student)) == 0
    assert db.get(User, user["id"]) is not None


def test_admin_edits_and_deletes_teacher_without_deleting_class(client, db):
    user = client.post("/api/v1/admin/users", headers=_admin_header(), json={"username": "oldname", "display_name": "旧名称"}).json()["data"]
    created = client.post(
        "/api/v1/admin/classes",
        headers=_admin_header(),
        json={"name": "保留数据班", "grade": "初二", "owner_user_id": user["id"], "provision_agent": False},
    ).json()["data"]["class"]

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
    assert db.get(ClassRoom, created["id"]) is not None
    assert db.get(ClassRoom, created["id"]).owner_user_id is None


def test_agent_studio_runtime_workspace_usage_and_logs(client, db, monkeypatch, tmp_path):
    test_settings = replace(settings, openclaw_class_workspace_root=tmp_path, openclaw_state_dir=tmp_path)
    monkeypatch.setattr(openclaw_provisioning, "settings", test_settings)
    monkeypatch.setattr(openclaw_usage, "settings", test_settings)
    created = client.post(
        "/api/v1/admin/classes", headers=_admin_header(),
        json={"name": "Agent Studio 班", "grade": "高一", "provision_agent": False},
    ).json()["data"]["class"]
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == created["id"]))
    binding.openclaw_agent_id = "classclaw-test-agent"
    binding.workspace_path = str(tmp_path / "agent-workspace")
    db.commit()
    patches: list[dict] = []

    async def fake_rpc(method: str, params: dict | None = None):
        if method == "config.get":
            return {
                "hash": "config-hash",
                "config": {"agents": {"list": [{
                    "id": binding.openclaw_agent_id, "workspace": binding.workspace_path,
                    "model": "provider/model-a", "thinkingDefault": "low",
                    "skills": ["classclaw-manager"], "tools": {"profile": "minimal"},
                }]}},
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

    settings_response = client.get(f"/api/v1/admin/openclaw/agents/{created['id']}/settings", headers=_admin_header())
    assert settings_response.status_code == 200
    data = settings_response.json()["data"]
    assert data["runtime"]["provider"] == "provider"
    agents_file = next(item for item in data["workspace"]["files"] if item["name"] == "AGENTS.md")

    workspace_update = client.put(
        f"/api/v1/admin/openclaw/agents/{created['id']}/workspace/AGENTS.md", headers=_admin_header(),
        json={"content": "# Custom\n专业配置。\n", "expected_sha256": agents_file["sha256"]},
    )
    assert workspace_update.status_code == 200
    openclaw_provisioning._prepare_workspace(db.get(ClassRoom, created["id"]), binding)
    assert (tmp_path / "agent-workspace" / "AGENTS.md").read_text(encoding="utf-8") == "# Custom\n专业配置。\n"

    runtime_update = client.patch(
        f"/api/v1/admin/openclaw/agents/{created['id']}", headers=_admin_header(),
        json={"model": "provider/model-a", "thinking_default": "high", "model_params": {"temperature": 0.2}},
    )
    assert runtime_update.status_code == 200
    patched_config = json.loads(patches[-1]["raw"])
    agent_config = patched_config["agents"]["list"][0]
    assert agent_config["thinkingDefault"] == "high"
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
    assert usage.json()["data"][0]["totals"]["totalTokens"] == 120
    assert usage.json()["data"][0]["calls"] == 1
    assert usage.json()["data"][0]["latency"]["p95Ms"] == 1250
    assert usage.json()["data"][0]["daily"][0]["model_calls"] == 1

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
