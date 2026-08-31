from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import (
    Attachment,
    ClassPeriod,
    ClassOnboardingSession,
    ClassRoom,
    ClassSubject,
    DutySchedule,
    DutyAssignment,
    Exam,
    Homework,
    InteractionAnalysis,
    Student,
    WriteProposal,
)
from app.schemas.domain import InteractionAnalyzeCreate, WriteProposalCreate
from app.services import approval, openclaw_bridge
from app.utils.time import now


def _attachments(db: Session, attachment_ids: list[str]) -> list[Attachment]:
    unique_ids = list(dict.fromkeys(attachment_ids))
    rows = list(db.scalars(select(Attachment).where(Attachment.id.in_(unique_ids)))) if unique_ids else []
    by_id = {row.id: row for row in rows}
    missing = [attachment_id for attachment_id in unique_ids if attachment_id not in by_id]
    if missing:
        raise AppError("NOT_FOUND", "部分附件不存在", 404, {"attachment_ids": missing})
    return [by_id[attachment_id] for attachment_id in unique_ids]


def _context(db: Session, class_id: str | None) -> dict[str, Any]:
    classes = list(db.scalars(select(ClassRoom).where(ClassRoom.deleted_at.is_(None)).order_by(ClassRoom.name).limit(100)))
    result: dict[str, Any] = {
        "current_datetime": now().isoformat(),
        "classes": [{"id": row.id, "name": row.name, "grade": row.grade, "status": row.status} for row in classes],
    }
    if class_id:
        cls = db.get(ClassRoom, class_id)
        if not cls or cls.deleted_at is not None:
            raise not_found("班级", class_id)
        students = list(
            db.scalars(
                select(Student)
                .where(Student.class_id == class_id, Student.deleted_at.is_(None))
                .order_by(Student.student_no)
                .limit(1000)
            )
        )
        subjects = list(db.scalars(select(ClassSubject).where(ClassSubject.class_id == class_id).order_by(ClassSubject.name)))
        periods = list(db.scalars(select(ClassPeriod).where(ClassPeriod.class_id == class_id, ClassPeriod.enabled.is_(True)).order_by(ClassPeriod.period_no)))
        homework = list(db.scalars(select(Homework).where(Homework.class_id == class_id).order_by(Homework.assigned_date.desc()).limit(30)))
        exams = list(db.scalars(select(Exam).where(Exam.class_id == class_id).order_by(Exam.exam_date.desc()).limit(30)))
        duty_schedules = list(db.scalars(select(DutySchedule).where(DutySchedule.class_id == class_id).order_by(DutySchedule.start_date.desc()).limit(20)))
        recent_duty = list(
            db.scalars(
                select(DutyAssignment)
                .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
                .where(DutySchedule.class_id == class_id, DutyAssignment.duty_date.between(now().date() - timedelta(days=2), now().date()))
                .order_by(DutyAssignment.duty_date.desc(), DutyAssignment.item_name)
                .limit(100)
            )
        )
        result["selected_class"] = {"id": cls.id, "name": cls.name, "grade": cls.grade}
        result["students"] = [{"id": row.id, "student_no": row.student_no, "name": row.name} for row in students]
        result["subjects"] = [{"name": row.name, "teacher": row.teacher, "default_full_score": row.default_full_score} for row in subjects]
        result["periods"] = [{"period_no": row.period_no, "name": row.name} for row in periods]
        result["homework"] = [{"id": row.id, "title": row.title, "subject": row.subject, "assigned_date": row.assigned_date.isoformat(), "status": row.status} for row in homework]
        result["exams"] = [{"id": row.id, "name": row.name, "exam_date": row.exam_date.isoformat(), "status": row.status} for row in exams]
        result["duty_schedules"] = [{"id": row.id, "name": row.name, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(), "status": row.status} for row in duty_schedules]
        result["recent_duty_assignments"] = [{"id": row.id, "date": row.duty_date.isoformat(), "item_name": row.item_name, "student_id": row.student_id, "status": row.status, "score": row.score} for row in recent_duty]
    return result


