from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import (
    ClassAgentBinding,
    ClassOnboardingSession,
    ClassRoom,
    ClassSubject,
    DutyAssignment,
    DutySchedule,
    Exam,
    Homework,
    Student,
    WriteProposal,
)
from app.schemas.domain import (
    ArrangementCreate,
    AttendanceSet,
    ClassCreate,
    ClassOnboardingCreate,
    ClassOnboardingUpdate,
    DutyAssignmentScore,
    DutyConfirmRequest,
    ExamCreate,
    HomeworkBatchStatus,
    HomeworkCreate,
    LessonOverrideCreate,
    PeriodCreate,
    ScoreBatch,
    SeatingCreate,
    StudentCreate,
    StudentEventCreate,
    StudentUpdate,
    TimetableItem,
    TimetableReplace,
    WriteProposalBatchConfirm,
    WriteProposalConfirm,
    WriteProposalCreate,
)
from app.schemas.student_batch import StudentBatchUpdate
from app.services import academic, class_student, duty, operations, seating, student_batch, timetable
from app.services.common import audit, entity_dict
from app.services.openclaw_provisioning import agent_name_for_class
from app.utils.time import now

SUPPORTED_OPERATIONS = {
    "class.onboarding.commit",
    "student.create",
    "student.update",
    "student.update.batch",
    "seating.update",
    "attendance.set",
    "homework.create",
    "homework.status.batch",
    "student_event.create",
    "student_event.batch",
    "exam.create",
    "score.batch",
    "lesson_override.create",
    "arrangement.create",
    "duty.schedule.confirm",
    "duty.assignment.score",
}


def _normalized_class_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def class_name_conflicts(db: Session, class_name: str) -> list[dict[str, str]]:
    target = _normalized_class_name(class_name)
    if not target:
        return []
    rows = db.scalars(select(ClassRoom).where(ClassRoom.deleted_at.is_(None))).all()
    return [
        {"class_id": row.id, "name": row.name, "status": row.status}
        for row in rows
        if _normalized_class_name(row.name) == target
    ]


def ensure_class_name_available(db: Session, class_name: str) -> None:
    conflicts = class_name_conflicts(db, class_name)
    if conflicts:
        raise AppError(
            "CLASS_NAME_CONFLICT",
            f"班级名称“{' '.join(class_name.split())}”已存在，请检查已有班级或使用可区分的名称",
            409,
            {"class_conflicts": conflicts},
        )


def _validation_error(exc: ValidationError) -> AppError:
    return AppError("VALIDATION_ERROR", "写入草稿字段校验失败", details={"errors": exc.errors(include_url=False, include_context=False)})


def _model(model, payload: dict, *, exclude_unset: bool = False) -> dict:
    try:
        validated = model.model_validate(payload)
        return validated.model_dump(mode="json", exclude_unset=exclude_unset)
    except ValidationError as exc:
        raise _validation_error(exc) from exc


