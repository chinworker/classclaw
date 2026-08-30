from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import require_owned_class, require_owned_record
from app.database import get_db
from app.models.entities import BaseTimetable, ClassPeriod, LessonOverride
from app.schemas.domain import LessonBatchChangeRequest, LessonOverrideCreate, LessonSwapRequest, PeriodCreate, TimetableReplace
from app.services import timetable as service


router = APIRouter(tags=["课表与调课"])


@router.post("/classes/{class_id}/periods", status_code=201)
def period_create(request: Request, class_id: str, body: PeriodCreate, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.create_period(db, class_id, body), "节次已创建", 201)


@router.get("/classes/{class_id}/periods")
def period_list(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, list(db.scalars(select(ClassPeriod).where(ClassPeriod.class_id == class_id).order_by(ClassPeriod.sort_order, ClassPeriod.period_no))))


@router.get("/classes/{class_id}/timetable/base")
def timetable_base_get(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, list(db.scalars(select(BaseTimetable).where(BaseTimetable.class_id == class_id).order_by(BaseTimetable.weekday, BaseTimetable.period_no))))


@router.put("/classes/{class_id}/timetable/base")
def timetable_base_update(request: Request, class_id: str, body: TimetableReplace, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.replace_base_timetable(db, class_id, body), "基础课表已替换")


@router.get("/classes/{class_id}/timetable/daily")
def timetable_daily(request: Request, class_id: str, lesson_date: date, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.daily_timetable(db, class_id, lesson_date))


@router.post("/lesson-overrides", status_code=201)
def lesson_override_create(request: Request, body: LessonOverrideCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.create_override(db, body), "临时课程覆盖已创建", 201)


@router.delete("/lesson-overrides/{override_id}")
def lesson_override_remove(request: Request, override_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, LessonOverride, override_id)
    return ok(request, service.remove_override(db, override_id), "临时课程覆盖已撤销")


@router.post("/lesson-swaps/preview")
def lesson_swap_preview(request: Request, body: LessonSwapRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.swap_preview(db, body), "互换预览已生成，未写入数据库")


@router.post("/lesson-swaps/confirm", status_code=201)
def lesson_swap_confirm(request: Request, body: LessonSwapRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.confirm_swap(db, body), "课程互换已确认", 201)


@router.post("/lesson-batch-changes/preview")
def lesson_batch_change_preview(request: Request, body: LessonBatchChangeRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.batch_change_preview(db, body), "批量调课预览已生成，未写入数据库")


@router.post("/lesson-batch-changes/confirm", status_code=201)
def lesson_batch_change_confirm(request: Request, body: LessonBatchChangeRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.confirm_batch_change(db, body), "批量调课已确认", 201)
