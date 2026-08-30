from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.models.entities import ClassOnboardingSession, ClassSubject, WriteProposal
from app.schemas.domain import ClassOnboardingCreate, ClassOnboardingUpdate, WechatLoginWait, WriteProposalBatchConfirm, WriteProposalConfirm, WriteProposalCreate
from app.services import approval as service
from app.services import openclaw_bridge
from app.services import openclaw_provisioning
from app.services import operations as operations_service


router = APIRouter(tags=["强制复核与班级创建引导"])


def _require_web(request: Request) -> None:
    if request.headers.get("X-ClassClaw-Surface") != "web":
        raise AppError("WEB_ONBOARDING_REQUIRED", "新班级只能通过 ClassClaw 网页端创建", 403)


def _require_onboarding_access(request: Request, session: ClassOnboardingSession) -> None:
    principal = principal_from_request(request)
    if not principal.is_admin and session.owner_user_id != principal.user_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前账号的班级创建引导", 403, {"session_id": session.id})


def _require_proposal_access(request: Request, db: Session, proposal: WriteProposal) -> None:
    principal = principal_from_request(request)
    if principal.is_admin:
        return
    if proposal.onboarding_session_id:
        session = db.get(ClassOnboardingSession, proposal.onboarding_session_id)
        if not session:
            raise AppError("CLASS_ACCESS_DENIED", "写入预览没有有效的班级创建引导", 403)
        _require_onboarding_access(request, session)
        return
    class_ids = service._proposal_class_ids(db, proposal)
    if class_ids != ({principal.class_id} if principal.class_id else set()):
        raise AppError("CLASS_ACCESS_DENIED", "写入预览不属于当前班主任账号绑定的班级", 403)


@router.get("/openclaw/status")
async def openclaw_status(request: Request, refresh: bool = False):
    return ok(request, await openclaw_bridge.connection_status(force=refresh))


@router.post("/write-proposals", status_code=201)
def write_proposal_create(request: Request, body: WriteProposalCreate, db: Session = Depends(get_db)):
    proposal = service.create_proposal(db, body)
    try:
        _require_proposal_access(request, db, proposal)
    except AppError:
        db.delete(proposal)
        db.commit()
        raise
    return ok(request, proposal, "写入预览已生成，尚未修改业务数据", 201)


@router.get("/write-proposals/{proposal_id}")
def write_proposal_get(request: Request, proposal_id: str, db: Session = Depends(get_db)):
    proposal = service.get_proposal(db, proposal_id)
    _require_proposal_access(request, db, proposal)
    return ok(request, proposal)


@router.post("/write-proposals/confirm-batch")
def write_proposals_confirm_batch(request: Request, body: WriteProposalBatchConfirm, db: Session = Depends(get_db)):
    principal = principal_from_request(request)
    proposals = [service.get_proposal(db, item.proposal_id) for item in body.items]
    for proposal in proposals:
        _require_proposal_access(request, db, proposal)
    if not principal.is_admin:
        body = body.model_copy(update={"confirmed_by": principal.username})
    return ok(request, service.confirm_proposals_batch(db, body), "用户已在聊天中复核，全部写入执行完成")


@router.post("/write-proposals/{proposal_id}/confirm")
def write_proposal_confirm(request: Request, proposal_id: str, body: WriteProposalConfirm, db: Session = Depends(get_db)):
    proposal = service.get_proposal(db, proposal_id)
    principal = principal_from_request(request)
    if proposal.operation_type == "class.onboarding.commit":
        _require_web(request)
        session = db.get(ClassOnboardingSession, proposal.onboarding_session_id) if proposal.onboarding_session_id else None
        if session:
            _require_onboarding_access(request, session)
        if not principal.is_admin:
            body = body.model_copy(update={"confirmed_by": principal.username})
    elif not principal.is_admin:
        _require_proposal_access(request, db, proposal)
    return ok(request, service.confirm_proposal(db, proposal_id, body), "用户已复核，写入执行完成")


@router.post("/write-proposals/{proposal_id}/cancel")
def write_proposal_cancel(request: Request, proposal_id: str, cancelled_by: str | None = Body(default=None, embed=True), db: Session = Depends(get_db)):
    proposal = service.get_proposal(db, proposal_id)
    _require_proposal_access(request, db, proposal)
    return ok(request, service.cancel_proposal(db, proposal_id, cancelled_by), "写入预览已取消")


@router.post("/class-onboarding/sessions", status_code=201)
def class_onboarding_start(request: Request, body: ClassOnboardingCreate, db: Session = Depends(get_db)):
    _require_web(request)
    principal = principal_from_request(request)
    if not principal.is_admin:
        body = body.model_copy(update={"created_by": principal.username})
    return ok(request, service.create_onboarding(db, body, None if principal.is_admin else principal.user_id), "班级创建引导已开始", 201)


