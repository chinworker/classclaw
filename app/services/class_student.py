from __future__ import annotations

from datetime import timedelta

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import (
    AnalysisCache,
    Arrangement,
    Attachment,
    AttachmentLink,
    AttendanceRecord,
    BaseTimetable,
    ClassAgentBinding,
    ClassOnboardingSession,
    ClassPeriod,
    ClassAgentMemory,
    ClassRoom,
    ClassSubject,
    DeletionOperation,
    DutyAssignment,
    DutyEvaluation,
    DutyEvaluationDetail,
    DutyRule,
    DutySchedule,
    DutyScoreItem,
    Exam,
    ExamSubject,
    Homework,
    HomeworkStudentStatus,
    InteractionAnalysis,
    LessonOverride,
    Reminder,
    Score,
    SeatingSnapshot,
    Student,
    StudentEvent,
    SystemSetting,
    WriteProposal,
)
from app.schemas.domain import ClassCreate, ClassUpdate, StudentCreate, StudentUpdate
from app.services.common import audit, entity_dict
from app.services.student_ordering import student_order_by
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


def _value_references_class(value: object, class_id: str) -> bool:
    if isinstance(value, dict):
        return any((key == "class_id" and item == class_id) or _value_references_class(item, class_id) for key, item in value.items())
    if isinstance(value, list):
        return any(_value_references_class(item, class_id) for item in value)
    return False


