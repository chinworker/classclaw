from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.analytics.service import morning_briefing, student_comprehensive
from app.core.errors import AppError
from app.models.entities import LessonOverride, Reminder
from app.schemas.domain import (
    ArrangementCreate,
    LessonOverrideCreate,
    LessonSwapRequest,
    PeriodCreate,
    StudentEventCreate,
    TimetableItem,
    TimetableReplace,
)
from app.services import academic, operations, timetable


def test_lesson_key_override_swap_and_remove(db, sample):
    cls = sample[0]
    timetable.create_period(db, cls.id, PeriodCreate(period_no=1, name="第一节"))
    timetable.create_period(db, cls.id, PeriodCreate(period_no=2, name="第二节"))
    timetable.replace_base_timetable(db, cls.id, TimetableReplace(items=[TimetableItem(weekday=1, period_no=1, subject="语文"), TimetableItem(weekday=1, period_no=2, subject="数学")]))
    assert timetable.lesson_key(date(2026, 9, 7), 1) == "2026-09-07-P01"
    preview = timetable.swap_preview(db, LessonSwapRequest(class_id=cls.id, lesson_date=date(2026, 9, 7), period_a=1, period_b=2, reason="测试"))
    assert preview["writes_performed"] == 0 and db.scalar(select(func.count(LessonOverride.id))) == 0
    rows = timetable.confirm_swap(db, LessonSwapRequest(class_id=cls.id, lesson_date=date(2026, 9, 7), period_a=1, period_b=2, reason="测试"))
    assert len(rows) == 2
    timetable.remove_override(db, rows[0].id)
    daily = timetable.daily_timetable(db, cls.id, date(2026, 9, 7))
    assert daily[0]["subject"] == "语文"


def test_cross_date_swap_uses_latest_adjusted_lessons(db, sample):
    cls = sample[0]
    timetable.create_period(db, cls.id, PeriodCreate(period_no=1))
    timetable.replace_base_timetable(
        db,
        cls.id,
        TimetableReplace(
            items=[
                TimetableItem(weekday=1, period_no=1, subject="语文", teacher="张老师", room="101"),
                TimetableItem(weekday=2, period_no=1, subject="数学", teacher="李老师", room="102"),
            ]
        ),
    )
    monday = date(2026, 9, 7)
    tuesday = date(2026, 9, 8)
    timetable.create_override(
        db,
        LessonOverrideCreate(
            class_id=cls.id,
            lesson_date=monday,
            period_no=1,
            replacement_subject="英语",
            replacement_teacher="王老师",
            replacement_room="201",
            reason="临时调整",
        ),
    )
    request = LessonSwapRequest(
        class_id=cls.id,
        lesson_date_a=monday,
        lesson_date_b=tuesday,
        period_a=1,
        period_b=1,
        reason="跨日互换",
    )
    preview = timetable.swap_preview(db, request)
    assert preview["changes"][0]["from"] == "英语"
    assert preview["changes"][0]["to"] == "数学"

    timetable.confirm_swap(db, request)
    monday_lesson = timetable.daily_timetable(db, cls.id, monday)[0]
    tuesday_lesson = timetable.daily_timetable(db, cls.id, tuesday)[0]
    assert (monday_lesson["subject"], monday_lesson["teacher"], monday_lesson["room"]) == ("数学", "李老师", "102")
    assert (tuesday_lesson["subject"], tuesday_lesson["teacher"], tuesday_lesson["room"]) == ("英语", "王老师", "201")


def test_period_custom_name_is_optional_and_has_chinese_fallback(db, sample):
    cls = sample[0]
    first = timetable.create_period(db, cls.id, PeriodCreate(period_no=1))
    custom = timetable.create_period(db, cls.id, PeriodCreate(period_no=2, name=" 早读 "))
    assert first.name is None
    assert custom.name == "早读"
    assert timetable.period_display_name(1, first.name) == "第一节"
    assert timetable.period_display_name(12) == "第十二节"
    assert timetable.period_display_name(2, custom.name) == "早读"

    timetable.replace_base_timetable(
        db,
        cls.id,
        TimetableReplace(items=[TimetableItem(weekday=1, period_no=1, subject="语文")]),
    )
    daily = timetable.daily_timetable(db, cls.id, date(2026, 9, 7))
    assert daily[0]["period_name"] == "第一节"


