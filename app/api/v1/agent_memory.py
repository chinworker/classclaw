from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import require_owned_class
from app.database import get_db
from app.services import agent_memory

router = APIRouter(tags=["班级 Agent 记忆"])


@router.get("/classes/{class_id}/agent-memories")
def memories(request: Request, class_id: str, date: date | None = None, q: str = Query(default="", max_length=60),
             include_expired: bool = False, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, agent_memory.read(db, class_id, on_date=date, q=q, include_expired=include_expired))
