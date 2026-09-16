from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.entities import AttendanceRecord, Student
from app.schemas.domain import (
    ExamCreate,
    ExamSubjectInput,
    HomeworkCreate,
    WriteProposalConfirm,
    WriteProposalCreate,
)
from app.services import academic, approval, student_refs
from app.utils.time import now, today


def _propose(db, operation_type, payload, bound_class_id=None):
    return approval.create_proposal(
        db,
        WriteProposalCreate(operation_type=operation_type, payload=payload),
        bound_class_id=bound_class_id,
    )


def test_normalize_student_ref():
    assert student_refs.normalize_student_ref(" 001 ") == "001"
    assert student_refs.normalize_student_ref("13号") == "13"
    assert student_refs.normalize_student_ref("第13号") == "13"
    assert student_refs.normalize_student_ref("１３") == "13"
    assert student_refs.normalize_student_ref("００２號") == "002"


def test_resolve_exact_numeric_and_uuid(db, sample):
    cls, _other, students = sample
    assert student_refs.resolve_student(db, cls.id, "002").id == students[1].id
    assert student_refs.resolve_student(db, cls.id, "2").id == students[1].id
    assert student_refs.resolve_student(db, cls.id, "2号").id == students[1].id
    assert student_refs.resolve_student(db, cls.id, students[0].id).id == students[0].id
    # 学号只在班级内有效：外班的 001 不影响本班解析
    assert student_refs.resolve_student(db, cls.id, "001").id == students[0].id


def test_resolve_unique_name_and_ambiguity(db, sample):
    cls, _other, students = sample
    assert student_refs.resolve_student(db, cls.id, "王五").id == students[2].id
    db.add(Student(class_id=cls.id, student_no="099", name="张三", tags=[]))
    db.commit()
    with pytest.raises(AppError) as caught:
        student_refs.resolve_student(db, cls.id, "张三")
    assert caught.value.code == "STUDENT_AMBIGUOUS"
    assert {row["student_no"] for row in caught.value.details["candidates"]} == {"001", "099"}


def test_resolve_numeric_collision_is_ambiguous(db, sample):
    cls, _other, _students = sample
    db.add(Student(class_id=cls.id, student_no="1", name="一一", tags=[]))
    db.commit()
    assert student_refs.resolve_student(db, cls.id, "1").student_no == "1"
    with pytest.raises(AppError) as caught:
        student_refs.resolve_student(db, cls.id, "01")
    assert caught.value.code == "STUDENT_AMBIGUOUS"


def test_resolve_rejects_deleted_and_unknown(db, sample):
    cls, _other, students = sample
    students[0].deleted_at = now()
    db.commit()
    with pytest.raises(AppError) as caught:
        student_refs.resolve_student(db, cls.id, "001")
    assert caught.value.code == "STUDENT_NOT_FOUND"
    with pytest.raises(AppError):
        student_refs.resolve_student(db, cls.id, "999")


def test_attendance_proposal_resolves_student_no(db, sample):
    cls, _other, students = sample
    proposal = _propose(db, "attendance.set", {
        "class_id": cls.id, "student_no": "2号", "attendance_date": str(today()),
        "period": "morning", "status": "late", "note": "迟到两分钟",
    })
    assert proposal.normalized_payload_json["student_id"] == students[1].id
    summary = proposal.preview_json["summary"]
    assert "student_id" not in summary
    assert summary["student_no"] == "002" and summary["name"] == "李四"
    confirmed = approval.confirm_proposal(db, proposal.id, WriteProposalConfirm(revision=1, confirmed_by="班主任"))
    assert confirmed.status == "completed"
    row = db.scalar(select(AttendanceRecord).where(AttendanceRecord.student_id == students[1].id))
    assert row.status == "late"


def test_student_update_and_batch_resolve_student_nos(db, sample):
    cls, _other, students = sample
    single = _propose(db, "student.update", {"class_id": cls.id, "student_no": "001", "changes": {"group_no": "2"}})
    assert single.normalized_payload_json["student_id"] == students[0].id
    assert single.preview_json["summary"]["student_no"] == "001"

    batch = _propose(db, "student.update.batch", {
        "class_id": cls.id, "student_nos": ["1", "003"], "changes": {"gender": "女"}, "only_if_empty": ["gender"],
    })
    assert set(batch.normalized_payload_json["student_ids"]) == {students[0].id, students[2].id}
    rows = batch.preview_json["students"]
    assert all("student_id" not in row for row in rows)
    assert {row["student_no"] for row in rows} == {"001", "003"}
    approval.confirm_proposal(db, batch.id, WriteProposalConfirm(revision=1, confirmed_by="班主任", bound_class_id=cls.id))
    assert students[0].gender == students[2].gender == "女"


def test_student_update_requires_class_context(db, sample):
    cls, _other, students = sample
    with pytest.raises(AppError) as caught:
        _propose(db, "student.update", {"student_no": "001", "changes": {"group_no": "2"}})
    assert caught.value.code == "VALIDATION_ERROR"
    # 绑定班级上下文可直接注入
    proposal = _propose(db, "student.update", {"student_no": "001", "changes": {"group_no": "3"}}, bound_class_id=cls.id)
    assert proposal.normalized_payload_json["student_id"] == students[0].id