def _onboarding_preview(payload: dict, evidence: list[dict] | None = None) -> tuple[dict, dict]:
    class_info = payload.get("class_info") or payload.get("class") or {}
    missing = [name for name in ("name", "grade") if not class_info.get(name)]
    errors: list[dict] = []
    try:
        normalized_class = _model(ClassCreate, class_info) if not missing else class_info
    except AppError as exc:
        normalized_class = class_info
        errors.extend(exc.details.get("errors", []))
    students = payload.get("students") or []
    normalized_students = []
    student_nos: set[str] = set()
    for index, row in enumerate(students):
        student_no = str(row.get("student_no", "")).strip()
        name = str(row.get("name", "")).strip()
        if not student_no or not name:
            errors.append({"field": f"students.{index}", "message": "学生姓名和学号必填"})
            continue
        if student_no in student_nos:
            errors.append({"field": f"students.{index}.student_no", "message": "名单内学号重复", "student_no": student_no})
            continue
        student_nos.add(student_no)
        normalized_students.append({**row, "student_no": student_no, "name": name, "tags": row.get("tags") or [], "status": row.get("status", "active")})
    if not students:
        missing.append("students")
    periods = []
    seen_periods: set[int] = set()
    for index, row in enumerate(payload.get("periods") or []):
        try:
            normalized = _model(PeriodCreate, row)
        except AppError as exc:
            errors.extend({**item, "field": f"periods.{index}"} for item in exc.details.get("errors", []))
            continue
        if normalized["period_no"] in seen_periods:
            errors.append({"field": f"periods.{index}.period_no", "message": "节次重复"})
            continue
        seen_periods.add(normalized["period_no"])
        periods.append(normalized)
    base_timetable = []
    timetable_keys: set[tuple[int, int]] = set()
    class_room = str(class_info.get("room") or "").strip() or None
    for index, row in enumerate(payload.get("base_timetable") or []):
        try:
            item = _model(TimetableItem, {**row, "room": row.get("room") or class_room})
        except AppError as exc:
            errors.extend({**entry, "field": f"base_timetable.{index}"} for entry in exc.details.get("errors", []))
            continue
        if seen_periods and item["period_no"] not in seen_periods:
            errors.append({"field": f"base_timetable.{index}.period_no", "message": "课表引用了未定义节次"})
        key = (item["weekday"], item["period_no"])
        if key in timetable_keys:
            errors.append({"field": f"base_timetable.{index}", "message": "星期与节次重复"})
            continue
        timetable_keys.add(key)
        base_timetable.append(item)
    if not base_timetable:
        missing.append("base_timetable")
    if base_timetable and not periods:
        for period_no in sorted({row["period_no"] for row in base_timetable}):
            periods.append({"period_no": period_no, "name": None, "sort_order": period_no, "enabled": True})
        seen_periods = {row["period_no"] for row in periods}
    subject_rows: dict[str, list[dict]] = {}
    for row in base_timetable:
        subject_rows.setdefault(row["subject"].strip(), []).append(row)
    normalized_subjects = []
    for name, rows in subject_rows.items():
        teachers = {str(row.get("teacher") or "").strip() for row in rows} - {""}
        normalized_subjects.append(
            {
                "name": name,
                "default_full_score": 100,
                "teacher": next(iter(teachers)) if len(teachers) == 1 else None,
                "weekly_periods": len(rows),
                "enabled": True,
            }
        )
    low_confidence = [row for row in (evidence or []) if row.get("confidence") is not None and float(row["confidence"]) < 0.75]
    normalized = {"class_info": normalized_class, "subjects": normalized_subjects, "students": normalized_students, "periods": periods, "base_timetable": base_timetable}
    ready = not missing and not errors
    preview = {
        "ready": ready,
        "title": f"创建班级：{class_info.get('name') or '未命名班级'}",
        "summary": {"class_name": class_info.get("name"), "grade": class_info.get("grade"), "subject_count": len(normalized_subjects), "student_count": len(normalized_students), "period_count": len(periods), "timetable_item_count": len(base_timetable)},
        "missing_fields": sorted(set(missing)),
        "validation_errors": errors,
        "low_confidence_evidence": low_confidence,
        "confirmation_message": f"将创建班级“{class_info.get('name') or '未命名'}”，包含{len(normalized_students)}名学生、从课表归纳的{len(normalized_subjects)}门科目和{len(base_timetable)}条课程。确认后一次性写入，并进入专属 OpenClaw 智能体与微信绑定。",
    }
    return normalized, preview


