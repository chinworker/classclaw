from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.analytics import service as analytics
from app.models.entities import AttendanceRecord, ClassRoom, Student, WriteProposal
from app.schemas.domain import AttendanceSet, WriteProposalCreate
from app.services import academic, approval, openclaw_bridge
from app.utils.time import now


def test_daily_write_defaults_and_legacy_periods_update_the_same_record(client, db, sample):
    cls, _, students = sample
    payload = {"class_id": cls.id, "student_id": students[0].id, "attendance_date": "2026-10-22", "status": "late"}
    first = client.put("/api/v1/attendance", json=payload)
    assert first.status_code == 200, first.text
    record_id = first.json()["data"]["id"]
    for period in ("morning", "afternoon", "recess", "care_1", "care_2"):
        updated = client.put("/api/v1/attendance", json={**payload, "period": period, "status": "leave"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["id"] == record_id
        assert updated.json()["data"]["period"] == "full_day"
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 1
    queried = client.get(f"/api/v1/attendance?class_id={cls.id}&start_date=2026-10-22&end_date=2026-10-22")
    assert len(queried.json()["data"]) == 1


def test_database_enforces_one_record_per_student_date(db, sample):
    cls, _, students = sample
    data = AttendanceSet(class_id=cls.id, student_id=students[0].id, attendance_date=date(2026, 10, 22), status="late")
    academic.set_attendance(db, data)
    db.add(AttendanceRecord(**data.model_dump()))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 1


def test_summary_and_both_analytics_paths_count_days_once(db, sample):
    cls, _, students = sample
    start, end = date(2026, 10, 22), date(2026, 10, 23)
    academic.set_attendance(db, AttendanceSet(class_id=cls.id, student_id=students[0].id, attendance_date=start, status="absent"))
    academic.set_attendance(db, AttendanceSet(class_id=cls.id, student_id=students[0].id, attendance_date=end, status="late"))
    summary = academic.attendance_summary(db, cls.id, start, end)
    assert summary["student_day_count"] == summary["slot_count"] == 6
    assert summary["status_counts"] == {"absent": 1, "present": 4, "late": 1}
    assert summary["attendance_rate"] == 5 / 6
    assert [row["student_id"] for row in summary["students"]] == [student.id for student in students[:3]]
    assert summary["students"][0] == {"student_id": students[0].id, "attendance_rate": 0.5, "late": 1, "absent": 1, "leave": 0}
    single = analytics.student_comprehensive(db, students[0].id, start, end)
    batch = analytics._class_student_analyses(db, cls.id, start, end)
    assert single["metrics"]["attendance"] == batch[0]["metrics"]["attendance"]
    assert single["metrics"]["attendance"]["attendance_rate"] == 0.5
    assert single["evidence"][0]["summary"] == "absent"


def test_attendance_preview_needs_no_period_or_clock_time(db, sample):
    cls, _, students = sample
    proposal = approval.create_proposal(db, WriteProposalCreate(
        operation_type="attendance.set", payload={"class_id": cls.id, "student_no": students[0].student_no,
                                                 "attendance_date": "2026-10-22", "status": "late"},
    ))
    assert proposal.preview_json["summary"]["student_no"] == students[0].student_no
    assert "period" not in proposal.preview_json["summary"]
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 0
    rules = openclaw_bridge._extractor_agents_md()
    assert "考勤每天仅一次" in rules
    assert "不追问上午/下午" in rules
    assert "period" not in openclaw_bridge._INTERACTION_PAYLOAD_HINTS["attendance.set"]


@pytest.fixture
def legacy_attendance(db, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0020_daily_attendance.py"
    spec = importlib.util.spec_from_file_location("daily_attendance_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.get_bind().begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
        cls = connection.execute(ClassRoom.__table__.insert().values(name="迁移班", grade="高一")).inserted_primary_key[0]
        sid = connection.execute(Student.__table__.insert().values(class_id=cls, student_no="1", name="学生")).inserted_primary_key[0]
        yield connection, migration, cls, sid


def test_migration_preserves_history_and_cancels_old_attendance_previews(legacy_attendance):
    connection, migration, cls, sid = legacy_attendance
    table = AttendanceRecord.__table__
    connection.execute(table.insert().values(id="old-attendance", class_id=cls, student_id=sid,
                       attendance_date=date(2026, 10, 22), period="morning", status="late", note="保留备注"))
    connection.execute(WriteProposal.__table__.insert().values(
        id="old-preview", operation_type="attendance.set", payload_json={}, normalized_payload_json={}, preview_json={},
        status="pending_review", expires_at=now() + timedelta(hours=1),
    ))
    migration.upgrade()
    row = connection.execute(select(table)).mappings().one()
    assert (row["id"], row["period"], row["status"], row["note"]) == ("old-attendance", "full_day", "late", "保留备注")
    assert connection.scalar(select(WriteProposal.status)) == "cancelled"
    assert any(item["column_names"] == ["student_id", "attendance_date"] for item in inspect(connection).get_unique_constraints(table.name))
    migration.downgrade()
    migration.upgrade()
    assert connection.scalar(select(func.count()).select_from(table)) == 1


def test_migration_refuses_ambiguous_old_days_without_changing_data(legacy_attendance):
    connection, migration, cls, sid = legacy_attendance
    table = AttendanceRecord.__table__
    for period, status in (("morning", "present"), ("afternoon", "absent")):
        connection.execute(table.insert().values(class_id=cls, student_id=sid, attendance_date=date(2026, 10, 22), period=period, status=status))
    with pytest.raises(RuntimeError, match="多条历史考勤"):
        migration.upgrade()
    assert set(connection.execute(select(table.c.period, table.c.status))) == {("morning", "present"), ("afternoon", "absent")}