def test_analytics_returns_evidence_and_warnings(db, sample):
    cls, _, students = sample
    event = academic.create_student_event(db, StudentEventCreate(class_id=cls.id, student_id=students[0].id, event_type="behavior", subtype="课堂", event_date=date(2026, 9, 1), content="课堂积极发言", sentiment="positive"))
    result = student_comprehensive(db, students[0].id, date(2026, 9, 1), date(2026, 9, 7))
    assert any(row["id"] == event.id for row in result["evidence"])
    assert result["period"]["start_date"] == date(2026, 9, 1)
    assert result["warnings"]
    brief = morning_briefing(db, cls.id, date(2026, 9, 7))
    assert "timetable" in brief and "data_quality" in brief


def test_arrangement_defaults_to_one_reminder_three_hours_before(db, sample):
    cls = sample[0]
    starts_at = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
    payload = ArrangementCreate(class_id=cls.id, title="家长会", start_at=starts_at)
    assert payload.reminder_times == [starts_at - timedelta(hours=3)]

    arrangement = operations.create_arrangement(db, payload)
    reminders = list(db.scalars(select(Reminder).where(Reminder.arrangement_id == arrangement.id)))
    assert len(reminders) == 1
    assert reminders[0].remind_at.hour == 6


def test_arrangement_accepts_at_most_three_custom_reminders():
    starts_at = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
    reminder_times = [starts_at - timedelta(days=2), starts_at - timedelta(days=1), starts_at - timedelta(hours=1)]
    payload = ArrangementCreate(title="家长会", start_at=starts_at, reminder_times=reminder_times)
    assert payload.reminder_times == reminder_times

    with pytest.raises(ValidationError):
        ArrangementCreate(title="家长会", start_at=starts_at, reminder_times=reminder_times + [starts_at - timedelta(minutes=30)])


def test_morning_briefing_includes_same_day_start_only_arrangement(db, sample):
    cls = sample[0]
    operations.create_arrangement(
        db,
        ArrangementCreate(class_id=cls.id, title="秋游集合", start_at=datetime(2026, 9, 7, 8, 0)),
    )
    brief = morning_briefing(db, cls.id, date(2026, 9, 7))
    reminder = next(item for item in brief["today_reminders"] if item["title"] == "秋游集合")
    assert reminder["due_at"] is None
    assert len(reminder["reminder_times"]) == 1
    assert reminder["reminder_times"][0].hour == 5


def test_reminder_delivery_rechecks_arrangement_state(db, sample):
    cls = sample[0]
    arrangement = operations.create_arrangement(
        db,
        ArrangementCreate(class_id=cls.id, title="交材料", due_at=datetime(2026, 9, 7, 12, 0)),
    )
    reminder = db.scalar(select(Reminder).where(Reminder.arrangement_id == arrangement.id))
    due = operations.reminder_delivery(db, reminder.id, datetime(2026, 9, 7, 10, 0))
    assert due["active"] is True and due["due"] is True

    operations.complete_arrangement(db, arrangement.id)
    cancelled = operations.reminder_delivery(db, reminder.id, datetime(2026, 9, 7, 10, 0))
    assert cancelled["active"] is False and cancelled["due"] is False


def test_api_response_for_unsupported_direct_class_creation(client, db):
    response = client.post("/api/v1/classes", json={"name": "API测试班", "grade": "高一"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WEB_ONBOARDING_REQUIRED"
    preview = client.post("/api/v1/write-proposals", json={"operation_type": "class.create", "payload": {"name": "API测试班", "grade": "高一"}, "requested_by": "teacher-1"})
    assert preview.status_code == 400
    assert preview.json()["error"]["code"] == "VALIDATION_ERROR"
    assert preview.json()["request_id"]
