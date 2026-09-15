from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.analytics import service
from app.core.responses import ok
from app.core.security import require_owned_class, require_owned_student
from app.database import get_db

router = APIRouter(tags=["综合分析与早报"])


@router.get("/analytics/students/{student_id}/comprehensive")
def student_comprehensive_analysis(request: Request, student_id: str, start_date: date | None = None, end_date: date | None = None, subject: str | None = None, event_type: str | None = None, db: Session = Depends(get_db)):
    require_owned_student(request, db, student_id)
    return ok(request, service.student_comprehensive(db, student_id, start_date, end_date, subject, event_type))


@router.get("/analytics/students/{student_id}/compare")
def student_period_compare(request: Request, student_id: str, start_date: date, end_date: date, compare_start_date: date, compare_end_date: date, subject: str | None = None, db: Session = Depends(get_db)):
    require_owned_student(request, db, student_id)
    return ok(request, service.student_compare(db, student_id, start_date, end_date, compare_start_date, compare_end_date, subject))


@router.get("/analytics/classes/{class_id}/comprehensive")
def class_comprehensive_analysis(request: Request, class_id: str, start_date: date | None = None, end_date: date | None = None, subject: str | None = None, event_type: str | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.class_comprehensive(db, class_id, start_date, end_date, subject, event_type))


@router.get("/analytics/classes/{class_id}/compare")
def class_period_compare(request: Request, class_id: str, start_date: date, end_date: date, compare_start_date: date, compare_end_date: date, subject: str | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.class_compare(db, class_id, start_date, end_date, compare_start_date, compare_end_date, subject))


@router.get("/analytics/classes/{class_id}/attention-students")
def class_attention_students(request: Request, class_id: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.attention_students(db, class_id, start_date, end_date))


@router.get("/analytics/classes/{class_id}/cross-module")
def class_cross_module_analysis(request: Request, class_id: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.cross_module(db, class_id, start_date, end_date))


@router.get("/analytics/classes/{class_id}/data-quality")
def class_data_quality(request: Request, class_id: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.data_quality(db, class_id, start_date, end_date))


@router.get("/briefings/morning")
def morning_brief(request: Request, class_id: str, briefing_date: Annotated[date | None, Query(alias="date")] = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.morning_briefing(db, class_id, briefing_date))
