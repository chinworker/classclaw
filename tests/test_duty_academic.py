from datetime import date, datetime

from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.entities import DutyAssignment, DutySchedule, HomeworkStudentStatus, Score, StudentEvent
from app.schemas.domain import (
    AttendanceSet,
    DutyConfirmRequest,
    DutyPreviewRequest,
    ExamCreate,
    ExamSubjectInput,
    HomeworkBatchStatus,
    HomeworkCreate,
    HomeworkStatusItem,
    ScoreBatch,
    ScoreInput,
)
from app.services import academic, duty


def test_duty_preview_does_not_write_then_confirm_balanced(db, sample):
    cls = sample[0]
    rule = {"workdays": [1, 2, 3, 4, 5], "items": [{"name": "卫生区", "count": 1}], "balance": True}
    preview_body = DutyPreviewRequest(class_id=cls.id, name="一周值日", start_date=date(2026, 9, 7), end_date=date(2026, 9, 11), rule_json=rule)
    preview = duty.generate_preview(db, preview_body)
    assert preview["writes_performed"] == 0
    assert db.scalar(select(func.count(DutySchedule.id))) == 0
    confirmed = duty.confirm_schedule(db, DutyConfirmRequest(**preview_body.model_dump(), preview_token=preview["preview_token"]))
    assert confirmed["assignment_count"] == 5
    counts = [row["count"] for row in confirmed["workload"]]
    assert max(counts) - min(counts) <= 1


def test_homework_event_idempotency_and_attendance_rate(db, sample):
    cls, _, students = sample
    hw = academic.create_homework(db, HomeworkCreate(class_id=cls.id, title="数学练习", subject="数学", assigned_date=date(2026, 9, 1)))
    academic.batch_homework_status(db, hw.id, HomeworkBatchStatus(items=[HomeworkStatusItem(student_id=students[0].id, status="missing")]))
    assert academic.sync_missing_homework_events(db, hw.id)["created"] == 1
    assert academic.sync_missing_homework_events(db, hw.id)["created"] == 0
    academic.set_attendance(db, AttendanceSet(class_id=cls.id, student_id=students[0].id, attendance_date=date(2026, 9, 1), period="recess", status="absent"))
    summary = academic.attendance_summary(db, cls.id, date(2026, 9, 1), date(2026, 9, 1))
    assert summary["attendance_rate"] == 1.0
    assert summary["attendance_rate"] <= 1


def test_score_batch_invalid_rolls_back(db, sample):
    cls, _, students = sample
    exam = academic.create_exam(db, ExamCreate(class_id=cls.id, name="月考", exam_date=date(2026, 9, 2), subjects=[ExamSubjectInput(subject="数学", full_score=100)]))
    try:
        academic.save_scores(db, exam.id, ScoreBatch(scores=[ScoreInput(student_id=students[0].id, subject="数学", score=90), ScoreInput(student_id=students[1].id, subject="数学", score=101)]))
    except AppError as exc:
        assert exc.code == "SCORE_EXCEEDS_FULL_SCORE"
    else:
        raise AssertionError("超满分应拒绝整批")
    assert db.scalar(select(func.count(Score.id))) == 0