@router.get("/class-onboarding/name-check")
async def class_onboarding_name_check(request: Request, class_name: str = Query(..., min_length=1, max_length=100), db: Session = Depends(get_db)):
    _require_web(request)
    normalized = " ".join(class_name.split())
    class_conflicts = service.class_name_conflicts(db, normalized)
    agent_conflicts = await openclaw_provisioning.agent_name_conflicts(normalized)
    available = not class_conflicts and not agent_conflicts
    if class_conflicts:
        message = f"班级名称“{normalized}”已存在，请先检查已有班级"
    elif agent_conflicts:
        message = f"OpenClaw 中已有使用“{normalized}”命名的智能体，请更换班级名称或先处理旧智能体"
    else:
        message = "班级名称可用；专属智能体将使用独立的 ClassClaw 唯一编号"
    return ok(request, {"available": available, "class_name": normalized, "class_conflicts": class_conflicts, "agent_conflicts": agent_conflicts, "message": message})


@router.get("/class-onboarding/sessions")
def class_onboarding_list(request: Request, status: str | None = None, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    _require_web(request)
    principal = principal_from_request(request)
    stmt = select(ClassOnboardingSession)
    if not principal.is_admin:
        stmt = stmt.where(ClassOnboardingSession.owner_user_id == principal.user_id)
    if status:
        stmt = stmt.where(ClassOnboardingSession.status == status)
    return ok(request, list(db.scalars(stmt.order_by(ClassOnboardingSession.updated_at.desc()).limit(limit))))


@router.get("/class-onboarding/sessions/{session_id}")
def class_onboarding_get(request: Request, session_id: str, db: Session = Depends(get_db)):
    _require_web(request)
    session = service.get_onboarding(db, session_id)
    _require_onboarding_access(request, session)
    return ok(request, session)


@router.patch("/class-onboarding/sessions/{session_id}")
def class_onboarding_update(request: Request, session_id: str, body: ClassOnboardingUpdate, db: Session = Depends(get_db)):
    _require_web(request)
    _require_onboarding_access(request, service.get_onboarding(db, session_id))
    return ok(request, service.update_onboarding(db, session_id, body), "班级引导草稿已更新，尚未创建班级")


@router.post("/class-onboarding/sessions/{session_id}/preview", status_code=201)
def class_onboarding_preview(request: Request, session_id: str, requested_by: str | None = Body(default=None, embed=True), db: Session = Depends(get_db)):
    _require_web(request)
    session = service.get_onboarding(db, session_id)
    _require_onboarding_access(request, session)
    principal = principal_from_request(request)
    if not principal.is_admin:
        requested_by = principal.username
    return ok(request, service.preview_onboarding(db, session_id, requested_by), "班级创建预览已生成，等待用户复核", 201)


@router.post("/class-onboarding/sessions/{session_id}/files")
async def class_onboarding_files(
    request: Request,
    session_id: str,
    target_section: str = Form(...),
    expected_revision: int = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    _require_web(request)
    if not files or len(files) > 8:
        from app.core.errors import AppError

        raise AppError("VALIDATION_ERROR", "每次必须上传 1 至 8 个文件", 422)
    session = service.get_onboarding(db, session_id)
    _require_onboarding_access(request, session)
    attachments = []
    for upload in files:
        attachment = operations_service.save_attachment(db, upload, None, f"班级创建/{target_section}/OpenClaw处理")
        operations_service.link_attachment(db, attachment.id, "class_onboarding_session", session_id)
        attachments.append(attachment)
    result = await openclaw_bridge.analyze_onboarding_files(db, session_id, target_section, attachments, expected_revision)
    return ok(request, result, "文件已由 OpenClaw 分析并填入草稿")


@router.post("/class-onboarding/sessions/{session_id}/analyze-text")
def class_onboarding_analyze_text_removed(request: Request, session_id: str):
    _require_web(request)
    raise AppError("FILE_UPLOAD_REQUIRED", "班级创建已改为文件导入；请上传文件并在结构化预览中编辑", 410, {"session_id": session_id})


@router.get("/classes/{class_id}/agent-binding")
def class_agent_binding_get(request: Request, class_id: str, db: Session = Depends(get_db)):
    _require_web(request)
    require_owned_class(request, class_id)
    return ok(request, openclaw_provisioning.get_binding(db, class_id))


@router.post("/classes/{class_id}/agent-binding/provision")
async def class_agent_provision(request: Request, class_id: str, db: Session = Depends(get_db)):
    _require_web(request)
    require_owned_class(request, class_id)
    binding = await openclaw_provisioning.provision_class_agent(db, class_id)
    return ok(request, binding, "专属智能体已创建；微信绑定为可选项")


@router.post("/classes/{class_id}/agent-binding/start")
async def class_agent_binding_start(request: Request, class_id: str, force: bool = Body(default=False, embed=True), db: Session = Depends(get_db)):
    _require_web(request)
    require_owned_class(request, class_id)
    return ok(request, await openclaw_provisioning.start_wechat_binding(db, class_id, force), "专属智能体已创建，请扫码绑定微信")


@router.post("/classes/{class_id}/agent-binding/wait")
async def class_agent_binding_wait(request: Request, class_id: str, body: WechatLoginWait, db: Session = Depends(get_db)):
    _require_web(request)
    require_owned_class(request, class_id)
    return ok(request, await openclaw_provisioning.wait_wechat_binding(db, class_id, body.current_qr_data_url), "微信绑定状态已刷新")


@router.get("/classes/{class_id}/subjects")
def class_subjects(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, list(db.scalars(select(ClassSubject).where(ClassSubject.class_id == class_id).order_by(ClassSubject.name))))