def _result(db: Session, analysis: InteractionAnalysis) -> dict[str, Any]:
    proposals = []
    for proposal_id in analysis.proposal_ids_json or []:
        proposal = db.get(WriteProposal, proposal_id)
        if proposal:
            proposals.append(proposal)
    return {"analysis": analysis, "proposals": proposals}


async def analyze(db: Session, data: InteractionAnalyzeCreate) -> dict[str, Any]:
    if not (data.text and data.text.strip()) and not data.attachment_ids:
        raise AppError("VALIDATION_ERROR", "自然语言或附件至少提供一项", 422)
    idempotency_key = data.idempotency_key or (f"{data.channel}:{data.external_message_id}" if data.external_message_id else None)
    if idempotency_key:
        existing = db.scalar(select(InteractionAnalysis).where(InteractionAnalysis.idempotency_key == idempotency_key))
        if existing:
            return _result(db, existing)

    if data.onboarding_session_id and not db.get(ClassOnboardingSession, data.onboarding_session_id):
        raise not_found("班级创建引导", data.onboarding_session_id)
    attachments = _attachments(db, data.attachment_ids)

    analysis = InteractionAnalysis(
        channel=data.channel,
        input_kind="attachment" if attachments and not data.text else "mixed" if attachments else "natural_language",
        external_message_id=data.external_message_id,
        sender_id=data.sender_id,
        class_id=data.class_id,
        onboarding_session_id=data.onboarding_session_id,
        attachment_ids_json=[row.id for row in attachments],
        status="analyzing",
        requested_by=data.requested_by,
        idempotency_key=idempotency_key,
        analyzed_by=f"openclaw/{openclaw_bridge.extraction_agent_label()}",
    )
    db.add(analysis)
    db.commit()

    try:
        structured = await openclaw_bridge.analyze_interaction(
            analysis_id=analysis.id,
            channel=data.channel,
            raw_text=data.text,
            attachments=attachments,
            context=_context(db, data.class_id),
        )
        warnings = [str(item)[:1000] for item in (structured.get("warnings") or [])]
        questions = [str(item)[:1000] for item in (structured.get("questions") or [])]
        proposal_ids: list[str] = []
        operations = structured.get("operations") or []
        if len(operations) > 10:
            warnings.append("单次最多生成 10 个写入预览，其余操作未处理")
            operations = operations[:10]
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or not isinstance(operation.get("payload"), dict):
                warnings.append(f"第 {index + 1} 个操作格式无效")
                continue
            operation_type = str(operation.get("operation_type") or "")
            try:
                proposal = approval.create_proposal(
                    db,
                    WriteProposalCreate(
                        operation_type=operation_type,
                        payload=operation["payload"],
                        requested_by=data.requested_by,
                        idempotency_key=f"interaction:{analysis.id}:operation:{index}",
                    ),
                )
                proposal_ids.append(proposal.id)
            except AppError as exc:
                warnings.append(f"操作 {index + 1} 未通过后端校验：{exc.message}")
                questions.append(f"请补充或更正操作 {index + 1} 的字段后重新分析")

        analysis.structured_json = structured
        analysis.proposal_ids_json = proposal_ids
        analysis.intent = str(structured.get("intent") or "")[:100] or None
        analysis.summary = str(structured.get("summary") or "")[:2000] or None
        value = structured.get("confidence")
        analysis.confidence = max(0.0, min(1.0, float(value))) if isinstance(value, (int, float)) else None
        analysis.questions_json = list(dict.fromkeys(questions))
        analysis.warnings_json = list(dict.fromkeys(warnings))
        if proposal_ids:
            analysis.status = "awaiting_review"
        elif structured.get("status") == "no_action":
            analysis.status = "no_action"
        else:
            analysis.status = "needs_clarification"
        analysis.analyzed_at = now()
        db.commit()
        return _result(db, analysis)
    except Exception as exc:
        db.rollback()
        failed = db.get(InteractionAnalysis, analysis.id)
        if failed:
            failed.status = "failed"
            failed.error_message = str(exc)[:2000]
            failed.analyzed_at = now()
            db.commit()
        raise


def get(db: Session, analysis_id: str) -> dict[str, Any]:
    analysis = db.get(InteractionAnalysis, analysis_id)
    if not analysis:
        raise not_found("OpenClaw 分析记录", analysis_id)
    return _result(db, analysis)