def normalize_and_preview(operation_type: str, payload: dict, evidence: list[dict] | None = None) -> tuple[dict, dict]:
    if operation_type not in SUPPORTED_OPERATIONS:
        raise AppError("VALIDATION_ERROR", "不支持的写入类型", details={"operation_type": operation_type, "supported": sorted(SUPPORTED_OPERATIONS)})
    if operation_type == "class.onboarding.commit":
        return _onboarding_preview(payload, evidence)
    specs: dict[str, tuple[Any, str]] = {
        "student.create": (StudentCreate, "新增学生"),
        "attendance.set": (AttendanceSet, "登记考勤"),
        "homework.create": (HomeworkCreate, "创建作业"),
        "student_event.create": (StudentEventCreate, "登记学生事件"),
        "exam.create": (ExamCreate, "创建考试"),
        "lesson_override.create": (LessonOverrideCreate, "创建临时调课"),
        "arrangement.create": (ArrangementCreate, "创建日常安排"),
        "duty.schedule.confirm": (DutyConfirmRequest, "确认值日排班"),
    }
    if operation_type in specs:
        model, title = specs[operation_type]
        normalized = _model(model, payload, exclude_unset=operation_type == "attendance.set")
        return normalized, {"ready": True, "title": title, "summary": normalized, "missing_fields": [], "validation_errors": [], "confirmation_message": f"即将{title}，请核对字段后确认。"}
    if operation_type == "student.update.batch":
        normalized = _model(StudentBatchUpdate, payload, exclude_unset=True)
        return normalized, {
            "ready": True, "title": "批量修改学生档案",
            "summary": {"class_id": normalized["class_id"], "record_count": len(normalized["student_ids"]),
                        "changes": normalized["changes"], "only_if_empty": normalized.get("only_if_empty", [])},
            "missing_fields": [], "validation_errors": [], "confirmation_message": "即将批量修改学生档案，请核对完整名单和变更字段。",
        }
    if operation_type == "student.update":
        if not payload.get("student_id"):
            raise AppError("VALIDATION_ERROR", "student.update缺少student_id")
        changes = _model(StudentUpdate, payload.get("changes") or {}, exclude_unset=True)
        normalized = {"student_id": payload["student_id"], "changes": changes}
        return normalized, {"ready": True, "title": "修改学生档案", "summary": normalized, "missing_fields": [], "validation_errors": [], "confirmation_message": "即将修改学生档案，请核对变更字段。"}
    if operation_type == "seating.update":
        if not payload.get("class_id"):
            raise AppError("VALIDATION_ERROR", "seating.update缺少class_id")
        body = _model(SeatingCreate, payload)
        normalized = {"class_id": payload["class_id"], **body}
        return normalized, {"ready": True, "title": "更新座位表", "summary": {"class_id": payload["class_id"], "rows": body["rows"], "cols": body["cols"]}, "missing_fields": [], "validation_errors": [], "confirmation_message": "即将保存新的完整座位快照。"}
    if operation_type in {"homework.status.batch", "score.batch"}:
        id_field = "homework_id" if operation_type.startswith("homework") else "exam_id"
        model = HomeworkBatchStatus if id_field == "homework_id" else ScoreBatch
        if not payload.get(id_field):
            raise AppError("VALIDATION_ERROR", f"缺少{id_field}")
        body = _model(model, payload, exclude_unset=True)
        normalized = {id_field: payload[id_field], **body}
        count = len(body["items"] if "items" in body else body["scores"])
        return normalized, {"ready": True, "title": "批量更新作业状态" if id_field == "homework_id" else "批量保存成绩", "summary": {id_field: payload[id_field], "record_count": count}, "missing_fields": [], "validation_errors": [], "confirmation_message": f"即将批量写入{count}条记录。"}
    if operation_type == "student_event.batch":
        raw_items = payload.get("items") or []
        items = [_model(StudentEventCreate, item) for item in raw_items]
        if not items:
            raise AppError("VALIDATION_ERROR", "事件列表不能为空")
        return {"items": items}, {"ready": True, "title": "批量登记学生事件", "summary": {"record_count": len(items)}, "missing_fields": [], "validation_errors": [], "confirmation_message": f"即将登记{len(items)}条学生事件。"}
    if operation_type == "duty.assignment.score":
        if not payload.get("assignment_id"):
            raise AppError("VALIDATION_ERROR", "duty.assignment.score缺少assignment_id")
        body = _model(DutyAssignmentScore, payload)
        normalized = {"assignment_id": payload["assignment_id"], **body}
        return normalized, {"ready": True, "title": "登记值日评分", "summary": normalized, "missing_fields": [], "validation_errors": [], "confirmation_message": "即将按0至5分登记值日，评分后自动完成。"}
    raise AppError("VALIDATION_ERROR", "操作类型尚未实现")


