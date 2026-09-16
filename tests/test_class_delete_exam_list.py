import asyncio
import json
from dataclasses import replace
from datetime import date

from sqlalchemy import select

from app.config import settings
from app.models.entities import (
    Arrangement,
    AttendanceRecord,
    AuditLog,
    ClassAgentBinding,
    ClassRoom,
    DutyAssignment,
    DutyRule,
    DutySchedule,
    Exam,
    Homework,
    HomeworkStudentStatus,
    Reminder,
    Score,
    SeatingSnapshot,
    Student,
    StudentEvent,
    SystemSetting,
)
from app.schemas.domain import ExamCreate, ExamSubjectInput
from app.services import academic, deletion_runtime, openclaw_provisioning
from app.utils.time import now


def _admin_header() -> dict[str, str]:
    assert settings.api_token
    return {"Authorization": f"Bearer {settings.api_token}", "X-ClassClaw-Surface": "web"}


def _teacher_headers(client, username: str) -> dict[str, str]:
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "32767"}).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}", "X-ClassClaw-Surface": "web"}


def test_exam_list_returns_subjects_filters_and_scopes_teacher(client, db):
    user = client.post("/api/v1/admin/users", json={"username": "examteacher"}, headers=_admin_header()).json()["data"]
    owned = ClassRoom(name="考试班", grade="高一", owner_user_id=user["id"])
    other = ClassRoom(name="其他考试班", grade="高二")
    db.add_all([owned, other])
    db.commit()
    academic.create_exam(
        db,
        ExamCreate(
            class_id=owned.id,
            name="期中考试",
            exam_date=date(2026, 10, 20),
            subjects=[ExamSubjectInput(subject="语文", full_score=120), ExamSubjectInput(subject="数学", full_score=150)],
        ),
    )
    academic.create_exam(
        db,
        ExamCreate(
            class_id=other.id,
            name="外班考试",
            exam_date=date(2026, 10, 21),
            subjects=[ExamSubjectInput(subject="英语", full_score=100)],
        ),
    )
    headers = _teacher_headers(client, "examteacher")

    listed = client.get("/api/v1/exams", headers=headers)
    assert listed.status_code == 200
    assert [exam["name"] for exam in listed.json()["data"]] == ["期中考试"]
    assert {row["subject"]: row["full_score"] for row in listed.json()["data"][0]["subjects"]} == {"数学": 150.0, "语文": 120.0}

    filtered = client.get("/api/v1/exams?start_date=2026-10-21", headers=headers)
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []
    forbidden = client.get(f"/api/v1/exams?class_id={other.id}", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "CLASS_ACCESS_DENIED"


def test_hard_delete_class_removes_business_data_and_releases_account_slot(client, db, deletion_gateway):
    user = client.post("/api/v1/admin/users", json={"username": "deleteteacher"}, headers=_admin_header()).json()["data"]
    owned = ClassRoom(name="待删除班", grade="高一", owner_user_id=user["id"])
    other = ClassRoom(name="禁止删除班", grade="高二")
    db.add_all([owned, other])
    db.flush()
    owned_workspace = settings.openclaw_class_workspace_root / owned.id
    owned_workspace.mkdir(parents=True, exist_ok=True)
    (owned_workspace / "SOUL.md").write_text("temporary", encoding="utf-8")
    student = Student(class_id=owned.id, student_no="01", name="测试学生", tags=[])
    db.add(student)
    db.flush()
    homework = Homework(class_id=owned.id, title="练习", subject="语文", assigned_date=date(2026, 9, 1))
    exam = Exam(class_id=owned.id, name="月考", exam_date=date(2026, 9, 2))
    schedule = DutySchedule(class_id=owned.id, name="值日", start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
    arrangement = Arrangement(class_id=owned.id, title="班会")
    db.add_all([homework, exam, schedule, arrangement])
    db.flush()
    db.add_all(
        [
            ClassAgentBinding(
                class_id=owned.id,
                agent_name=f"classclaw-{owned.id}",
                workspace_path=str(settings.openclaw_class_workspace_root / owned.id),
            ),
            SeatingSnapshot(class_id=owned.id, rows=1, cols=1, layout_json=[[student.id]]),
            DutyRule(
                class_id=owned.id,
                name="规则",
                rule_json={"items": []},
                effective_from=date(2026, 9, 1),
                effective_to=date(2026, 9, 30),
            ),
            DutyAssignment(duty_schedule_id=schedule.id, duty_date=date(2026, 9, 1), item_name="卫生", student_id=student.id),
            HomeworkStudentStatus(homework_id=homework.id, student_id=student.id, status="missing"),
            StudentEvent(
                class_id=owned.id,
                student_id=student.id,
                event_type="behavior",
                subtype="sleeping",
                event_date=date(2026, 9, 1),
                content="上课睡觉",
            ),
            AttendanceRecord(
                class_id=owned.id,
                student_id=student.id,
                attendance_date=date(2026, 9, 1),
                period="morning",
                status="late",
            ),
            Score(exam_id=exam.id, student_id=student.id, subject="语文", score=90, full_score=100),
            Reminder(arrangement_id=arrangement.id, remind_at=now()),
            SystemSetting(key="current_class_id", value_json=owned.id),
        ]
    )
    db.commit()
    headers = _teacher_headers(client, "deleteteacher")

    forbidden = client.delete(f"/api/v1/classes/{other.id}", headers=headers)
    assert forbidden.status_code == 403
    deleted = client.delete(f"/api/v1/classes/{owned.id}", headers=headers)
    assert deleted.status_code == 200
    result = deleted.json()["data"]
    assert result["deleted"] is True
    assert result["cleanup_status"] == "complete"
    assert result["deletion_id"]
    assert result["agent_cleanup"]["workspace_removed"] is True
    assert not owned_workspace.exists()
    assert db.get(ClassRoom, owned.id) is None
    assert db.get(Student, student.id) is None
    assert db.get(Homework, homework.id) is None
    assert db.get(Exam, exam.id) is None
    assert db.scalar(select(SystemSetting.value_json).where(SystemSetting.key == "current_class_id")) is None
    assert db.scalar(select(AuditLog).where(AuditLog.action == "hard_delete", AuditLog.entity_id == owned.id)) is not None

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["class_id"] is None
    restarted = client.post("/api/v1/class-onboarding/sessions", json={}, headers=headers)
    assert restarted.status_code == 201


def test_class_agent_runtime_cleanup_uses_null_keys_and_removes_directories(db, tmp_path, monkeypatch):
    cls = ClassRoom(name="智能体清理班", grade="高一")
    db.add(cls)
    db.commit()
    workspace = tmp_path / "workspaces" / cls.id
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("temporary", encoding="utf-8")
    agent_state = tmp_path / "state" / "agents" / "agent-delete"
    agent_state.mkdir(parents=True)
    (agent_state / "session.jsonl").write_text("old", encoding="utf-8")

    monkeypatch.setattr(
        openclaw_provisioning,
        "settings",
        replace(settings, openclaw_class_workspace_root=tmp_path / "workspaces",
                openclaw_state_dir=tmp_path / "state"),
    )
    plan = {
        "class_id": cls.id,
        "agent_id": "agent-delete",
        "workspace": str(workspace),
        "channel": settings.openclaw_wechat_channel,
        "account_ids": ["wechat-delete"],
    }
    calls: list[tuple[str, dict | None]] = []
    patched = {"done": False}

    async def fake_admin_rpc(method, params=None):
        calls.append((method, params))
        if method == "config.get":
            mapping = {"agent-keep": "keep-class"} if patched["done"] else {"agent-delete": cls.id, "agent-keep": "keep-class"}
            agents = [{"id": "agent-keep"}] if patched["done"] else [{"id": "agent-delete"}, {"id": "agent-keep"}]
            bindings = [] if patched["done"] else [
                {"agentId": "agent-delete", "match": {"channel": settings.openclaw_wechat_channel, "accountId": "wechat-delete"}},
                {"agentId": "agent-keep", "match": {"channel": "other", "accountId": "keep"}},
            ]
            return {"hash": "config-hash", "config": {"agents": {"list": agents}, "bindings": bindings,
                    "plugins": {"entries": {"classclaw": {"config": {"agentClasses": mapping}}}}}}
        if method == "channels.logout":
            return {"cleared": True, "loggedOut": True}
        if method == "tasks.list":
            return {"tasks": []}
        if method == "config.patch":
            patched["done"] = True
            return {"ok": True}
        raise AssertionError(method)

    async def cancel_wechat(class_id, action, **params):
        assert action == "cancel" and class_id == cls.id
        return {"cancelled": True, "accountIds": []}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_admin_rpc)
    monkeypatch.setattr("app.services.wechat_login.call", cancel_wechat)

    asyncio.run(deletion_runtime.cleanup(plan))

    patch_params = next(params for method, params in calls if method == "config.patch")
    raw = json.loads(patch_params["raw"])
    # An explicit null deletes the agentClasses key; replacePaths only replaces arrays.
    assert raw["plugins"]["entries"]["classclaw"]["config"]["agentClasses"] == {"agent-delete": None}
    assert patch_params["replacePaths"] == ["agents.list", "bindings"]
    assert raw["agents"]["list"] == [{"id": "agent-keep"}]
    assert raw["bindings"] == [{"agentId": "agent-keep", "match": {"channel": "other", "accountId": "keep"}}]
    assert [params for method, params in calls if method == "channels.logout"] == [
        {"channel": settings.openclaw_wechat_channel, "accountId": "wechat-delete"},
    ]

    files = deletion_runtime.cleanup_files(plan)
    assert files["runtime_removed"] is True
    assert files["workspace_removed"] is True
    assert files["agent_state_removed"] is True
    assert not workspace.exists()
    assert not agent_state.exists()