def test_student_update_still_accepts_legacy_uuid_without_class(db, sample):
    _cls, _other, students = sample
    proposal = _propose(db, "student.update", {"student_id": students[0].id, "changes": {"group_no": "4"}})
    assert proposal.normalized_payload_json["student_id"] == students[0].id


def test_student_event_create_and_batch_resolve(db, sample):
    cls, _other, students = sample
    single = _propose(db, "student_event.create", {
        "class_id": cls.id, "student_no": "003", "event_type": "behavior", "subtype": "helping_peer",
        "event_date": str(today()), "content": "主动帮助同学",
    })
    assert single.normalized_payload_json["student_id"] == students[2].id

    batch = _propose(db, "student_event.batch", {
        "items": [
            {"student_no": "001", "event_type": "homework", "subtype": "homework_missing", "event_date": str(today()), "content": "语文作业未交", "subject": "语文"},
            {"student_no": "002", "event_type": "homework", "subtype": "homework_missing", "event_date": str(today()), "content": "语文作业未交", "subject": "语文"},
        ],
    }, bound_class_id=cls.id)
    items = batch.normalized_payload_json["items"]
    assert [item["class_id"] for item in items] == [cls.id, cls.id]
    assert [item["student_id"] for item in items] == [students[0].id, students[1].id]
    display = batch.preview_json["summary"]["items"]
    assert [row["student_no"] for row in display] == ["001", "002"]


def test_homework_and_score_batches_resolve_within_parent_class(db, sample):
    cls, _other, students = sample
    homework = academic.create_homework(db, HomeworkCreate(class_id=cls.id, title="语文背诵", subject="语文", assigned_date=today()))
    proposal = _propose(db, "homework.status.batch", {
        "homework_id": homework.id, "items": [{"student_no": "001", "status": "missing"}, {"student_no": "003", "status": "missing"}],
    }, bound_class_id=cls.id)
    assert [item["student_id"] for item in proposal.normalized_payload_json["items"]] == [students[0].id, students[2].id]
    summary_items = proposal.preview_json["summary"]["items"]
    assert all("student_id" not in row for row in summary_items)
    assert [row["student_no"] for row in summary_items] == ["001", "003"]

    exam = academic.create_exam(db, ExamCreate(class_id=cls.id, name="9月月考", exam_date=today(),
                                subjects=[ExamSubjectInput(subject="语文", full_score=100)]))
    scored = _propose(db, "score.batch", {
        "exam_id": exam.id, "scores": [{"student_no": "1", "subject": "语文", "score": 92}],
    }, bound_class_id=cls.id)
    assert scored.normalized_payload_json["scores"][0]["student_id"] == students[0].id
    assert scored.preview_json["summary"]["scores"][0]["student_no"] == "001"

    with pytest.raises(AppError) as caught:
        _propose(db, "score.batch", {"exam_id": exam.id, "scores": [{"student_no": "001", "subject": "语文", "score": 90}]}, bound_class_id=_other.id)
    assert caught.value.code == "CLASS_SCOPE_VIOLATION"


def test_seating_layout_accepts_student_nos(db, sample):
    cls, _other, students = sample
    proposal = _propose(db, "seating.update", {
        "class_id": cls.id, "rows": 1, "cols": 3, "layout": [["001", None, "王五"]],
    })
    layout = proposal.normalized_payload_json["layout"]
    assert layout == [[students[0].id, None, students[2].id]]
    with pytest.raises(AppError) as caught:
        _propose(db, "seating.update", {"class_id": cls.id, "rows": 1, "cols": 1, "layout": "not-a-matrix"})
    assert caught.value.code == "VALIDATION_ERROR"


def test_cross_class_payload_rejected_before_resolution(db, sample):
    cls, other, _students = sample
    with pytest.raises(AppError) as caught:
        _propose(db, "attendance.set", {
            "class_id": other.id, "student_no": "001", "attendance_date": str(today()), "period": "morning", "status": "late",
        }, bound_class_id=cls.id)
    assert caught.value.code == "CLASS_SCOPE_VIOLATION"
    with pytest.raises(AppError) as not_found:
        _propose(db, "attendance.set", {
            "class_id": cls.id, "student_no": "999", "attendance_date": str(today()), "period": "morning", "status": "late",
        }, bound_class_id=cls.id)
    assert not_found.value.code == "STUDENT_NOT_FOUND"


def test_http_proposal_preview_uses_student_no(client, sample):
    cls, _other, students = sample
    response = client.post("/api/v1/write-proposals", json={
        "operation_type": "attendance.set",
        "payload": {"class_id": cls.id, "student_no": "002", "attendance_date": str(today()), "period": "afternoon", "status": "leave"},
    })
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["normalized_payload_json"]["student_id"] == students[1].id
    summary = data["preview_json"]["summary"]
    assert "student_id" not in summary and summary["student_no"] == "002"


def test_student_no_exact_search(client, db, sample):
    cls, _other, students = sample
    db.add(Student(class_id=cls.id, student_no="0020", name="新生", tags=[]))
    db.commit()
    response = client.get("/api/v1/students", params={"class_id": cls.id, "student_no": "002"})
    assert response.status_code == 200
    data = response.json()["data"]
    # 精确匹配：002 不会模糊命中 0020
    assert data["total"] == 1 and data["items"][0]["id"] == students[1].id
