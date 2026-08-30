from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.errors import AppError
from app.core.security import principal_from_request, require_owned_record, scoped_class_id
from app.database import get_db
from app.models.entities import Arrangement, AttachmentLink, AuditLog
from app.schemas.domain import ArrangementCreate, AttachmentLinkCreate
from app.services import operations as service


router = APIRouter(tags=["安排、附件与审计"])


@router.post("/arrangements", status_code=201)
def arrangement_create(request: Request, body: ArrangementCreate, db: Session = Depends(get_db)):
    class_id = scoped_class_id(request, body.class_id)
    if class_id != body.class_id:
        body = body.model_copy(update={"class_id": class_id})
    return ok(request, service.create_arrangement(db, body), "安排已创建", 201)


@router.get("/arrangements")
def arrangement_query(request: Request, class_id: str | None = None, status: str | None = None, due_before: datetime | None = None, db: Session = Depends(get_db)):
    class_id = scoped_class_id(request, class_id)
    service.refresh_overdue(db)
    stmt = select(Arrangement)
    if class_id:
        stmt = stmt.where((Arrangement.class_id == class_id) | Arrangement.class_id.is_(None))
    if status:
        stmt = stmt.where(Arrangement.status == status)
    if due_before:
        stmt = stmt.where(Arrangement.due_at <= due_before)
    return ok(request, list(db.scalars(stmt.order_by(Arrangement.due_at))))


@router.post("/arrangements/{arrangement_id}/complete")
def arrangement_complete(request: Request, arrangement_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, Arrangement, arrangement_id)
    return ok(request, service.complete_arrangement(db, arrangement_id), "安排已完成")


@router.get("/reminders/due")
def due_reminders(request: Request, at: datetime | None = None, db: Session = Depends(get_db)):
    return ok(request, service.due_reminders(db, at))


@router.get("/reminders/{reminder_id}")
def reminder_get(request: Request, reminder_id: str, db: Session = Depends(get_db)):
    result = service.reminder_delivery(db, reminder_id)
    require_owned_record(request, db, Arrangement, result["arrangement"].id)
    return ok(request, result)


@router.post("/reminders/{reminder_id}/sent")
def reminder_sent(request: Request, reminder_id: str, bound_class_id: str | None = Body(default=None, embed=True), db: Session = Depends(get_db)):
    result = service.reminder_delivery(db, reminder_id)
    require_owned_record(request, db, Arrangement, result["arrangement"].id)
    if bound_class_id and result["arrangement"].class_id != bound_class_id:
        raise AppError("CLASS_SCOPE_VIOLATION", "该提醒不属于当前班级专属智能体", 403)
    return ok(request, service.mark_reminder(db, reminder_id, True), "提醒已标记发送成功")


@router.post("/reminders/{reminder_id}/failed")
def reminder_failed(request: Request, reminder_id: str, error: str | None = None, db: Session = Depends(get_db)):
    return ok(request, service.mark_reminder(db, reminder_id, False, error), "提醒已标记发送失败")


@router.get("/audit-logs")
def audit_list(request: Request, entity_type: str | None = None, entity_id: str | None = None, db: Session = Depends(get_db)):
    if not principal_from_request(request).is_admin:
        from app.core.errors import AppError

        raise AppError("ADMIN_REQUIRED", "仅管理员可以查看审计日志", 403)
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return ok(request, list(db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(200))))


@router.post("/attachments", status_code=201)
def attachment_upload(
    request: Request,
    file: UploadFile = File(...),
    source_message_id: str | None = Form(None),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
):
    return ok(request, service.save_attachment(db, file, source_message_id, description), "附件已保存", 201)


@router.post("/attachments/{attachment_id}/links", status_code=201)
def attachment_link(request: Request, attachment_id: str, body: AttachmentLinkCreate, db: Session = Depends(get_db)):
    return ok(request, service.link_attachment(db, attachment_id, body.entity_type, body.entity_id), "附件关联已创建", 201)
