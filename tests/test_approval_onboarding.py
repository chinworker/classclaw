import asyncio
import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.errors import AppError
from app.models.entities import (
    AttendanceRecord,
    BaseTimetable,
    ClassAgentBinding,
    ClassOnboardingSession,
    ClassPeriod,
    ClassRoom,
    ClassSubject,
    InteractionAnalysis,
    Student,
    WriteProposal,
)
from app.schemas.domain import ClassOnboardingUpdate
from app.services import approval as approval_service
from app.services import openclaw_bridge, openclaw_provisioning


def test_class_onboarding_is_draft_until_explicit_confirmation(client, db):
    started = client.post("/api/v1/class-onboarding/sessions", json={"created_by": "teacher-1"})
    assert started.status_code == 201
    session = started.json()["data"]
    draft = {
        "class_info": {"name": "高一(3)班", "grade": "高一", "head_teacher": "李老师", "room": "303"},
        "students": [
            {"student_no": "001", "name": "张三", "gender": "男"},
            {"student_no": "002", "name": "李四", "gender": "女"},
        ],
        "periods": [{"period_no": 1, "name": None, "sort_order": 1}],
        "base_timetable": [
            {"weekday": 1, "period_no": 1, "subject": "语文", "teacher": "李老师"},
            {"weekday": 2, "period_no": 1, "subject": "数学", "teacher": "王老师"},
        ],
    }
    updated = client.patch(
        f"/api/v1/class-onboarding/sessions/{session['id']}",
        json={"expected_revision": session["revision"], "current_step": "review", "draft_patch": draft, "field_evidence_patch": {"class_info.name": {"source_type": "file", "location": "名单.xlsx!A1", "confidence": 0.98}}},
    )
    assert updated.status_code == 200
    updated_session = updated.json()["data"]
    preview = client.post(f"/api/v1/class-onboarding/sessions/{session['id']}/preview", json={"requested_by": "teacher-1"})
    assert preview.status_code == 201
    proposal = preview.json()["data"]
    assert proposal["preview_json"]["ready"] is True
    assert proposal["preview_json"]["summary"]["student_count"] == 2
    assert db.scalar(select(func.count(ClassRoom.id))) == 0
    assert db.scalar(select(func.count(Student.id))) == 0
    confirmed = client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm", json={"revision": proposal["revision"], "confirmed_by": "teacher-1", "confirmation_note": "网页端已核对"})
    assert confirmed.status_code == 200
    result = confirmed.json()["data"]["result_json"]
    assert result["student_count"] == 2 and result["subject_count"] == 2
    assert db.scalar(select(func.count(ClassRoom.id))) == 1
    assert db.scalar(select(func.count(Student.id))) == 2
    assert db.scalar(select(func.count(ClassSubject.id))) == 2
    assert db.scalar(select(func.count(ClassAgentBinding.id))) == 1
    assert db.scalar(select(ClassPeriod.name)) is None
    assert {row.room for row in db.scalars(select(BaseTimetable))} == {"303"}
    assert db.get(ClassOnboardingSession, session["id"]).status == "completed"


def test_incomplete_onboarding_cannot_commit(client, db):
    started = client.post("/api/v1/class-onboarding/sessions", json={"initial_draft": {"class_info": {"name": "缺少年级"}}}).json()["data"]
    proposal = client.post(f"/api/v1/class-onboarding/sessions/{started['id']}/preview", json={}).json()["data"]
    assert proposal["preview_json"]["ready"] is False
    response = client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm", json={"revision": 1, "confirmed_by": "teacher-1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PENDING_CONFIRMATION_REQUIRED"
    assert db.scalar(select(func.count(ClassRoom.id))) == 0


