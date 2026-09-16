"""Synchronous, resumable cleanup. Business commits and file intents are atomic."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.database import suspend_writer
from app.models.entities import (
    Attachment,
    AttachmentLink,
    ClassAgentBinding,
    ClassOnboardingSession,
    ClassRoom,
    DeletionOperation,
    InteractionAnalysis,
    User,
    WriteProposal,
)
from app.services import class_student, deletion_runtime
from app.services.common import audit
from app.services.deletion_files import safe_path

_running: set[str] = set()


def pending(db, target_type, target_id):
    return db.scalar(select(DeletionOperation).where(DeletionOperation.target_type == target_type,
        DeletionOperation.target_id == target_id, DeletionOperation.status != "complete"))


def require_available(db, target_type, target_id):
    if target_id and not db.info.get("deletion_operation") and pending(db, target_type, target_id):
        raise AppError("DELETION_IN_PROGRESS", "资源正在删除或等待清理，请先完成删除操作", 409)


def public(job):
    return {"id": job.id, "target_type": job.target_type, "target_id": job.target_id, "status": job.status,
            "phase": job.phase, "error_code": job.error_code, "updated_at": job.updated_at,
            "retryable": job.status != "complete" and job.id not in _running}


def prepare(db, target_type, target_id, operator_id=None):
    job = db.scalar(select(DeletionOperation).where(DeletionOperation.target_type == target_type, DeletionOperation.target_id == target_id))
    if job:
        return job
    model = ClassRoom if target_type == "class" else User
    target = db.get(model, target_id)
    if not target:
        raise not_found("班级" if target_type == "class" else "用户", target_id)
    if target_type == "user" and target.role != "head_teacher":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "唯一管理员账号不能删除", 409)
    owner = target.owner_user_id if target_type == "class" else target.id
    job = DeletionOperation(target_type=target_type, target_id=target_id, operator_id=operator_id,
                            owner_user_id=owner, resources_json={}, result_json={})
    db.add(job)
    db.commit()
    return job


def class_plan(db, class_id):
    from app.services import openclaw_provisioning as gateway
    binding = db.scalar(select(ClassAgentBinding).where(ClassAgentBinding.class_id == class_id))
    agent_id = binding.openclaw_agent_id if binding and binding.openclaw_agent_id else gateway.agent_name_for_class(class_id)
    workspace = binding.workspace_path if binding else str(gateway.settings.openclaw_class_workspace_root / class_id)
    channel = binding.channel_id if binding else gateway.settings.openclaw_wechat_channel
    accounts = [binding.channel_account_id] if binding and binding.channel_account_id else []
    plan = {"class_id": class_id, "agent_id": agent_id, "workspace": workspace, "channel": channel, "account_ids": accounts}
    deletion_runtime.validate_paths(plan)
    validate_database_resources(db, plan)
    return plan


def validate_database_resources(db, plan):
    """Claims may change while Gateway I/O releases the writer or between retries."""
    this_path = Path(plan["workspace"]).expanduser().resolve()
    for other in db.scalars(select(ClassAgentBinding).where(ClassAgentBinding.class_id != plan["class_id"])):
        other_path = Path(other.workspace_path).expanduser().resolve()
        if (other.openclaw_agent_id == plan["agent_id"] or other_path == this_path
                or other_path in this_path.parents or this_path in other_path.parents
                or (other.channel_id == plan["channel"] and other.channel_account_id and deletion_runtime.account_key(other.channel_account_id) in plan["account_ids"])):
            raise AppError("DELETION_RESOURCE_SHARED", "待清理资源被其他班级使用", 409)


def _delete_user_data(db, job):
    from app.services import accounts
    drafts = list(db.scalars(select(ClassOnboardingSession).where(ClassOnboardingSession.owner_user_id == job.target_id)))
    # Completed class onboarding belongs to the surviving class, not the deleted login.
    for draft in drafts:
        if draft.class_id:
            draft.owner_user_id = None
    draft_ids = {draft.id for draft in drafts if not draft.class_id}
    candidate = set()
    for draft in drafts:
        if draft.id in draft_ids:
            candidate |= class_student._attachment_ids_from_value(draft.draft_json)
            candidate |= class_student._attachment_ids_from_value(draft.field_evidence_json)
    entity_ids = set(draft_ids)
    for model in (InteractionAnalysis, WriteProposal):
        for row in db.scalars(select(model).where(model.onboarding_session_id.in_(draft_ids))):
            entity_ids.add(row.id)
            for column in model.__table__.columns:
                if column.name.endswith("_json"):
                    value = getattr(row, column.name)
                    candidate |= class_student._attachment_ids_from_value({column.name: value})
            db.delete(row)
    for link in db.scalars(select(AttachmentLink).where(AttachmentLink.entity_id.in_(entity_ids))):
        candidate.add(link.attachment_id)
        db.delete(link)
    for draft in drafts:
        if draft.id in draft_ids:
            db.delete(draft)
    db.flush()
    candidates = candidate - class_student.referenced_attachment_ids(db, candidate)
    files = list(db.scalars(select(Attachment).where(Attachment.id.in_(candidates))))
    paths = [item.stored_path for item in files]
    for item in files:
        db.delete(item)
    result = accounts.delete_teacher(db, job.target_id, operator_id=job.operator_id, commit=False)
    job.resources_json = {**job.resources_json, "attachment_paths": paths}
    job.result_json = result
    job.phase = "files"
    db.commit()


def _busy(job):
    from app.services import agent_chat, ai_tasks
    if job.target_type == "class":
        active = any(key[0] == job.target_id for key in agent_chat._active_conversations)
    else:
        active = any(key[1] == job.target_id for key in agent_chat._active_conversations)
    if active or ai_tasks.has_active(class_id=job.target_id if job.target_type == "class" else None,
                                     user_id=job.target_id if job.target_type == "user" else None):
        raise AppError("DELETION_BUSY", "相关 AI 请求尚未结束，请结束后重试删除", 409)


async def execute(db: Session, job: DeletionOperation):
    if job.status == "complete":
        return {**job.result_json, "deletion_id": job.id, "cleanup_status": "complete"}
    if job.id in _running:
        raise AppError("DELETION_BUSY", "删除操作正在执行", 409)
    _running.add(job.id)
    db.info["deletion_operation"] = job.id
    try:
        _busy(job)
        job.status = "running"
        job.error_code = None
        db.commit()
        if job.phase == "preflight":
            if job.target_type == "class":
                plan = job.resources_json.get("runtime") or class_plan(db, job.target_id)
                async with suspend_writer(db):
                    plan = await deletion_runtime.preflight(plan)
                validate_database_resources(db, plan)
                job.resources_json = {**job.resources_json, "runtime": plan}
            job.phase = "gateway" if job.target_type == "class" else "business"
            db.commit()
        if job.phase == "gateway":
            plan = job.resources_json["runtime"]
            validate_database_resources(db, plan)
            async with suspend_writer(db):
                plan = await deletion_runtime.stop_login(plan)
                plan = await deletion_runtime.preflight(plan)
            validate_database_resources(db, plan)
            job.resources_json = {**job.resources_json, "runtime": plan}
            db.commit()
            async with suspend_writer(db):
                await deletion_runtime.cleanup(plan)
            validate_database_resources(db, plan)
            job.phase = "business"
            db.commit()
        if job.phase == "business":
            if job.target_type == "class":
                class_student.hard_delete_class(db, job.target_id, operator_id=job.operator_id, operation=job)
            else:
                _delete_user_data(db, job)
        if job.phase == "files":
            plan = job.resources_json.get("runtime")
            if plan:
                async with suspend_writer(db):
                    await deletion_runtime.verify(plan)
                validate_database_resources(db, plan)
                result = deletion_runtime.cleanup_files(plan)
                job.result_json = {**job.result_json, "agent_cleanup": result}
            for stored_path in job.resources_json.get("attachment_paths", []):
                # Metadata must not have been reintroduced by a manual repair.
                if db.scalar(select(Attachment.id).where(Attachment.stored_path == stored_path)):
                    raise AppError("DELETION_RESOURCE_SHARED", "附件路径已被重新引用", 409)
                path = safe_path(settings.attachment_dir, settings.attachment_dir.parent / stored_path)
                path.unlink(missing_ok=True)
            job.phase = "complete"
            job.status = "complete"
            audit(db, "complete", "deletion", job.id, operator_id=job.operator_id)
            db.commit()
        return {**job.result_json, "deletion_id": job.id, "cleanup_status": "complete"}
    except (Exception, asyncio.CancelledError) as exc:
        db.rollback()
        job.status = "failed"
        job.error_code = exc.code if isinstance(exc, AppError) else "DELETION_INTERRUPTED" if isinstance(exc, asyncio.CancelledError) else "DELETION_IO_FAILED"
        db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise AppError("DELETION_INCOMPLETE", "删除尚未完成，请查看失败阶段并重试清理", 409 if isinstance(exc, AppError) and exc.status_code == 409 else 503,
                       {"deletion_id": job.id, "phase": job.phase, "cause": job.error_code, "deleted": bool(job.result_json.get("deleted"))}) from exc
    finally:
        db.info.pop("deletion_operation", None)
        _running.discard(job.id)


async def delete_target(db, target_type, target_id, operator_id=None):
    return await execute(db, prepare(db, target_type, target_id, operator_id))
