from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import BaseTimetable, ClassPeriod, LessonOverride
from app.schemas.domain import (
    LessonBatchChangeRequest,
    LessonOverrideCreate,
    LessonSwapRequest,
    PeriodCreate,
    TimetableReplace,
)
from app.services.class_student import get_class
from app.services.common import audit, entity_dict


def lesson_key(lesson_date: date, period_no: int) -> str:
    if period_no < 1 or period_no > 99:
        raise AppError("VALIDATION_ERROR", "period_no必须在1到99之间")
    return f"{lesson_date.isoformat()}-P{period_no:02d}"


def create_period(db: Session, class_id: str, data: PeriodCreate, *, commit: bool = True) -> ClassPeriod:
    get_class(db, class_id)
    obj = ClassPeriod(class_id=class_id, **data.model_dump())
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("TIMETABLE_CONFLICT", "该班级节次编号已存在", 409) from exc
    audit(db, "create", "class_period", obj.id, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def replace_base_timetable(db: Session, class_id: str, data: TimetableReplace, *, commit: bool = True) -> list[BaseTimetable]:
    get_class(db, class_id)
    keys = [(item.weekday, item.period_no) for item in data.items]
    if len(keys) != len(set(keys)):
        raise AppError("TIMETABLE_CONFLICT", "基础课表包含重复星期和节次")
    period_nos = {p.period_no for p in db.scalars(select(ClassPeriod).where(ClassPeriod.class_id == class_id, ClassPeriod.enabled.is_(True)))}
    invalid = sorted({item.period_no for item in data.items} - period_nos)
    if invalid:
        raise AppError("TIMETABLE_CONFLICT", "课表使用了未定义或未启用节次", details={"period_nos": invalid})
    before = [entity_dict(x) for x in db.scalars(select(BaseTimetable).where(BaseTimetable.class_id == class_id))]
    db.execute(delete(BaseTimetable).where(BaseTimetable.class_id == class_id))
    result = []
    for item in data.items:
        obj = BaseTimetable(class_id=class_id, **item.model_dump())
        db.add(obj)
        result.append(obj)
    db.flush()
    audit(db, "replace", "base_timetable", class_id, before={"items": before}, after={"items": [entity_dict(x) for x in result]})
    if commit:
        db.commit()
    return result


def daily_timetable(db: Session, class_id: str, lesson_date: date) -> list[dict]:
    get_class(db, class_id, include_inactive=True)
    bases = list(
        db.scalars(
            select(BaseTimetable)
            .where(BaseTimetable.class_id == class_id, BaseTimetable.weekday == lesson_date.isoweekday())
            .order_by(BaseTimetable.period_no)
        )
    )
    periods = {p.period_no: p for p in db.scalars(select(ClassPeriod).where(ClassPeriod.class_id == class_id))}
    overrides = {o.period_no: o for o in db.scalars(select(LessonOverride).where(LessonOverride.class_id == class_id, LessonOverride.lesson_date == lesson_date))}
    rows = []
    for base in bases:
        override = overrides.get(base.period_no)
        cancelled = bool(override and override.status == "cancelled")
        rows.append(
            {
                "lesson_key": lesson_key(lesson_date, base.period_no),
                "class_id": class_id,
                "lesson_date": lesson_date,
                "weekday": lesson_date.isoweekday(),
                "period_no": base.period_no,
                "period_name": periods.get(base.period_no).name if periods.get(base.period_no) else f"第{base.period_no}节",
                "subject": None if cancelled else (override.replacement_subject if override and override.replacement_subject is not None else base.subject),
                "teacher": None if cancelled else (override.replacement_teacher if override and override.replacement_teacher is not None else base.teacher),
                "room": None if cancelled else (override.replacement_room if override and override.replacement_room is not None else base.room),
                "is_changed": bool(override),
                "is_cancelled": cancelled,
                "change_reason": override.reason if override else None,
                "original_subject": base.subject,
                "original_teacher": base.teacher,
            }
        )
    return rows


def _base_lesson(db: Session, class_id: str, lesson_date: date, period_no: int) -> BaseTimetable:
    base = db.scalar(
        select(BaseTimetable).where(
            BaseTimetable.class_id == class_id,
            BaseTimetable.weekday == lesson_date.isoweekday(),
            BaseTimetable.period_no == period_no,
        )
    )
    if not base:
        raise AppError("TIMETABLE_CONFLICT", "指定日期和节次没有基础课程", details={"lesson_key": lesson_key(lesson_date, period_no)})
    return base


def create_override(db: Session, data: LessonOverrideCreate, *, commit: bool = True) -> LessonOverride:
    get_class(db, data.class_id)
    base = _base_lesson(db, data.class_id, data.lesson_date, data.period_no)
    key = lesson_key(data.lesson_date, data.period_no)
    if db.scalar(select(LessonOverride).where(LessonOverride.class_id == data.class_id, LessonOverride.lesson_key == key)):
        raise AppError("TIMETABLE_CONFLICT", "该课程已有临时覆盖", 409, {"lesson_key": key})
    obj = LessonOverride(
        class_id=data.class_id,
        lesson_key=key,
        lesson_date=data.lesson_date,
        period_no=data.period_no,
        original_subject=base.subject,
        original_teacher=base.teacher,
        replacement_subject=data.replacement_subject,
        replacement_teacher=data.replacement_teacher,
        replacement_room=data.replacement_room,
        status=data.status,
        reason=data.reason,
    )
    db.add(obj)
    db.flush()
    audit(db, "create", "lesson_override", obj.id, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def remove_override(db: Session, override_id: str) -> dict:
    obj = db.get(LessonOverride, override_id)
    if not obj:
        raise not_found("课程覆盖", override_id)
    before = entity_dict(obj)
    db.delete(obj)
    audit(db, "remove", "lesson_override", override_id, before=before, after=None)
    db.commit()
    return {"removed_id": override_id, "restored_to_base": True}


def swap_preview(db: Session, data: LessonSwapRequest) -> dict:
    if data.period_a == data.period_b:
        raise AppError("TIMETABLE_CONFLICT", "互换节次不能相同")
    a = _base_lesson(db, data.class_id, data.lesson_date, data.period_a)
    b = _base_lesson(db, data.class_id, data.lesson_date, data.period_b)
    keys = [lesson_key(data.lesson_date, data.period_a), lesson_key(data.lesson_date, data.period_b)]
    conflicts = list(db.scalars(select(LessonOverride.lesson_key).where(LessonOverride.class_id == data.class_id, LessonOverride.lesson_key.in_(keys))))
    return {
        "changes": [
            {"lesson_key": keys[0], "from": a.subject, "to": b.subject},
            {"lesson_key": keys[1], "from": b.subject, "to": a.subject},
        ],
        "conflicts": conflicts,
        "writes_performed": 0,
    }


def confirm_swap(db: Session, data: LessonSwapRequest) -> list[LessonOverride]:
    preview = swap_preview(db, data)
    if preview["conflicts"]:
        raise AppError("TIMETABLE_CONFLICT", "互换涉及已有覆盖", details={"lesson_keys": preview["conflicts"]})
    a = _base_lesson(db, data.class_id, data.lesson_date, data.period_a)
    b = _base_lesson(db, data.class_id, data.lesson_date, data.period_b)
    try:
        first = create_override(
            db,
            LessonOverrideCreate(
                class_id=data.class_id,
                lesson_date=data.lesson_date,
                period_no=data.period_a,
                replacement_subject=b.subject,
                replacement_teacher=b.teacher,
                replacement_room=b.room,
                reason=data.reason,
            ),
            commit=False,
        )
        second = create_override(
            db,
            LessonOverrideCreate(
                class_id=data.class_id,
                lesson_date=data.lesson_date,
                period_no=data.period_b,
                replacement_subject=a.subject,
                replacement_teacher=a.teacher,
                replacement_room=a.room,
                reason=data.reason,
            ),
            commit=False,
        )
        db.commit()
        return [first, second]
    except Exception:
        db.rollback()
        raise


def batch_change_preview(db: Session, data: LessonBatchChangeRequest) -> dict:
    if data.start_date > data.end_date:
        raise AppError("VALIDATION_ERROR", "end_date不能早于start_date")
    if not data.weekdays or any(day not in range(1, 8) for day in data.weekdays):
        raise AppError("VALIDATION_ERROR", "weekdays只能包含1到7")
    changes = []
    conflicts = []
    current = data.start_date
    while current <= data.end_date:
        if current.isoweekday() in data.weekdays:
            try:
                base = _base_lesson(db, data.class_id, current, data.period_no)
                key = lesson_key(current, data.period_no)
                exists = db.scalar(select(LessonOverride.id).where(LessonOverride.class_id == data.class_id, LessonOverride.lesson_key == key))
                if exists:
                    conflicts.append({"lesson_key": key, "reason": "已有覆盖"})
                changes.append({"lesson_date": current, "lesson_key": key, "original_subject": base.subject, "replacement_subject": data.replacement_subject, "status": data.status})
            except AppError as exc:
                conflicts.append({"lesson_date": current, "reason": exc.message})
        current += timedelta(days=1)
    return {"changes": changes, "conflicts": conflicts, "total": len(changes), "writes_performed": 0}


def confirm_batch_change(db: Session, data: LessonBatchChangeRequest) -> list[LessonOverride]:
    preview = batch_change_preview(db, data)
    if preview["conflicts"]:
        raise AppError("TIMETABLE_CONFLICT", "批量调课存在冲突", details={"conflicts": preview["conflicts"]})
    result = []
    try:
        for row in preview["changes"]:
            result.append(
                create_override(
                    db,
                    LessonOverrideCreate(
                        class_id=data.class_id,
                        lesson_date=row["lesson_date"],
                        period_no=data.period_no,
                        replacement_subject=data.replacement_subject,
                        replacement_teacher=data.replacement_teacher,
                        replacement_room=data.replacement_room,
                        status=data.status,
                        reason=data.reason,
                    ),
                    commit=False,
                )
            )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
