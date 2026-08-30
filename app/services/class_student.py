from __future__ import annotations

from datetime import timedelta

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import (
    AttendanceRecord,
    ClassRoom,
    DutyAssignment,
    HomeworkStudentStatus,
    Score,
    SeatingSnapshot,
    Student,
    StudentEvent,
    SystemSetting,
)
from app.schemas.domain import ClassCreate, ClassUpdate, StudentCreate, StudentUpdate
from app.services.common import audit, entity_dict
from app.utils.time import now, today


def get_class(db: Session, class_id: str, include_inactive: bool = False) -> ClassRoom:
    obj = db.get(ClassRoom, class_id)
    if not obj or obj.deleted_at or (not include_inactive and obj.status != "active"):
        raise not_found("班级", class_id)
    return obj


def create_class(db: Session, data: ClassCreate, *, commit: bool = True) -> ClassRoom:
    obj = ClassRoom(**data.model_dump())
    db.add(obj)
    db.flush()
    audit(db, "create", "class", obj.id, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def update_class(db: Session, class_id: str, data: ClassUpdate) -> ClassRoom:
    obj = get_class(db, class_id, include_inactive=True)
    before = entity_dict(obj)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    audit(db, "update", "class", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def list_classes(db: Session, page: int, page_size: int, status: str | None = None, owner_user_id: str | None = None) -> dict:
    stmt = select(ClassRoom).where(ClassRoom.deleted_at.is_(None))
    if owner_user_id:
        stmt = stmt.where(ClassRoom.owner_user_id == owner_user_id)
    if status:
        stmt = stmt.where(ClassRoom.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(ClassRoom.created_at.desc()).offset((page - 1) * page_size).limit(page_size)))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def set_current_class(db: Session, class_id: str) -> ClassRoom:
    cls = get_class(db, class_id)
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "current_class_id"))
    before = setting.value_json if setting else None
    if not setting:
        setting = SystemSetting(key="current_class_id", value_json=class_id)
        db.add(setting)
    else:
        setting.value_json = class_id
    audit(db, "set_current", "class", class_id, before={"class_id": before}, after={"class_id": class_id})
    db.commit()
    return cls


def class_summary(db: Session, class_id: str) -> dict:
    cls = get_class(db, class_id, include_inactive=True)
    student_count = db.scalar(select(func.count(Student.id)).where(Student.class_id == class_id, Student.deleted_at.is_(None))) or 0
    recent_events = db.scalar(
        select(func.count(StudentEvent.id)).where(
            StudentEvent.class_id == class_id,
            StudentEvent.deleted_at.is_(None),
            StudentEvent.event_date >= today() - timedelta(days=7),
        )
    ) or 0
    return {"class": cls, "student_count": student_count, "events_last_7_days": recent_events}


def get_student(db: Session, student_id: str, include_deleted: bool = False) -> Student:
    obj = db.get(Student, student_id)
    if not obj or (obj.deleted_at and not include_deleted):
        raise AppError("STUDENT_NOT_FOUND", "学生不存在", 404, {"student_id": student_id})
    return obj


def create_student(db: Session, data: StudentCreate, *, commit: bool = True) -> Student:
    get_class(db, data.class_id)
    obj = Student(**data.model_dump())
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("STUDENT_NO_CONFLICT", "同一班级内学号已存在", 409) from exc
    audit(db, "create", "student", obj.id, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def update_student(db: Session, student_id: str, data: StudentUpdate, *, commit: bool = True) -> Student:
    obj = get_student(db, student_id)
    before = entity_dict(obj)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("STUDENT_NO_CONFLICT", "同一班级内学号已存在", 409) from exc
    audit(db, "update", "student", obj.id, before=before, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def delete_student(db: Session, student_id: str) -> Student:
    obj = get_student(db, student_id)
    before = entity_dict(obj)
    obj.deleted_at = now()
    obj.status = "inactive"
    audit(db, "soft_delete", "student", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def search_students(
    db: Session,
    *,
    class_id: str | None,
    query: str | None,
    tag: str | None,
    status: str | None,
    page: int,
    page_size: int,
    exact_name: bool = False,
) -> dict:
    stmt = select(Student).where(Student.deleted_at.is_(None))
    if class_id:
        stmt = stmt.where(Student.class_id == class_id)
    if query:
        if exact_name:
            stmt = stmt.where(Student.name == query)
        else:
            pattern = f"%{query}%"
            stmt = stmt.where(or_(Student.name.like(pattern), Student.student_no.like(pattern)))
    if tag:
        stmt = stmt.where(cast(Student.tags, String).like(f'%"{tag}"%'))
    if status:
        stmt = stmt.where(Student.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(Student.student_no).offset((page - 1) * page_size).limit(page_size)))
    result = {"items": items, "total": total, "page": page, "page_size": page_size}
    if exact_name and total > 1:
        result["ambiguous"] = True
        result["error_code"] = "STUDENT_AMBIGUOUS"
    return result


def student_detail(db: Session, student_id: str) -> dict:
    student = get_student(db, student_id, include_deleted=True)
    snapshot = db.scalar(
        select(SeatingSnapshot)
        .where(SeatingSnapshot.class_id == student.class_id)
        .order_by(SeatingSnapshot.snapshot_at.desc(), SeatingSnapshot.created_at.desc())
        .limit(1)
    )
    seat = None
    if snapshot:
        for r, row in enumerate(snapshot.layout_json):
            for c, sid in enumerate(row):
                if sid == student_id:
                    seat = {"row": r + 1, "col": c + 1, "snapshot_id": snapshot.id}
    recent_events = list(
        db.scalars(
            select(StudentEvent)
            .where(StudentEvent.student_id == student_id, StudentEvent.deleted_at.is_(None))
            .order_by(StudentEvent.event_date.desc(), StudentEvent.created_at.desc())
            .limit(10)
        )
    )
    attendance = list(
        db.scalars(select(AttendanceRecord).where(AttendanceRecord.student_id == student_id).order_by(AttendanceRecord.attendance_date.desc()).limit(30))
    )
    homework = list(db.scalars(select(HomeworkStudentStatus).where(HomeworkStudentStatus.student_id == student_id).order_by(HomeworkStudentStatus.updated_at.desc()).limit(20)))
    scores = list(db.scalars(select(Score).where(Score.student_id == student_id).order_by(Score.created_at.desc()).limit(30)))
    duties = list(db.scalars(select(DutyAssignment).where(DutyAssignment.student_id == student_id).order_by(DutyAssignment.duty_date.desc()).limit(10)))
    attention = [e for e in recent_events if e.sentiment == "negative" or e.severity in {"attention", "serious"}]
    return {
        "student": student,
        "current_seat": seat,
        "recent_duty": duties,
        "homework": homework,
        "attendance": attendance,
        "events": recent_events,
        "scores": scores,
        "attachment_summary": {"count": 0},
        "attention_items": attention[:5],
    }
