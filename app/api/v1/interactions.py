from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.schemas.domain import InteractionAnalyzeCreate
from app.services import interactions as service


router = APIRouter(tags=["OpenClaw 统一输入分析"])


@router.post("/interaction-analyses", status_code=201)
async def interaction_analyze(request: Request, body: InteractionAnalyzeCreate, db: Session = Depends(get_db)):
    principal = principal_from_request(request)
    if not principal.is_admin:
        if body.class_id:
            require_owned_class(request, body.class_id)
        else:
            body = body.model_copy(update={"class_id": principal.class_id})
    result = await service.analyze(db, body)
    return ok(request, result, "输入已由 OpenClaw 清洗；有效写入已生成待复核预览", 201)


@router.get("/interaction-analyses/{analysis_id}")
def interaction_get(request: Request, analysis_id: str, db: Session = Depends(get_db)):
    analysis = service.get(db, analysis_id)
    if analysis.class_id:
        require_owned_class(request, analysis.class_id)
    return ok(request, analysis)