def test_web_onboarding_wizard_is_served(client):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "ClassClaw · 班主任工作台" in response.text
    assert 'type="module" src="./app.js' in response.text
    onboarding = client.get("/app/js/pages/onboarding.js")
    assert onboarding.status_code == 200
    assert '"创建班级"' in onboarding.text
    assert "确认并创建班级" not in onboarding.text
    assert "我已核对班级、名单、课表和科目" not in onboarding.text
    assert "commitBtn.disabled" in onboarding.text
    assert "班级专属助手" in onboarding.text
    assert "暂不绑定" in onboarding.text
    assert "/agent-binding/provision" in onboarding.text
    assert "初始座位" not in onboarding.text
    assert "seatMapEditor" not in onboarding.text
    assert "自定义名称" not in onboarding.text
    assert "再次输入完整班级名称" not in onboarding.text
    assert "manualStart: true" in onboarding.text
    assert "取消解析" in client.get("/app/js/components.js").text
    components = client.get("/app/js/components.js")
    assert components.status_code == 200
    assert "export function aiButton" in components.text
    assert "ai-button-timer" in components.text
    assert "window.setInterval(updateClock, 100)" in components.text
    assert "/agent-binding/wait" in components.text
    assert "我已扫码，检查绑定" not in components.text


def test_class_agent_can_be_provisioned_without_wechat(client, db, sample, tmp_path, monkeypatch):
    cls, _, _ = sample
    monkeypatch.setattr(
        openclaw_provisioning,
        "settings",
        SimpleNamespace(openclaw_class_workspace_root=tmp_path, openclaw_wechat_channel="openclaw-weixin"),
    )
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params or {}))
        if method == "agents.list":
            return {"agents": []}
        if method == "agents.create":
            return {"agentId": f"classclaw-{cls.id}"}
        if method == "agents.update":
            return {"ok": True}
        if method == "config.get":
            return {"hash": "config-hash", "config": {"agents": {"list": []}, "bindings": []}}
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{cls.id}/agent-binding/provision", json={})
    assert response.status_code == 200
    binding = response.json()["data"]
    assert binding["status"] == "agent_created"
    assert binding["channel_account_id"] is None
    assert not any(method.startswith("web.login") for method, _ in calls)
    patch = next(params for method, params in calls if method == "config.patch")
    runtime = __import__("json").loads(patch["raw"])
    assert "bindings" not in runtime
    assert runtime["agents"]["list"][0]["skills"] == ["classclaw-manager"]
    assert runtime["plugins"]["entries"]["classclaw"]["config"]["agentClasses"][f"classclaw-{cls.id}"] == cls.id
    assert (tmp_path / cls.id / "SOUL.md").is_file()
    assert (tmp_path / cls.id / "IDENTITY.md").is_file()


def test_onboarding_name_check_reports_existing_class_and_legacy_agent(client, db, monkeypatch):
    db.add(ClassRoom(name="高一三班", grade="高一"))
    db.commit()

    async def rpc(method, params=None):
        assert method == "agents.list"
        return {"agents": [{"id": "高一三班", "name": "高一三班专属智能体", "workspace": "/tmp/legacy-agent"}]}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.get("/api/v1/class-onboarding/name-check", params={"class_name": " 高一三班 "})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["available"] is False
    assert result["class_conflicts"][0]["name"] == "高一三班"
    assert result["agent_conflicts"][0]["agent_id"] == "高一三班"


