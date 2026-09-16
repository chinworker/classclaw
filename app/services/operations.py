from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.base import new_id
from app.models.entities import (
    Arrangement,
    Attachment,
    AttachmentLink,
    ClassOnboardingSession,
    Reminder,
)
from app.schemas.domain import ArrangementCreate
from app.services.class_student import get_class
from app.services.common import audit, entity_dict
from app.utils.time import now


def create_arrangement(db: Session, data: ArrangementCreate, *, commit: bool = True) -> Arrangement:
    if data.class_id:
        get_class(db, data.class_id)
    obj = Arrangement(**data.model_dump(exclude={"reminder_times"}))
    db.add(obj)
    db.flush()
    for remind_at in data.reminder_times:
        db.add(Reminder(arrangement_id=obj.id, remind_at=remind_at))
    audit(db, "create", "arrangement", obj.id, after=entity_dict(obj), source_message_id=data.source_message_id)
    if commit:
        db.commit()
    return obj


def complete_arrangement(db: Session, arrangement_id: str) -> Arrangement:
    obj = db.get(Arrangement, arrangement_id)
    if not obj:
        raise not_found("安排", arrangement_id)
    before = entity_dict(obj)
    obj.status = "completed"
    obj.completed_at = now()
    db.execute(
        Reminder.__table__.update()
        .where(Reminder.arrangement_id == arrangement_id, Reminder.status == "pending")
        .values(status="cancelled")
    )
    audit(db, "complete", "arrangement", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def refresh_overdue(db: Session) -> int:
    rows = list(
        db.scalars(
            select(Arrangement).where(
                Arrangement.status.in_(["pending", "in_progress"]),
                Arrangement.due_at.is_not(None),
                Arrangement.due_at < now(),
            )
        )
    )
    for obj in rows:
        obj.status = "overdue"
    if rows:
        db.commit()
    return len(rows)


def due_reminders(db: Session, at: datetime | None = None) -> list[Reminder]:
    at = at or now()
    return list(
        db.scalars(
            select(Reminder)
            .join(Arrangement, Arrangement.id == Reminder.arrangement_id)
            .where(
                Reminder.status == "pending",
                Reminder.remind_at <= at,
                Arrangement.status.not_in(["completed", "cancelled"]),
            )
            .order_by(Reminder.remind_at)
        )
    )


def reminder_delivery(db: Session, reminder_id: str, at: datetime | None = None) -> dict:
    reminder = db.get(Reminder, reminder_id)
    if not reminder:
        raise not_found("提醒", reminder_id)
    arrangement = db.get(Arrangement, reminder.arrangement_id)
    if not arrangement:
        raise not_found("安排", reminder.arrangement_id)
    current = at or now()
    remind_at = reminder.remind_at
    if remind_at.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    active = reminder.status == "pending" and arrangement.status not in {"completed", "cancelled"}
    return {
        "reminder": reminder,
        "arrangement": arrangement,
        "active": active,
        "due": active and remind_at <= current,
    }


def mark_reminder(db: Session, reminder_id: str, success: bool, error: str | None = None) -> Reminder:
    obj = db.get(Reminder, reminder_id)
    if not obj:
        raise not_found("提醒", reminder_id)
    if success:
        obj.status = "sent"
        obj.sent_at = now()
        obj.last_error = None
    else:
        obj.status = "failed"
        obj.retry_count += 1
        obj.last_error = error
    db.commit()
    return obj


def save_attachment(db: Session, upload: UploadFile, source_message_id: str | None, description: str | None,
                    *, class_id: str | None = None, onboarding_session_id: str | None = None) -> Attachment:
    from app.services import deletions
    if class_id:
        get_class(db, class_id, include_inactive=True)
        deletions.require_available(db, "class", class_id)
    if onboarding_session_id:
        draft = db.get(ClassOnboardingSession, onboarding_session_id)
        if not draft:
            raise not_found("班级创建引导", onboarding_session_id)
        deletions.require_available(db, "user", draft.owner_user_id)
        deletions.require_available(db, "class", draft.class_id)
    original = Path(upload.filename or "attachment").name
    suffix = Path(original).suffix[:20]
    stored_name = f"{new_id()}{suffix}"
    stamp = now()
    directory = settings.attachment_dir / f"{stamp.year:04d}" / f"{stamp.month:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / stored_name
    hasher = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_attachment_bytes:
                    raise AppError("VALIDATION_ERROR", "附件超过大小限制", 413, {"max_bytes": settings.max_attachment_bytes})
                hasher.update(chunk)
                target.write(chunk)
    except Exception:
        if path.exists():
            path.unlink()
        raise
    obj = Attachment(
        original_name=original,
        stored_name=stored_name,
        stored_path=str(path.relative_to(settings.attachment_dir.parent)),
        mime_type=upload.content_type,
        file_size=size,
        sha256=hasher.hexdigest(),
        source_message_id=source_message_id,
        description=description,
    )
    try:
        db.add(obj)
        db.flush()
        for entity_type, entity_id in (("class", class_id), ("class_onboarding_session", onboarding_session_id)):
            if entity_id:
                db.add(AttachmentLink(attachment_id=obj.id, entity_type=entity_type, entity_id=entity_id))
        audit(db, "create", "attachment", obj.id, after=entity_dict(obj), source_message_id=source_message_id)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return obj


def link_attachment(db: Session, attachment_id: str, entity_type: str, entity_id: str) -> AttachmentLink:
    if not db.get(Attachment, attachment_id):
        raise not_found("附件", attachment_id)
    existing = db.scalar(
        select(AttachmentLink).where(
            AttachmentLink.attachment_id == attachment_id,
            AttachmentLink.entity_type == entity_type,
            AttachmentLink.entity_id == entity_id,
        )
    )
    if existing:
        return existing
    obj = AttachmentLink(attachment_id=attachment_id, entity_type=entity_type, entity_id=entity_id)
    db.add(obj)
    db.commit()
    return obj


def list_class_attachments(db: Session, class_id: str) -> list[dict]:
    rows = db.scalars(
        select(Attachment)
        .join(AttachmentLink, AttachmentLink.attachment_id == Attachment.id)
        .where(AttachmentLink.entity_type == "class", AttachmentLink.entity_id == class_id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
    ).all()
    return [
        {
            "id": row.id,
            "original_name": row.original_name,
            "mime_type": row.mime_type,
            "file_size": row.file_size,
            "description": row.description,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def get_class_attachment(db: Session, attachment_id: str) -> tuple[Attachment, str | None]:
    row = db.get(Attachment, attachment_id)
    if not row:
        raise not_found("附件", attachment_id)
    class_id = db.scalar(
        select(AttachmentLink.entity_id).where(
            AttachmentLink.attachment_id == attachment_id,
            AttachmentLink.entity_type == "class",
        )
    )
    return row, class_id


def delete_attachment(db: Session, attachment: Attachment, *, class_id: str, operator_id: str | None = None) -> dict:
    from app.services import deletion_files
    from app.services.class_student import referenced_attachment_ids

    owners = set(db.scalars(select(AttachmentLink.entity_id).where(
        AttachmentLink.attachment_id == attachment.id, AttachmentLink.entity_type == "class")))
    shared = referenced_attachment_ids(db, {attachment.id}, ignore_entity_types={"class"})
    same_path = db.scalar(select(Attachment.id).where(Attachment.stored_path == attachment.stored_path, Attachment.id != attachment.id))
    if owners != {class_id} or shared or same_path:
        raise AppError("ATTACHMENT_IN_USE", "附件已被其他记录引用，不能删除", 409, {"attachment_id": attachment.id})

    stored_path = attachment.stored_path
    original_name = attachment.original_name
    # Keep ownership metadata until the file is gone. On I/O failure DELETE can
    # be retried; a crash after unlink is safe because an absent file is complete.
    path = deletion_files.safe_path(settings.attachment_dir, settings.attachment_dir.parent / stored_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AppError("ATTACHMENT_DELETE_FAILED", "附件文件删除失败，记录已保留，请重试", 503,
                       {"attachment_id": attachment.id}) from exc
    db.delete(attachment)
    audit(db, "delete", "attachment", attachment.id, operator_id=operator_id, before={"original_name": original_name})
    db.commit()
    return {"id": attachment.id, "original_name": original_name, "file_removed": True}