def _attachment_ids_from_value(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "attachment_id" and isinstance(item, str):
                found.add(item)
            elif key in {"attachment_ids", "attachment_ids_json"} and isinstance(item, list):
                found.update(str(attachment_id) for attachment_id in item if attachment_id)
            found.update(_attachment_ids_from_value(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_attachment_ids_from_value(item))
    return found


def referenced_attachment_ids(db: Session, attachment_ids: set[str], *, ignore_entity_types: set[str] | None = None) -> set[str]:
    remaining_attachment_ids: set[str] = set()
    if attachment_ids:
        link_query = select(AttachmentLink.attachment_id).where(AttachmentLink.attachment_id.in_(attachment_ids))
        if ignore_entity_types:
            link_query = link_query.where(AttachmentLink.entity_type.notin_(ignore_entity_types))
        remaining_attachment_ids.update(db.scalars(link_query))
        remaining_attachment_ids.update(
            value
            for value in db.scalars(
                select(HomeworkStudentStatus.attachment_id).where(HomeworkStudentStatus.attachment_id.in_(attachment_ids))
            )
            if value
        )
        remaining_attachment_ids.update(
            value
            for value in db.scalars(select(StudentEvent.attachment_id).where(StudentEvent.attachment_id.in_(attachment_ids)))
            if value
        )
        remaining_attachment_ids.update(
            value
            for value in db.scalars(select(Arrangement.attachment_id).where(Arrangement.attachment_id.in_(attachment_ids)))
            if value
        )
        for row in db.scalars(select(InteractionAnalysis)):
            remaining_attachment_ids.update(str(value) for value in (row.attachment_ids_json or []) if value)
            remaining_attachment_ids.update(_attachment_ids_from_value(row.structured_json))
        for row in db.scalars(select(ClassOnboardingSession)):
            remaining_attachment_ids.update(_attachment_ids_from_value(row.draft_json))
            remaining_attachment_ids.update(_attachment_ids_from_value(row.field_evidence_json))
        for proposal in db.scalars(select(WriteProposal)):
            remaining_attachment_ids.update(_attachment_ids_from_value(proposal.payload_json))
            remaining_attachment_ids.update(_attachment_ids_from_value(proposal.normalized_payload_json))
            remaining_attachment_ids.update(_attachment_ids_from_value(proposal.preview_json))
            remaining_attachment_ids.update(_attachment_ids_from_value(proposal.result_json))
    return remaining_attachment_ids


def hard_delete_class(db: Session, class_id: str, *, operator_id: str | None = None, operation: DeletionOperation | None = None) -> dict:
    cls = get_class(db, class_id, include_inactive=True)
    class_name = cls.name
    owner_user_id = cls.owner_user_id

    def ids(model, condition) -> set[str]:
        return set(db.scalars(select(model.id).where(condition)))

    student_ids = ids(Student, Student.class_id == class_id)
    schedule_ids = ids(DutySchedule, DutySchedule.class_id == class_id)
    assignment_ids = ids(DutyAssignment, DutyAssignment.duty_schedule_id.in_(schedule_ids))
    evaluation_ids = ids(DutyEvaluation, DutyEvaluation.duty_schedule_id.in_(schedule_ids))
    evaluation_detail_ids = ids(DutyEvaluationDetail, DutyEvaluationDetail.evaluation_id.in_(evaluation_ids))
    homework_ids = ids(Homework, Homework.class_id == class_id)
    homework_status_ids = ids(HomeworkStudentStatus, HomeworkStudentStatus.homework_id.in_(homework_ids))
    exam_ids = ids(Exam, Exam.class_id == class_id)
    exam_subject_ids = ids(ExamSubject, ExamSubject.exam_id.in_(exam_ids))
    score_ids = ids(Score, Score.exam_id.in_(exam_ids))
    arrangement_ids = ids(Arrangement, Arrangement.class_id == class_id)
    reminder_ids = ids(Reminder, Reminder.arrangement_id.in_(arrangement_ids))
    onboarding_rows = list(db.scalars(select(ClassOnboardingSession).where(ClassOnboardingSession.class_id == class_id)))
    onboarding_ids = {row.id for row in onboarding_rows}
    interaction_rows = list(
        db.scalars(
            select(InteractionAnalysis).where(
                or_(InteractionAnalysis.class_id == class_id, InteractionAnalysis.onboarding_session_id.in_(onboarding_ids))
            )
        )
    )
    interaction_ids = {row.id for row in interaction_rows}
    proposal_rows = list(db.scalars(select(WriteProposal)))
    proposal_ids = {
        proposal.id
        for proposal in proposal_rows
        if proposal.onboarding_session_id in onboarding_ids
        or _value_references_class(proposal.payload_json, class_id)
        or _value_references_class(proposal.normalized_payload_json, class_id)
        or _value_references_class(proposal.preview_json, class_id)
        or _value_references_class(proposal.result_json, class_id)
    }

    entity_ids_by_type = {
        "classes": {class_id},
        "class_agent_bindings": ids(ClassAgentBinding, ClassAgentBinding.class_id == class_id),
        "class_agent_memories": ids(ClassAgentMemory, ClassAgentMemory.class_id == class_id),
        "class_subjects": ids(ClassSubject, ClassSubject.class_id == class_id),
        "students": student_ids,
        "seating_snapshots": ids(SeatingSnapshot, SeatingSnapshot.class_id == class_id),
        "duty_rules": ids(DutyRule, DutyRule.class_id == class_id),
        "duty_schedules": schedule_ids,
        "duty_assignments": assignment_ids,
        "duty_score_items": ids(DutyScoreItem, DutyScoreItem.class_id == class_id),
        "duty_evaluations": evaluation_ids,
        "duty_evaluation_details": evaluation_detail_ids,
        "homework": homework_ids,
        "homework_student_statuses": homework_status_ids,
        "student_events": ids(StudentEvent, StudentEvent.class_id == class_id),
        "attendance_records": ids(AttendanceRecord, AttendanceRecord.class_id == class_id),
        "exams": exam_ids,
        "exam_subjects": exam_subject_ids,
        "scores": score_ids,
        "class_periods": ids(ClassPeriod, ClassPeriod.class_id == class_id),
        "base_timetable": ids(BaseTimetable, BaseTimetable.class_id == class_id),
        "lesson_overrides": ids(LessonOverride, LessonOverride.class_id == class_id),
        "arrangements": arrangement_ids,
        "reminders": reminder_ids,
        "interaction_analyses": interaction_ids,
        "write_proposals": proposal_ids,
        "class_onboarding_sessions": onboarding_ids,
    }
    class_entity_ids = set().union(*entity_ids_by_type.values())

    attachment_links = [link for link in db.scalars(select(AttachmentLink)) if link.entity_id in class_entity_ids]
    attachment_ids = {link.attachment_id for link in attachment_links}
    for row in onboarding_rows:
        attachment_ids.update(_attachment_ids_from_value(row.draft_json))
        attachment_ids.update(_attachment_ids_from_value(row.field_evidence_json))
    for row in interaction_rows:
        attachment_ids.update(str(value) for value in (row.attachment_ids_json or []) if value)
        attachment_ids.update(_attachment_ids_from_value(row.structured_json))
    for proposal in proposal_rows:
        if proposal.id not in proposal_ids:
            continue
        attachment_ids.update(_attachment_ids_from_value(proposal.payload_json))
        attachment_ids.update(_attachment_ids_from_value(proposal.normalized_payload_json))
        attachment_ids.update(_attachment_ids_from_value(proposal.preview_json))
        attachment_ids.update(_attachment_ids_from_value(proposal.result_json))
    if homework_status_ids:
        attachment_ids.update(
            value
            for value in db.scalars(select(HomeworkStudentStatus.attachment_id).where(HomeworkStudentStatus.id.in_(homework_status_ids)))
            if value
        )
    event_ids = entity_ids_by_type["student_events"]
    if event_ids:
        attachment_ids.update(
            value for value in db.scalars(select(StudentEvent.attachment_id).where(StudentEvent.id.in_(event_ids))) if value
        )
    if arrangement_ids:
        attachment_ids.update(
            value for value in db.scalars(select(Arrangement.attachment_id).where(Arrangement.id.in_(arrangement_ids))) if value
        )

    try:
        for link in attachment_links:
            db.delete(link)
        if proposal_ids:
            db.execute(delete(WriteProposal).where(WriteProposal.id.in_(proposal_ids)))
        if interaction_ids:
            db.execute(delete(InteractionAnalysis).where(InteractionAnalysis.id.in_(interaction_ids)))
        if onboarding_ids:
            db.execute(delete(ClassOnboardingSession).where(ClassOnboardingSession.id.in_(onboarding_ids)))
        if arrangement_ids:
            db.execute(delete(Arrangement).where(Arrangement.id.in_(arrangement_ids)))
        if exam_ids:
            db.execute(delete(AnalysisCache).where(AnalysisCache.kind.in_([f"exam_statistics:{exam_id}" for exam_id in exam_ids])))
            db.execute(delete(Exam).where(Exam.id.in_(exam_ids)))
        if homework_ids:
            db.execute(delete(Homework).where(Homework.id.in_(homework_ids)))
        if event_ids:
            db.execute(delete(StudentEvent).where(StudentEvent.id.in_(event_ids)))
        db.execute(delete(AttendanceRecord).where(AttendanceRecord.class_id == class_id))
        if schedule_ids:
            db.execute(delete(DutySchedule).where(DutySchedule.id.in_(schedule_ids)))
        db.execute(delete(DutyRule).where(DutyRule.class_id == class_id))
        db.execute(delete(DutyScoreItem).where(DutyScoreItem.class_id == class_id))
        db.execute(delete(SeatingSnapshot).where(SeatingSnapshot.class_id == class_id))
        db.execute(delete(Student).where(Student.class_id == class_id))

        current = db.scalar(select(SystemSetting).where(SystemSetting.key == "current_class_id"))
        if current and current.value_json == class_id:
            current.value_json = None
        audit(db, "hard_delete", "class", class_id, operator_id=operator_id)
        db.delete(cls)
        db.flush()

        remaining_attachment_ids = referenced_attachment_ids(db, attachment_ids)
        orphan_attachment_ids = attachment_ids - remaining_attachment_ids
        orphan_attachments = (
            list(db.scalars(select(Attachment).where(Attachment.id.in_(orphan_attachment_ids)))) if orphan_attachment_ids else []
        )
        attachment_paths = [attachment.stored_path for attachment in orphan_attachments]
        for attachment in orphan_attachments:
            db.delete(attachment)
        if operation is not None:
            operation.resources_json = {**operation.resources_json, "attachment_paths": attachment_paths}
            operation.phase = "files"
            operation.result_json = {"class_id": class_id, "class_name": class_name, "deleted": True,
                                     "owner_user_id": owner_user_id,
                                     "deleted_counts": {**{k: len(v) for k, v in entity_ids_by_type.items() if v},
                                                        "attachments": len(orphan_attachments)}}
        db.commit()
        if operation is not None:
            return operation.result_json
    except Exception:
        db.rollback()
        raise

    file_warnings = []
    attachment_root = settings.attachment_dir.resolve()
    for stored_path in attachment_paths:
        path = (settings.attachment_dir.parent / stored_path).resolve()
        if path == attachment_root or attachment_root not in path.parents:
            file_warnings.append(f"跳过不安全的附件路径：{stored_path}")
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            file_warnings.append(f"附件文件删除失败：{stored_path}（{exc}）")

    deleted_counts = {name: len(values) for name, values in entity_ids_by_type.items() if values}
    if orphan_attachments:
        deleted_counts["attachments"] = len(orphan_attachments)
    return {
        "class_id": class_id,
        "class_name": class_name,
        "deleted": True,
        "owner_user_id": owner_user_id,
        "deleted_counts": deleted_counts,
        "warnings": file_warnings,
    }


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


def _student_integrity_error(exc: IntegrityError) -> AppError:
    message = str(getattr(exc, "orig", "") or exc)
    if "UNIQUE constraint failed" in message and "students.student_no" in message:
        return AppError("STUDENT_NO_CONFLICT", "同一班级内学号已存在", 409)
    return AppError("STUDENT_SAVE_FAILED", "学生档案保存失败", 500)


def create_student(db: Session, data: StudentCreate, *, commit: bool = True) -> Student:
    get_class(db, data.class_id)
    obj = Student(**data.model_dump())
    db.add(obj)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _student_integrity_error(exc) from exc
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
        raise _student_integrity_error(exc) from exc
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
    student_no: str | None = None,
) -> dict:
    stmt = select(Student).where(Student.deleted_at.is_(None))
    if class_id:
        stmt = stmt.where(Student.class_id == class_id)
    if student_no:
        stmt = stmt.where(Student.student_no == student_no.strip())
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
    items = list(db.scalars(stmt.order_by(*student_order_by()).offset((page - 1) * page_size).limit(page_size)))
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
