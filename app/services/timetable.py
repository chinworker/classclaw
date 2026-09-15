from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import BaseTimetable, ClassPeriod, ClassSubject, LessonOverride
from app.schemas.domain import (
    LessonBatchChangeRequest,
    LessonOverrideCreate,
    LessonSwapRequest,
    PeriodCreate,
    TimetableImportApply,
    TimetableReplace,
)
from app.services.class_student import get_class
from app.services.common import audit, entity_dict

_CN_DIGITS = "零一二三四五六七八九"


def default_period_name(period_no: int) -> str:
    if period_no < 10:
        number = _CN_DIGITS[period_no]
    elif period_no < 20:
        number = "十" + (_CN_DIGITS[period_no % 10] if period_no % 10 else "")
    elif period_no < 100:
        number = _CN_DIGITS[period_no // 10] + "十" + (_CN_DIGITS[period_no % 10] if period_no % 10 else "")
    else:
        number = str(period_no)
    return f"第{number}节"


def period_display_name(period_no: int, name: str | None = None) -> str:
    return name.strip() if name and name.strip() else default_period_name(period_no)


def lesson_key(lesson_date: date, period_no: int) -> str:
    if period_no < 1 or period_no > 99:
        raise AppError("VALIDATION_ERROR", "period_no必须在1到99之间")
    return f"{lesson_date.isoformat()}-P{period_no:02d}"


def create_period(db: Session, class_id: str, data: PeriodCreate, *, commit: bool = True) -> ClassPeriod:
    get_class(db, class_id)
    values = data.model_dump()
    values["name"] = data.name.strip() if data.name and data.name.strip() else None
    obj = ClassPeriod(class_id=class_id, **values)
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
    sync_subjects_from_timetable(db, class_id, data.items)
    audit(db, "replace", "base_timetable", class_id, before={"items": before}, after={"items": [entity_dict(x) for x in result]})
    if commit:
        db.commit()
    return result


def sync_subjects_from_timetable(db: Session, class_id: str, items: list) -> list[ClassSubject]:
    """Ensure every course appearing in the timetable is available to subject selectors."""
    existing = {row.name: row for row in db.scalars(select(ClassSubject).where(ClassSubject.class_id == class_id))}
    grouped: dict[str, list] = {}
    for item in items:
        name = item.subject.strip()
        if name:
            grouped.setdefault(name, []).append(item)
    result: list[ClassSubject] = []
    for name, lessons in grouped.items():
        teachers = [lesson.teacher.strip() for lesson in lessons if lesson.teacher and lesson.teacher.strip()]
        subject = existing.get(name)
        if subject is None:
            subject = ClassSubject(
                class_id=class_id,
                name=name,
                teacher=max(set(teachers), key=teachers.count) if teachers else None,
                weekly_periods=len(lessons),
                enabled=True,
            )
            db.add(subject)
        else:
            subject.enabled = True
            subject.weekly_periods = len(lessons)
            if not subject.teacher and teachers:
                subject.teacher = max(set(teachers), key=teachers.count)
        result.append(subject)
    db.flush()
    return result


def apply_imported_timetable(db: Session, class_id: str, data: TimetableImportApply) -> dict:
    """Apply a reviewed file preview in one transaction, updating period labels and replacing the base table."""
    get_class(db, class_id)
    seen: set[int] = set()
    for period in data.periods:
        if period.period_no in seen:
            raise AppError("TIMETABLE_CONFLICT", "识别结果中有重复节次")
        seen.add(period.period_no)
    existing = {row.period_no: row for row in db.scalars(select(ClassPeriod).where(ClassPeriod.class_id == class_id))}
    try:
        for period in data.periods:
            row = existing.get(period.period_no)
            if row:
                row.name = period.name.strip() if period.name and period.name.strip() else None
                row.sort_order = period.sort_order
                row.enabled = period.enabled
            else:
                values = period.model_dump()
                values["name"] = period.name.strip() if period.name and period.name.strip() else None
                db.add(ClassPeriod(class_id=class_id, **values))
        rows = replace_base_timetable(db, class_id, TimetableReplace(items=data.items), commit=False)
        subjects = list(db.scalars(select(ClassSubject).where(ClassSubject.class_id == class_id, ClassSubject.enabled.is_(True)).order_by(ClassSubject.name)))
        db.commit()
        return {"periods_updated": len(data.periods), "courses_saved": len(rows), "subjects": subjects, "items": rows}
    except Exception:
        db.rollback()
        raise


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
                "period_name": period_display_name(base.period_no, periods.get(base.period_no).name if periods.get(base.period_no) else None),
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
    obj = db.scalar(select(LessonOverride).where(LessonOverride.class_id == data.class_id, LessonOverride.lesson_key == key))
    if obj:
        before = entity_dict(obj)
        obj.original_subject = base.subject
        obj.original_teacher = base.teacher
        obj.replacement_subject = data.replacement_subject
        obj.replacement_teacher = data.replacement_teacher
        obj.replacement_room = data.replacement_room
        obj.status = data.status
        obj.reason = data.reason
        action = "update"
    else:
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
        before = None
        action = "create"
    db.flush()
    audit(db, action, "lesson_override", obj.id, before=before, after=entity_dict(obj))
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


def _effective_lesson(db: Session, class_id: str, lesson_date: date, period_no: int) -> dict:
    base = _base_lesson(db, class_id, lesson_date, period_no)
    key = lesson_key(lesson_date, period_no)
    override = db.scalar(select(LessonOverride).where(LessonOverride.class_id == class_id, LessonOverride.lesson_key == key))
    cancelled = bool(override and override.status == "cancelled")
    return {
        "lesson_key": key,
        "lesson_date": lesson_date,
        "period_no": period_no,
        "subject": None if cancelled else (override.replacement_subject if override and override.replacement_subject is not None else base.subject),
        "teacher": None if cancelled else (override.replacement_teacher if override and override.replacement_teacher is not None else base.teacher),
        "room": None if cancelled else (override.replacement_room if override and override.replacement_room is not None else base.room),
        "is_changed": bool(override),
        "is_cancelled": cancelled,
    }


def swap_preview(db: Session, data: LessonSwapRequest) -> dict:
    date_a = data.lesson_date_a or data.lesson_date
    date_b = data.lesson_date_b or data.lesson_date
    assert date_a is not None and date_b is not None
    a = _effective_lesson(db, data.class_id, date_a, data.period_a)
    b = _effective_lesson(db, data.class_id, date_b, data.period_b)
    if a["is_cancelled"] or b["is_cancelled"]:
        raise AppError("TIMETABLE_CONFLICT", "已取消的课程不能参与互换，请先恢复或重新调整")
    return {
        "changes": [
            {"lesson_key": a["lesson_key"], "from": a["subject"], "to": b["subject"]},
            {"lesson_key": b["lesson_key"], "from": b["subject"], "to": a["subject"]},
        ],
        "lessons": {"a": a, "b": b},
        "conflicts": [],
        "writes_performed": 0,
    }


def confirm_swap(db: Session, data: LessonSwapRequest) -> list[LessonOverride]:
    preview = swap_preview(db, data)
    a = preview["lessons"]["a"]
    b = preview["lessons"]["b"]
    try:
        first = create_override(
            db,
            LessonOverrideCreate(
                class_id=data.class_id,
                lesson_date=a["lesson_date"],
                period_no=data.period_a,
                replacement_subject=b["subject"],
                replacement_teacher=b["teacher"],
                replacement_room=b["room"],
                reason=data.reason,
            ),
            commit=False,
        )
        second = create_override(
            db,
            LessonOverrideCreate(
                class_id=data.class_id,
                lesson_date=b["lesson_date"],
                period_no=data.period_b,
                replacement_subject=a["subject"],
                replacement_teacher=a["teacher"],
                replacement_room=a["room"],
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