def create_proposal(db: Session, data: WriteProposalCreate, *, bound_class_id: str | None = None) -> WriteProposal:
    if data.idempotency_key:
        existing = db.scalar(select(WriteProposal).where(WriteProposal.idempotency_key == data.idempotency_key))
        if existing:
            if bound_class_id and _proposal_class_ids(db, existing) != {bound_class_id}:
                raise AppError("CLASS_SCOPE_VIOLATION", "写入预览不属于本次分析的班级", 403)
            return existing
    evidence = [item.model_dump(mode="json") for item in data.evidence]
    normalized, preview = normalize_and_preview(data.operation_type, data.payload, evidence)
    if data.operation_type == "student.update.batch":
        normalized, preview = student_batch.prepare(db, normalized, preview)
    obj = WriteProposal(
        operation_type=data.operation_type,
        payload_json=data.payload,
        normalized_payload_json=normalized,
        preview_json=preview,
        status="pending_review",
        requested_by=data.requested_by,
        source_message_id=data.source_message_id,
        idempotency_key=data.idempotency_key,
        expires_at=now() + timedelta(minutes=data.expires_in_minutes),
        onboarding_session_id=data.onboarding_session_id,
    )
    if bound_class_id and _proposal_class_ids(db, obj) != {bound_class_id}:
        raise AppError("CLASS_SCOPE_VIOLATION", "写入预览不属于本次分析的班级", 403)
    db.add(obj)
    db.flush()
    if data.onboarding_session_id:
        session = db.get(ClassOnboardingSession, data.onboarding_session_id)
        if session:
            session.status = "awaiting_confirmation"
    db.commit()
    return obj


def get_proposal(db: Session, proposal_id: str) -> WriteProposal:
    obj = db.get(WriteProposal, proposal_id)
    if not obj:
        raise not_found("写入预览", proposal_id)
    return obj


def _proposal_class_ids(db: Session, proposal: WriteProposal) -> set[str]:
    payload = proposal.normalized_payload_json or {}
    result: set[str] = set()
    if payload.get("class_id"):
        result.add(str(payload["class_id"]))
    for row in payload.get("items") or []:
        if isinstance(row, dict) and row.get("class_id"):
            result.add(str(row["class_id"]))
    if payload.get("student_id"):
        student = db.get(Student, payload["student_id"])
        if student:
            result.add(student.class_id)
    if payload.get("student_ids"):
        result.update(db.scalars(select(Student.class_id).where(Student.id.in_(payload["student_ids"]))))
    if payload.get("homework_id"):
        homework = db.get(Homework, payload["homework_id"])
        if homework:
            result.add(homework.class_id)
    if payload.get("exam_id"):
        exam = db.get(Exam, payload["exam_id"])
        if exam:
            result.add(exam.class_id)
    if payload.get("assignment_id"):
        assignment = db.get(DutyAssignment, payload["assignment_id"])
        schedule = db.get(DutySchedule, assignment.duty_schedule_id) if assignment else None
        if schedule:
            result.add(schedule.class_id)
    return result


def _execute_onboarding(db: Session, proposal: WriteProposal) -> dict:
    data = proposal.normalized_payload_json
    ensure_class_name_available(db, data["class_info"]["name"])
    session = db.get(ClassOnboardingSession, proposal.onboarding_session_id) if proposal.onboarding_session_id else None
    owner_user_id = session.owner_user_id if session else None
    if owner_user_id and db.scalar(select(ClassRoom.id).where(ClassRoom.owner_user_id == owner_user_id)):
        raise AppError("CLASS_LIMIT_REACHED", "一个班主任账号只能创建并绑定一个班级", 409)
    cls = class_student.create_class(db, ClassCreate.model_validate(data["class_info"]), commit=False)
    cls.owner_user_id = owner_user_id
    for row in data["subjects"]:
        db.add(ClassSubject(class_id=cls.id, **row))
    for row in data["students"]:
        class_student.create_student(db, StudentCreate.model_validate({**row, "class_id": cls.id}), commit=False)
    for row in data["periods"]:
        timetable.create_period(db, cls.id, PeriodCreate.model_validate(row), commit=False)
    if data["base_timetable"]:
        timetable.replace_base_timetable(db, cls.id, TimetableReplace(items=[TimetableItem.model_validate(row) for row in data["base_timetable"]]), commit=False)
    if session:
        session.status = "completed"
        session.class_id = cls.id
        session.current_step = "completed"
    binding = ClassAgentBinding(
        class_id=cls.id,
        agent_name=agent_name_for_class(cls.id),
        workspace_path=str((settings.openclaw_class_workspace_root / cls.id).resolve()),
        channel_id=settings.openclaw_wechat_channel,
        status="pending_agent",
    )
    db.add(binding)
    db.flush()
    return {"class_id": cls.id, "class_name": cls.name, "agent_binding_id": binding.id, "agent_status": binding.status, "student_count": len(data["students"]), "subject_count": len(data["subjects"]), "period_count": len(data["periods"]), "timetable_item_count": len(data["base_timetable"])}