def test_duplicate_class_name_is_rejected_when_onboarding_starts(client, db):
    db.add(ClassRoom(name="高一三班", grade="高一"))
    db.commit()
    response = client.post(
        "/api/v1/class-onboarding/sessions",
        json={"initial_draft": {"class_info": {"name": "高一三班", "grade": "高一"}}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASS_NAME_CONFLICT"


def test_onboarding_edit_invalidates_previous_preview(client):
    started = client.post(
        "/api/v1/class-onboarding/sessions",
        json={"initial_draft": {"class_info": {"name": "初始班", "grade": "高一"}, "students": [{"student_no": "1", "name": "甲"}], "base_timetable": [{"weekday": 1, "period_no": 1, "subject": "语文"}]}},
    ).json()["data"]
    proposal = client.post(f"/api/v1/class-onboarding/sessions/{started['id']}/preview", json={}).json()["data"]
    updated = client.patch(
        f"/api/v1/class-onboarding/sessions/{started['id']}",
        json={"expected_revision": started["revision"], "draft_patch": {"class_info": {"name": "修改后的班"}}},
    )
    assert updated.status_code == 200
    stale = client.post(
        f"/api/v1/write-proposals/{proposal['id']}/confirm",
        json={"revision": proposal["revision"], "confirmed_by": "teacher-1"},
    )
    assert stale.status_code == 409
    assert "重新生成" in stale.json()["error"]["message"]


def test_deterministic_api_remains_available_when_openclaw_is_disconnected(client, monkeypatch):
    async def disconnected(force: bool = False):
        return {"ready": False, "gateway_live": True, "plugin_ready": False, "error": "plugin missing"}

    monkeypatch.setattr(openclaw_bridge, "connection_status", disconnected)
    available = client.get("/api/v1/classes")
    assert available.status_code == 200
    status = client.get("/api/v1/openclaw/status?refresh=true")
    assert status.status_code == 200
    assert status.json()["data"]["ready"] is False


def test_onboarding_file_is_processed_by_openclaw_and_fills_draft(client, monkeypatch):
    started = client.post("/api/v1/class-onboarding/sessions", json={"initial_draft": {"class_info": {"name": "高一三班", "grade": "高一"}}}).json()["data"]

    async def analyze(db, session_id, target_section, attachments, expected_revision, *, cancelled=None):
        assert target_section == "students"
        assert attachments[0].original_name == "名单.csv"
        assert cancelled is not None
        updated = approval_service.update_onboarding(
            db,
            session_id,
            ClassOnboardingUpdate(
                expected_revision=expected_revision,
                current_step="students",
                draft_patch={"students": [{"student_no": "001", "name": "张三"}]},
                field_evidence_patch={"students.001": {"source_type": "file", "attachment_id": attachments[0].id, "confidence": 0.99}},
            ),
        )
        return {
            "session": updated,
            "attachments": attachments,
            "analysis": {
                "accepted": True,
                "summary": "识别 1 名学生",
                "warnings": [],
                "confidence": 0.99,
                "reasons": ["学号和姓名均清晰"],
            },
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_onboarding_files", analyze)
    response = client.post(
        f"/api/v1/class-onboarding/sessions/{started['id']}/files",
        data={"target_section": "students", "expected_revision": str(started["revision"])},
        files={"files": ("名单.csv", "学号,姓名\n001,张三\n", "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["session"]["draft_json"]["students"][0]["name"] == "张三"
    assert payload["analysis"]["confidence"] == 0.99


def test_reimport_uses_fresh_openclaw_session_and_replaces_old_rows(db, monkeypatch):
    onboarding = approval_service.create_onboarding(db, type("Input", (), {
        "initial_draft": {"class_info": {"name": "高一三班", "grade": "高一"}, "students": [{"student_no": "001", "name": "旧学生"}]},
        "field_evidence": {}, "created_by": "teacher", "source_message_id": None,
    })())
    users = []

    async def responses(_prompt, *, user, attachments=None, max_output_tokens=8000, db=None):
        users.append(user)
        return {
            "draft_patch": {"students": [{"student_no": "002", "name": "新学生"}]},
            "warnings": [],
            "confidence": 0.99,
            "reasons": ["每行学号和姓名均清晰且唯一"],
            "summary": "仅本次文件",
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    first = __import__("asyncio").run(openclaw_bridge.analyze_onboarding_files(db, onboarding.id, "students", [], onboarding.revision))
    second = __import__("asyncio").run(openclaw_bridge.analyze_onboarding_files(db, onboarding.id, "students", [], first["session"].revision))
    assert second["session"].draft_json["students"] == [{"student_no": "002", "name": "新学生"}]
    assert users[0] != users[1]
    assert all(value.startswith("classclaw-onboarding-import-") for value in users)


def test_cancelled_onboarding_analysis_does_not_update_draft(db, monkeypatch):
    onboarding = approval_service.create_onboarding(
        db,
        type(
            "Input",
            (),
            {
                "initial_draft": {
                    "class_info": {"name": "高一三班", "grade": "高一"},
                    "students": [{"student_no": "001", "name": "旧学生"}],
                },
                "field_evidence": {},
                "created_by": "teacher",
                "source_message_id": None,
            },
        )(),
    )

    async def responses(_prompt, *, user, attachments=None, max_output_tokens=8000, db=None):
        return {
            "draft_patch": {"students": [{"student_no": "002", "name": "新学生"}]},
            "warnings": [],
            "confidence": 0.99,
            "reasons": ["每行学号和姓名均清晰且唯一"],
            "summary": "已识别",
        }

    async def cancelled() -> bool:
        return True

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    with pytest.raises(AppError) as captured:
        asyncio.run(
            openclaw_bridge.analyze_onboarding_files(
                db,
                onboarding.id,
                "students",
                [],
                onboarding.revision,
                cancelled=cancelled,
            )
        )

    assert captured.value.code == "REQUEST_CANCELLED"
    db.refresh(onboarding)
    assert onboarding.revision == 1
    assert onboarding.draft_json["students"] == [{"student_no": "001", "name": "旧学生"}]


def test_cancellation_stops_inflight_openclaw_operation():
    operation_cancelled = False

    async def slow_operation() -> dict:
        nonlocal operation_cancelled
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            operation_cancelled = True
            raise
        return {}

    async def cancelled() -> bool:
        return True

    async def run() -> None:
        with pytest.raises(AppError) as captured:
            await openclaw_bridge._await_unless_cancelled(slow_operation(), cancelled)
        assert captured.value.code == "REQUEST_CANCELLED"

    asyncio.run(run())
    assert operation_cancelled is True


def test_onboarding_pasted_text_is_rejected_in_file_first_flow(client):
    started = client.post("/api/v1/class-onboarding/sessions", json={"initial_draft": {"class_info": {"name": "高一三班", "grade": "高一"}}}).json()["data"]

    response = client.post(
        f"/api/v1/class-onboarding/sessions/{started['id']}/analyze-text",
        json={"target_section": "students", "expected_revision": started["revision"], "text": "001 张三"},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "FILE_UPLOAD_REQUIRED"


def test_class_creation_routes_reject_non_web_call(client):
    response = client.post("/api/v1/class-onboarding/sessions", headers={"X-ClassClaw-Surface": "agent"}, json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WEB_ONBOARDING_REQUIRED"


def test_class_specific_agent_and_wechat_binding_flow(client, db, tmp_path, monkeypatch):
    started = client.post("/api/v1/class-onboarding/sessions", json={"initial_draft": {
        "class_info": {"name": "高二一班", "grade": "高二", "room": "201"},
        "students": [{"student_no": "001", "name": "张三"}],
        "periods": [{"period_no": 1, "name": "第一节"}],
        "base_timetable": [{"weekday": 1, "period_no": 1, "subject": "语文"}],
    }}).json()["data"]
    proposal = client.post(f"/api/v1/class-onboarding/sessions/{started['id']}/preview", json={}).json()["data"]
    completed = client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm", json={"revision": 1, "confirmed_by": "班主任"}).json()["data"]
    class_id = completed["result_json"]["class_id"]
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == class_id))
    binding.workspace_path = str(tmp_path / "class-agent")
    db.commit()
    monkeypatch.setattr(openclaw_provisioning, "settings", replace(settings, openclaw_class_workspace_root=tmp_path))
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params or {}))
        if method == "agents.list":
            return {"agents": []}
        if method == "agents.create":
            return {"ok": True, "agentId": "class-agent-1", "name": "高二一班专属智能体", "workspace": binding.workspace_path}
        if method == "agents.update":
            return {"ok": True}
        if method == "web.login.start":
            return {"connected": False, "accountId": "class-wx-1", "qrDataUrl": "https://weixin.example/login/session-1", "message": "请扫码"}
        if method == "web.login.wait":
            return {"connected": True, "accountId": "class-wx-1", "message": "已连接"}
        if method == "config.get":
            return {"hash": "config-hash", "config": {"bindings": []}}
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    started_binding = client.post(f"/api/v1/classes/{class_id}/agent-binding/start", json={"force": False})
    assert started_binding.status_code == 200
    binding_result = started_binding.json()["data"]
    assert binding_result["qr_data_url"].startswith("data:image/png;base64,")
    assert binding_result["qr_content"] == "https://weixin.example/login/session-1"
    assert base64.b64decode(binding_result["qr_data_url"].split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")
    workspace_files = {path.name for path in (tmp_path / "class-agent").iterdir()}
    assert {"AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md", "USER.md", "HEARTBEAT.md"} <= workspace_files
    assert "ClassClaw 助理" in (tmp_path / "class-agent" / "IDENTITY.md").read_text()
    assert "Fill this in" not in (tmp_path / "class-agent" / "IDENTITY.md").read_text()
    assert sum((tmp_path / "class-agent" / name).stat().st_size for name in workspace_files) < 3000
    assert class_id in (tmp_path / "class-agent" / ".classclaw-agent.json").read_text()
    waited = client.post(f"/api/v1/classes/{class_id}/agent-binding/wait", json={"current_qr_data_url": binding_result["qr_content"]})
    assert waited.status_code == 200
    assert waited.json()["data"]["connected"] is True
    assert waited.json()["data"]["route_ready"] is True
    db.refresh(binding)
    assert binding.status == "linked" and binding.openclaw_agent_id == "class-agent-1"
    create_call = next(params for method, params in calls if method == "agents.create")
    assert create_call["name"] == f"classclaw-{class_id}"
    assert "高二一班" not in create_call["name"]
    patch = next(params for method, params in calls if method == "config.patch")
    assert '"agentId": "class-agent-1"' in patch["raw"]
    wait_call = next(params for method, params in calls if method == "web.login.wait")
    assert wait_call["currentQrDataUrl"].startswith("data:image/png;base64,")
    runtime_config = __import__("json").loads(patch["raw"])["agents"]["list"][0]
    assert runtime_config["contextInjection"] == "continuation-skip"
    assert runtime_config["thinkingDefault"] == "off"
    assert runtime_config["tools"]["profile"] == "minimal"
    assert runtime_config["skills"] == ["classclaw-manager"]
    update_call = next(params for method, params in calls if method == "agents.update")
    assert update_call["name"].startswith("ClassClaw 助理 · ")
    assert update_call["name"] != "高二一班"


def test_linked_binding_check_repairs_missing_openclaw_route(client, db, sample, monkeypatch):
    cls, _, _ = sample
    binding = ClassAgentBinding(
        class_id=cls.id,
        agent_name=f"classclaw-{cls.id}",
        openclaw_agent_id=f"classclaw-{cls.id}",
        workspace_path=f"/tmp/classclaw-test/{cls.id}",
        channel_id="openclaw-weixin",
        channel_account_id="wx-account-1",
        status="linked",
    )
    db.add(binding)
    db.commit()
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params or {}))
        if method == "config.get":
            return {"hash": "missing-route", "config": {"bindings": [], "agents": {"list": []}}}
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{cls.id}/agent-binding/wait", json={})
    assert response.status_code == 200
    assert response.json()["data"]["connected"] is True
    assert response.json()["data"]["route_ready"] is True
    assert [method for method, _ in calls] == ["config.get", "config.patch"]
    patch = __import__("json").loads(calls[-1][1]["raw"])
    assert patch["bindings"][0]["agentId"] == binding.openclaw_agent_id
    assert patch["plugins"]["entries"]["classclaw"]["config"]["agentClasses"][binding.openclaw_agent_id] == cls.id


def test_binding_recovers_agent_created_before_local_id_was_saved(client, db, tmp_path, monkeypatch):
    cls = ClassRoom(name="高二二班", grade="高二")
    db.add(cls)
    db.flush()
    workspace = tmp_path / cls.id
    binding = ClassAgentBinding(
        class_id=cls.id,
        agent_name="高二二班专属智能体",
        workspace_path=str(workspace),
        channel_id="openclaw-weixin",
        status="failed",
        last_error='agent "高二二班" already exists',
    )
    stale_class = ClassRoom(name="历史错误映射班", grade="高二")
    db.add(stale_class)
    db.flush()
    stale_binding = ClassAgentBinding(
        class_id=stale_class.id,
        agent_name="旧智能体",
        openclaw_agent_id=f"classclaw-{cls.id}",
        workspace_path=str(tmp_path / stale_class.id),
        channel_id="openclaw-weixin",
        status="awaiting_qr",
    )
    db.add_all([binding, stale_binding])
    db.commit()
    monkeypatch.setattr(openclaw_provisioning, "settings", replace(settings, openclaw_class_workspace_root=tmp_path))
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params or {}))
        if method == "agents.list":
            return {"agents": [{"id": f"classclaw-{cls.id}", "name": f"classclaw-{cls.id}", "workspace": str(workspace)}]}
        if method == "agents.update":
            return {"ok": True}
        if method == "web.login.start":
            return {"connected": False, "accountId": f"class-{cls.id}", "qrDataUrl": "weixin://login/session-2"}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    response = client.post(f"/api/v1/classes/{cls.id}/agent-binding/start", json={"force": False})
    assert response.status_code == 200
    db.refresh(binding)
    assert binding.openclaw_agent_id == f"classclaw-{cls.id}"
    assert binding.status == "awaiting_qr"
    db.refresh(stale_binding)
    assert stale_binding.openclaw_agent_id is None
    assert stale_binding.status == "pending_agent"
    assert not any(method == "agents.create" for method, _ in calls)


def test_wechat_interaction_creates_auditable_proposal_and_waits_for_confirmation(client, db, sample, monkeypatch):
    cls, _, students = sample

    async def analyze_interaction(**kwargs):
        assert kwargs["channel"] == "wechat"
        assert "张三" in kwargs["raw_text"]
        return {
            "status": "ready",
            "intent": "attendance.set",
            "summary": "登记张三上午迟到",
            "confidence": 0.98,
            "reasons": ["学生、日期、时段和迟到状态均明确"],
            "questions": [],
            "warnings": [],
            "operations": [{
                "operation_type": "attendance.set",
                "payload": {
                    "class_id": cls.id,
                    "student_id": students[0].id,
                    "attendance_date": "2026-08-29",
                    "period": "morning",
                    "status": "late",
                    "note": "微信消息",
                },
                "summary": "张三上午迟到",
                "confidence": 0.98,
                "reasons": ["学生、日期、时段和迟到状态均明确"],
            }],
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", analyze_interaction)
    response = client.post("/api/v1/interaction-analyses", json={
        "channel": "wechat",
        "external_message_id": "wx-msg-001",
        "sender_id": "teacher-1",
        "text": "张三今天上午迟到了",
        "class_id": cls.id,
        "requested_by": "李老师",
        "idempotency_key": "wechat:wx-msg-001",
    })
    assert response.status_code == 201
    payload = response.json()["data"]
    assert payload["analysis"]["status"] == "awaiting_review"
    assert len(payload["proposals"]) == 1
    assert db.scalar(select(func.count(AttendanceRecord.id))) == 0
    analysis = db.get(InteractionAnalysis, payload["analysis"]["id"])
    assert analysis.analyzed_by.startswith("openclaw/")

    proposal = payload["proposals"][0]
    confirmed = client.post(
        f"/api/v1/write-proposals/{proposal['id']}/confirm",
        json={"revision": proposal["revision"], "confirmed_by": "李老师", "confirmation_note": "已核对微信原文与预览"},
    )
    assert confirmed.status_code == 200
    assert db.scalar(select(func.count(AttendanceRecord.id))) == 1


def test_interaction_ambiguity_never_creates_a_proposal(client, db, monkeypatch):
    async def analyze_interaction(**_kwargs):
        return {
            "status": "needs_clarification",
            "intent": "student_event.create",
            "summary": "姓名不唯一",
            "confidence": 0.4,
            "reasons": ["王伟对应多个学生，无法唯一匹配"],
            "questions": ["请确认是哪一个王伟（学号或班级）"],
            "warnings": ["检测到同名学生"],
            "operations": [],
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", analyze_interaction)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "wechat", "text": "王伟今天表现很好"})
    assert response.status_code == 201
    payload = response.json()["data"]
    assert payload["analysis"]["status"] == "needs_clarification"
    assert payload["proposals"] == []
    assert payload["analysis"]["structured_json"]["rejected_reasons"] == ["王伟对应多个学生，无法唯一匹配"]
    assert db.scalar(select(func.count(WriteProposal.id))) == 0


def test_low_confidence_interaction_operation_is_excluded_and_reason_is_returned(client, db, sample, monkeypatch):
    cls, _, students = sample

    async def analyze_interaction(**_kwargs):
        return {
            "status": "ready",
            "intent": "attendance.set",
            "summary": "考勤信息部分模糊",
            "confidence": 0.95,
            "reasons": ["消息整体意图是登记考勤"],
            "questions": [],
            "warnings": [],
            "operations": [
                {
                    "operation_type": "attendance.set",
                    "payload": {
                        "class_id": cls.id,
                        "student_id": students[0].id,
                        "attendance_date": "2026-08-29",
                        "period": "morning",
                        "status": "late",
                    },
                    "summary": "张三迟到",
                    "confidence": 0.45,
                    "reasons": ["原消息没有说明迟到发生在上午还是下午"],
                }
            ],
        }

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", analyze_interaction)
    response = client.post(
        "/api/v1/interaction-analyses",
        json={"channel": "wechat", "text": "张三迟到了", "class_id": cls.id},
    )

    payload = response.json()["data"]
    assert response.status_code == 201
    assert payload["analysis"]["status"] == "needs_clarification"
    assert payload["proposals"] == []
    assert payload["analysis"]["structured_json"]["operations"] == []
    assert "上午还是下午" in payload["analysis"]["structured_json"]["rejected_reasons"][0]
    assert db.scalar(select(func.count(WriteProposal.id))) == 0


def test_interaction_cleaner_prompt_contains_common_database_semantics(db, monkeypatch):
    captured = {}

    async def responses(prompt, *, user, attachments=None, max_output_tokens=8000, db=None):
        captured["prompt"] = prompt
        return {
            "status": "no_action",
            "intent": "none",
            "summary": "无写入",
            "confidence": 1,
            "reasons": ["输入没有班级数据写入意图"],
            "questions": [],
            "warnings": [],
            "operations": [],
        }

    async def extractor_ready() -> bool:
        return True

    async def extractor_off() -> bool:
        return False

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)

    # Default path: extractor agent is ready, semantics live in its AGENTS.md.
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_ready)
    asyncio.run(openclaw_bridge.analyze_interaction(
        db=db,
        analysis_id="analysis-1",
        channel="wechat",
        raw_text="21号语文作业未交，不需要先新建",
        attachments=[],
        context={"current_datetime": "2026-08-30T08:00:00+08:00"},
    ))
    agents_md = openclaw_bridge._extractor_agents_md()
    assert "subtype=homework_missing" in agents_md
    assert "不新建/不关联具体作业" in agents_md
    assert "上学迟到" in agents_md
    assert "reasons" in agents_md
    assert "置信度门槛为 0.75" in agents_md

    # Fallback path: switch off, semantics stay in the inline prompt.
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    result = asyncio.run(openclaw_bridge.analyze_interaction(
        db=db,
        analysis_id="analysis-1",
        channel="wechat",
        raw_text="21号语文作业未交，不需要先新建",
        attachments=[],
        context={"current_datetime": "2026-08-30T08:00:00+08:00"},
    ))

    assert result["status"] == "no_action"
    assert "subtype=homework_missing" in captured["prompt"]
    assert "不新建/不关联具体作业" in captured["prompt"]
    assert "上学迟到" in captured["prompt"]


def test_bound_class_agent_cannot_confirm_foreign_proposal(client, sample):
    cls, other, students = sample
    preview = client.post("/api/v1/write-proposals", json={"operation_type": "attendance.set", "payload": {
        "class_id": cls.id, "student_id": students[0].id, "attendance_date": "2026-08-29", "period": "morning", "status": "late",
    }}).json()["data"]
    response = client.post(f"/api/v1/write-proposals/{preview['id']}/confirm", json={
        "revision": preview["revision"], "confirmed_by": "外班智能体", "bound_class_id": other.id,
    })
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CLASS_SCOPE_VIOLATION"


def test_chat_can_confirm_multiple_previews_in_one_atomic_request(client, db, sample):
    cls, _, students = sample
    proposals = []
    for index, student in enumerate(students[:2]):
        proposals.append(client.post("/api/v1/write-proposals", json={
            "operation_type": "attendance.set",
            "payload": {
                "class_id": cls.id,
                "student_id": student.id,
                "attendance_date": "2026-08-30",
                "period": "morning",
                "status": "late" if index == 0 else "present",
            },
        }).json()["data"])

    confirmed = client.post("/api/v1/write-proposals/confirm-batch", json={
        "items": [{"proposal_id": proposal["id"], "revision": proposal["revision"]} for proposal in proposals],
        "confirmed_by": "李老师",
        "confirmation_note": "聊天中回复全部确认",
        "bound_class_id": cls.id,
    })

    assert confirmed.status_code == 200
    assert [item["status"] for item in confirmed.json()["data"]] == ["completed", "completed"]
    assert db.scalar(select(func.count(AttendanceRecord.id))) == 2


def test_batch_confirmation_rolls_back_every_write_when_one_fails(client, db, sample, monkeypatch):
    cls, _, students = sample
    proposals = [
        client.post("/api/v1/write-proposals", json={
            "operation_type": "attendance.set",
            "payload": {
                "class_id": cls.id,
                "student_id": student.id,
                "attendance_date": "2026-08-30",
                "period": "afternoon",
                "status": "present",
            },
        }).json()["data"]
        for student in students[:2]
    ]
    original_execute = approval_service._execute
    calls = 0

    def fail_second(database, proposal):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AppError("TEST_BATCH_FAILURE", "模拟第二条写入失败", 409)
        return original_execute(database, proposal)

    monkeypatch.setattr(approval_service, "_execute", fail_second)
    response = client.post("/api/v1/write-proposals/confirm-batch", json={
        "items": [{"proposal_id": proposal["id"], "revision": 1} for proposal in proposals],
        "confirmed_by": "李老师",
        "bound_class_id": cls.id,
    })

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TEST_BATCH_FAILURE"
    assert db.scalar(select(func.count(AttendanceRecord.id))) == 0
    db.expire_all()
    assert [db.get(WriteProposal, proposal["id"]).status for proposal in proposals] == ["pending_review", "pending_review"]


def test_web_structured_write_skips_chat_proposal_flow(client, db, sample):
    cls, _, students = sample
    before = db.scalar(select(func.count(WriteProposal.id)))
    response = client.put("/api/v1/attendance", json={
        "class_id": cls.id,
        "student_id": students[0].id,
        "attendance_date": "2026-08-30",
        "period": "full_day",
        "status": "present",
    })
    assert response.status_code == 200
    assert db.scalar(select(func.count(AttendanceRecord.id))) == 1
    assert db.scalar(select(func.count(WriteProposal.id))) == before