def _execute(db: Session, proposal: WriteProposal) -> Any:
    op, p = proposal.operation_type, proposal.normalized_payload_json
    if op == "class.onboarding.commit":
        return _execute_onboarding(db, proposal)
    if op == "student.create":
        return class_student.create_student(db, StudentCreate.model_validate(p), commit=False)
    if op == "student.update":
        return class_student.update_student(db, p["student_id"], StudentUpdate.model_validate(p["changes"]), commit=False)
    if op == "student.update.batch":
        return student_batch.execute(db, p)
    if op == "seating.update":
        return seating.create_snapshot(db, p["class_id"], SeatingCreate.model_validate(p), commit=False)
    if op == "attendance.set":
        return academic.set_attendance(db, AttendanceSet.model_validate(p), commit=False)
    if op == "homework.create":
        return academic.create_homework(db, HomeworkCreate.model_validate(p), commit=False)
    if op == "homework.status.batch":
        return academic.batch_homework_status(db, p["homework_id"], HomeworkBatchStatus.model_validate(p), commit=False)
    if op == "student_event.create":
        return academic.create_student_event(db, StudentEventCreate.model_validate(p), commit=False)
    if op == "student_event.batch":
        return academic.batch_student_events(db, [StudentEventCreate.model_validate(row) for row in p["items"]], commit=False)
    if op == "exam.create":
        return academic.create_exam(db, ExamCreate.model_validate(p), commit=False)
    if op == "score.batch":
        return academic.save_scores(db, p["exam_id"], ScoreBatch.model_validate(p), commit=False)
    if op == "lesson_override.create":
        return timetable.create_override(db, LessonOverrideCreate.model_validate(p), commit=False)
    if op == "arrangement.create":
        arrangement = operations.create_arrangement(db, ArrangementCreate.model_validate(p), commit=False)
        db.flush()
        from app.models.entities import Reminder

        reminders = list(db.scalars(select(Reminder).where(Reminder.arrangement_id == arrangement.id).order_by(Reminder.remind_at)))
        return {"arrangement": arrangement, "reminders": reminders}
    if op == "duty.schedule.confirm":
        return duty.confirm_schedule(db, DutyConfirmRequest.model_validate(p), commit=False)
    if op == "duty.assignment.score":
        return duty.score_assignment(db, p["assignment_id"], p["score"], p.get("note"), commit=False)
    raise AppError("VALIDATION_ERROR", "操作类型没有执行器", details={"operation_type": op})


def _validate_confirmation(
    db: Session,
    proposal: WriteProposal,
    *,
    revision: int,
    bound_class_id: str | None,
    allow_onboarding: bool,
) -> bool:
    if bound_class_id:
        class_ids = _proposal_class_ids(db, proposal)
        if class_ids != {bound_class_id}:
            raise AppError("CLASS_SCOPE_VIOLATION", "该写入预览不属于当前班级专属智能体", 403, {"proposal_id": proposal.id, "bound_class_id": bound_class_id, "proposal_class_ids": sorted(class_ids)})
    if proposal.status == "completed":
        if revision != proposal.revision:
            raise AppError("PENDING_CONFIRMATION_REQUIRED", "预览版本已变化，请重新核对", 409, {"proposal_id": proposal.id, "expected_revision": proposal.revision})
        return False
    if proposal.status != "pending_review":
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "写入预览当前不可确认", 409, {"proposal_id": proposal.id, "status": proposal.status})
    if proposal.operation_type == "class.onboarding.commit" and not allow_onboarding:
        raise AppError("WEB_ONBOARDING_REQUIRED", "班级创建只能在网页复核页单独确认", 403, {"proposal_id": proposal.id})
    current = now()
    expiry = proposal.expires_at
    if expiry.tzinfo is None:
        current = current.replace(tzinfo=None)
    if expiry < current:
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "写入预览已过期，请重新生成", 409, {"proposal_id": proposal.id})
    if revision != proposal.revision:
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "预览版本已变化，请重新核对", 409, {"proposal_id": proposal.id, "expected_revision": proposal.revision})
    if proposal.onboarding_session_id:
        onboarding = db.get(ClassOnboardingSession, proposal.onboarding_session_id)
        expected_key = f"onboarding:{proposal.onboarding_session_id}:revision:{onboarding.revision}" if onboarding else None
        if not onboarding or onboarding.status != "awaiting_confirmation" or proposal.idempotency_key != expected_key:
            raise AppError("PENDING_CONFIRMATION_REQUIRED", "班级草稿在预览后已变化，请重新生成并核对预览", 409, {"proposal_id": proposal.id})
    if not proposal.preview_json.get("ready"):
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "预览仍有缺失或错误字段，不能写入", 409, {"proposal_id": proposal.id, "preview": proposal.preview_json})
    return True


def _complete_proposal(db: Session, proposal: WriteProposal, *, confirmed_by: str, confirmation_note: str | None) -> None:
    before = entity_dict(proposal)
    result = _execute(db, proposal)
    timestamp = now()
    proposal.status = "completed"
    proposal.confirmed_by = confirmed_by
    proposal.confirmed_at = timestamp
    proposal.executed_at = timestamp
    proposal.result_json = jsonable_encoder(result)
    audit(db, "confirm_and_execute", "write_proposal", proposal.id, before=before, after={"proposal": entity_dict(proposal), "confirmation_note": confirmation_note}, source_message_id=proposal.source_message_id)


def confirm_proposal(db: Session, proposal_id: str, data: WriteProposalConfirm) -> WriteProposal:
    proposal = get_proposal(db, proposal_id)
    should_execute = _validate_confirmation(
        db,
        proposal,
        revision=data.revision,
        bound_class_id=data.bound_class_id,
        allow_onboarding=True,
    )
    if not should_execute:
        return proposal
    try:
        _complete_proposal(db, proposal, confirmed_by=data.confirmed_by, confirmation_note=data.confirmation_note)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return proposal


def confirm_proposals_batch(db: Session, data: WriteProposalBatchConfirm) -> list[WriteProposal]:
    proposal_ids = [item.proposal_id for item in data.items]
    if len(proposal_ids) != len(set(proposal_ids)):
        raise AppError("VALIDATION_ERROR", "批量确认不能包含重复的写入预览")
    proposals = [get_proposal(db, proposal_id) for proposal_id in proposal_ids]
    revisions = {item.proposal_id: item.revision for item in data.items}
    executable = [
        proposal
        for proposal in proposals
        if _validate_confirmation(
            db,
            proposal,
            revision=revisions[proposal.id],
            bound_class_id=data.bound_class_id,
            allow_onboarding=False,
        )
    ]
    try:
        for proposal in executable:
            _complete_proposal(db, proposal, confirmed_by=data.confirmed_by, confirmation_note=data.confirmation_note)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return proposals


def cancel_proposal(db: Session, proposal_id: str, cancelled_by: str | None = None) -> WriteProposal:
    proposal = get_proposal(db, proposal_id)
    if proposal.status != "pending_review":
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "只有待复核预览可以取消", 409)
    proposal.status = "cancelled"
    audit(db, "cancel", "write_proposal", proposal.id, before=None, after={"cancelled_by": cancelled_by})
    db.commit()
    return proposal


def create_onboarding(db: Session, data: ClassOnboardingCreate, owner_user_id: str | None = None) -> ClassOnboardingSession:
    class_name = str(((data.initial_draft or {}).get("class_info") or {}).get("name") or "").strip()
    if class_name:
        ensure_class_name_available(db, class_name)
    if owner_user_id:
        if db.scalar(select(ClassRoom.id).where(ClassRoom.owner_user_id == owner_user_id, ClassRoom.deleted_at.is_(None))):
            raise AppError("CLASS_LIMIT_REACHED", "一个班主任账号只能创建并绑定一个班级", 409)
        existing = db.scalar(
            select(ClassOnboardingSession.id).where(
                ClassOnboardingSession.owner_user_id == owner_user_id,
                ClassOnboardingSession.status.in_(["draft", "awaiting_confirmation"]),
            )
        )
        if existing:
            raise AppError("ONBOARDING_ALREADY_EXISTS", "当前账号已有未完成的班级创建引导", 409, {"session_id": existing})
    obj = ClassOnboardingSession(status="draft", current_step="class_info", draft_json=data.initial_draft, field_evidence_json=data.field_evidence, warnings_json=[], revision=1, created_by=data.created_by, source_message_id=data.source_message_id, owner_user_id=owner_user_id, expires_at=now() + timedelta(days=7))
    db.add(obj)
    db.flush()
    audit(db, "create", "class_onboarding_session", obj.id, after=entity_dict(obj), source_message_id=data.source_message_id)
    db.commit()
    return obj


def get_onboarding(db: Session, session_id: str) -> ClassOnboardingSession:
    obj = db.get(ClassOnboardingSession, session_id)
    if not obj:
        raise not_found("班级创建引导", session_id)
    return obj


def _merge_draft(current: dict, patch: dict, replace_lists: bool) -> dict:
    result = json.loads(json.dumps(current))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = {**result[key], **value}
        elif isinstance(value, list) and isinstance(result.get(key), list) and not replace_lists:
            unique_keys = {"students": "student_no", "subjects": "name", "periods": "period_no"}
            identity = unique_keys.get(key)
            if identity:
                by_key = {str(row.get(identity)): row for row in result[key] if row.get(identity) is not None}
                for row in value:
                    marker = str(row.get(identity))
                    by_key[marker] = {**by_key.get(marker, {}), **row}
                result[key] = list(by_key.values())
            else:
                result[key] = result[key] + value
        else:
            result[key] = value
    return result


def update_onboarding(db: Session, session_id: str, data: ClassOnboardingUpdate) -> ClassOnboardingSession:
    obj = get_onboarding(db, session_id)
    if obj.status not in {"draft", "awaiting_confirmation"}:
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "引导会话已经结束", 409, {"status": obj.status})
    if data.expected_revision != obj.revision:
        raise AppError("PENDING_CONFIRMATION_REQUIRED", "引导内容已被其他客户端更新，请重新载入", 409, {"expected_revision": obj.revision})
    before = entity_dict(obj)
    merged_draft = _merge_draft(obj.draft_json or {}, data.draft_patch, data.replace_lists)
    class_name = str((merged_draft.get("class_info") or {}).get("name") or "").strip()
    if class_name:
        ensure_class_name_available(db, class_name)
    obj.draft_json = merged_draft
    obj.field_evidence_json = _merge_draft(obj.field_evidence_json or {}, data.field_evidence_patch, True)
    if data.current_step:
        obj.current_step = data.current_step
    obj.status = "draft"
    obj.revision += 1
    _, preview = _onboarding_preview(obj.draft_json, list((obj.field_evidence_json or {}).values()))
    obj.warnings_json = preview["validation_errors"] + ([{"missing_fields": preview["missing_fields"]}] if preview["missing_fields"] else [])
    audit(db, "update", "class_onboarding_session", obj.id, before=before, after=entity_dict(obj), source_message_id=obj.source_message_id)
    db.commit()
    return obj


def preview_onboarding(db: Session, session_id: str, requested_by: str | None = None) -> WriteProposal:
    session = get_onboarding(db, session_id)
    class_name = str(((session.draft_json or {}).get("class_info") or {}).get("name") or "").strip()
    if class_name:
        ensure_class_name_available(db, class_name)
    evidence = []
    for key, value in (session.field_evidence_json or {}).items():
        if isinstance(value, dict):
            evidence.append({"source_type": value.get("source_type", "onboarding"), "source_id": value.get("source_id"), "attachment_id": value.get("attachment_id"), "location": value.get("location") or key, "summary": value.get("summary"), "confidence": value.get("confidence")})
    return create_proposal(db, WriteProposalCreate(operation_type="class.onboarding.commit", payload=session.draft_json, evidence=evidence, requested_by=requested_by or session.created_by, source_message_id=session.source_message_id, idempotency_key=f"onboarding:{session.id}:revision:{session.revision}", onboarding_session_id=session.id, expires_in_minutes=60))
