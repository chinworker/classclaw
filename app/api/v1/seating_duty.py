from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import require_owned_class, require_owned_record, require_owned_student
from app.database import get_db
from app.models.entities import DutyAssignment, DutyRule, DutySchedule, DutyScoreItem, SeatingSnapshot, Student
from app.schemas.domain import (
    DutyAssignmentScore,
    DutyConfirmRequest,
    DutyEvaluationCreate,
    DutyPreviewRequest,
    DutyReplaceRequest,
    DutyRuleCreate,
    DutyRuleUpdate,
    SeatingCreate,
    SeatingRename,
    SeatingSwap,
)
from app.services import admin_console, ai_tasks, duty, openclaw_bridge, operations, seating
from app.services.common import audit, entity_dict
from app.services.student_ordering import student_order_by
from app.utils.time import today

router = APIRouter(tags=["座位与值日"])


@router.get("/classes/{class_id}/seating/current")
def seat_current_get(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, seating.current_snapshot(db, class_id, required=False))


@router.get("/classes/{class_id}/seating/history")
def seat_history_list(request: Request, class_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    stmt = select(SeatingSnapshot).where(SeatingSnapshot.class_id == class_id).order_by(SeatingSnapshot.snapshot_at.desc())
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    total = len(list(db.scalars(select(SeatingSnapshot.id).where(SeatingSnapshot.class_id == class_id))))
    return ok(request, {"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/seating/{snapshot_id}")
def seat_snapshot_get(request: Request, snapshot_id: str, db: Session = Depends(get_db)):
    obj = require_owned_record(request, db, SeatingSnapshot, snapshot_id)
    return ok(request, obj)


@router.patch("/seating/{snapshot_id}")
def seat_snapshot_rename(request: Request, snapshot_id: str, body: SeatingRename, db: Session = Depends(get_db)):
    require_owned_record(request, db, SeatingSnapshot, snapshot_id)
    return ok(request, seating.rename_snapshot(db, snapshot_id, body.name), "座位表已重命名")


@router.delete("/seating/{snapshot_id}")
def seat_snapshot_delete(request: Request, snapshot_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, SeatingSnapshot, snapshot_id)
    return ok(request, seating.delete_snapshot(db, snapshot_id), "座位表已删除")


@router.post("/classes/{class_id}/seating", status_code=201)
def seat_update(request: Request, class_id: str, body: SeatingCreate, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, seating.create_snapshot(db, class_id, body), "座位快照已保存", 201)


@router.post("/classes/{class_id}/seating/import-preview")
async def seat_import_preview(request: Request, class_id: str, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    admin_console.require_feature("feature.file_analysis")
    if not files or len(files) > 4:
        raise AppError("VALIDATION_ERROR", "每次请上传 1 至 4 个座位表文件", 422)
    async with ai_tasks.track(request, class_id=class_id) as cancelled:
        attachments = []
        for upload in files:
            attachment = operations.save_attachment(db, upload, None, "座位表文件识别", class_id=class_id)
            attachments.append(attachment)
        result = await openclaw_bridge.analyze_seating_files(db, class_id, attachments, cancelled=cancelled)
        message = "座位表文件已整理，请核对预览后保存" if result["analysis"]["accepted"] else "AI 认为座位表数据不够可靠，未采用本次结果"
        return ok(request, result, message)


@router.post("/classes/{class_id}/seating/swap", status_code=201)
def seat_swap(request: Request, class_id: str, body: SeatingSwap, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    require_owned_student(request, db, body.student_a_id)
    require_owned_student(request, db, body.student_b_id)
    return ok(request, seating.swap_students(db, class_id, body), "座位已交换", 201)


@router.post("/classes/{class_id}/seating/restore/{snapshot_id}", status_code=201)
def seat_restore(request: Request, class_id: str, snapshot_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    require_owned_record(request, db, SeatingSnapshot, snapshot_id)
    return ok(request, seating.restore_snapshot(db, class_id, snapshot_id), "历史座位已复制为新快照", 201)


@router.post("/duty/rules/validate")
def duty_rule_validate(request: Request, rule_json: dict = Body(...), db: Session = Depends(get_db)):
    return ok(request, duty.validate_rule_json(rule_json))


@router.post("/duty/rules", status_code=201)
def duty_rule_save(request: Request, body: DutyRuleCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, duty.save_rule(db, body), "值日规则已保存", 201)


@router.get("/duty/rules")
def duty_rule_list(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    stmt = select(DutyRule).where(DutyRule.class_id == class_id).order_by(DutyRule.created_at.desc())
    return ok(request, list(db.scalars(stmt)))


@router.patch("/duty/rules/{rule_id}")
def duty_rule_update(request: Request, rule_id: str, body: DutyRuleUpdate, db: Session = Depends(get_db)):
    require_owned_record(request, db, DutyRule, rule_id)
    return ok(request, duty.update_rule(db, rule_id, body), "值日规则已更新")


@router.delete("/duty/rules/{rule_id}")
def duty_rule_delete(request: Request, rule_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, DutyRule, rule_id)
    return ok(request, duty.delete_rule(db, rule_id), "值日规则已删除")


@router.post("/duty/schedules/preview")
def duty_schedule_preview(request: Request, body: DutyPreviewRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, duty.generate_preview(db, body), "预览已生成，尚未写入正式排班")


@router.post("/duty/schedules/confirm", status_code=201)
def duty_schedule_confirm(request: Request, body: DutyConfirmRequest, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, duty.confirm_schedule(db, body), "值日排班已确认", 201)


@router.get("/duty/assignments")
def duty_assignments(
    request: Request,
    class_id: str,
    duty_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    student_id: str | None = None,
    db: Session = Depends(get_db),
):
    require_owned_class(request, class_id)
    duty.complete_overdue_assignments(db, class_id)
    stmt = select(DutyAssignment).join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id).where(DutySchedule.class_id == class_id)
    if duty_date:
        stmt = stmt.where(DutyAssignment.duty_date == duty_date)
    if start_date:
        stmt = stmt.where(DutyAssignment.duty_date >= start_date)
    if end_date:
        stmt = stmt.where(DutyAssignment.duty_date <= end_date)
    if student_id:
        stmt = stmt.where(DutyAssignment.student_id == student_id)
    return ok(request, list(db.scalars(stmt.join(Student, Student.id == DutyAssignment.student_id)
                                     .order_by(DutyAssignment.duty_date, DutyAssignment.item_name, *student_order_by()))))


@router.get("/duty/today")
def duty_today(request: Request, class_id: str, day: date | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    duty.complete_overdue_assignments(db, class_id)
    target = day or today()
    stmt = select(DutyAssignment).join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id).where(DutySchedule.class_id == class_id, DutyAssignment.duty_date == target, DutyAssignment.status != "replaced")
    return ok(request, list(db.scalars(stmt.join(Student, Student.id == DutyAssignment.student_id)
                                     .order_by(DutyAssignment.item_name, *student_order_by()))))


@router.get("/duty/weekly")
def duty_weekly(request: Request, class_id: str, week_start: date, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    duty.complete_overdue_assignments(db, class_id)
    stmt = select(DutyAssignment).join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id).where(DutySchedule.class_id == class_id, DutyAssignment.duty_date.between(week_start, week_start + timedelta(days=6)))
    return ok(request, list(db.scalars(stmt.join(Student, Student.id == DutyAssignment.student_id)
                                     .order_by(DutyAssignment.duty_date, DutyAssignment.item_name, *student_order_by()))))


@router.post("/duty/assignments/{assignment_id}/replace", status_code=201)
def duty_replace_student(request: Request, assignment_id: str, body: DutyReplaceRequest, db: Session = Depends(get_db)):
    assignment = db.get(DutyAssignment, assignment_id)
    schedule = db.get(DutySchedule, assignment.duty_schedule_id) if assignment else None
    require_owned_class(request, schedule.class_id if schedule else "")
    require_owned_student(request, db, body.replacement_student_id)
    return ok(request, duty.replace_assignment(db, assignment_id, body.replacement_student_id, body.note), "值日学生已临时替换", 201)


@router.post("/duty/assignments/{assignment_id}/complete")
def duty_complete(request: Request, assignment_id: str, db: Session = Depends(get_db)):
    assignment = db.get(DutyAssignment, assignment_id)
    schedule = db.get(DutySchedule, assignment.duty_schedule_id) if assignment else None
    require_owned_class(request, schedule.class_id if schedule else "")
    return ok(request, duty.complete_assignment(db, assignment_id), "值日已完成")


@router.put("/duty/assignments/{assignment_id}/score")
def duty_assignment_score(request: Request, assignment_id: str, body: DutyAssignmentScore, db: Session = Depends(get_db)):
    assignment = db.get(DutyAssignment, assignment_id)
    schedule = db.get(DutySchedule, assignment.duty_schedule_id) if assignment else None
    require_owned_class(request, schedule.class_id if schedule else "")
    return ok(request, duty.score_assignment(db, assignment_id, body.score, body.note), "值日评分已保存并标记完成")


@router.post("/duty/score-items", status_code=201)
def duty_score_item_create(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    required = {"class_id", "name", "max_score"}
    if not required <= body.keys() or float(body["max_score"]) <= 0 or float(body.get("weight", 1)) < 0:
        raise AppError("DUTY_SCORE_INVALID", "评分项目字段无效")
    require_owned_class(request, str(body["class_id"]))
    obj = DutyScoreItem(class_id=body["class_id"], name=body["name"], max_score=float(body["max_score"]), weight=float(body.get("weight", 1)), enabled=bool(body.get("enabled", True)), sort_order=int(body.get("sort_order", 0)))
    db.add(obj)
    db.flush()
    audit(db, "create", "duty_score_item", obj.id, after=entity_dict(obj))
    db.commit()
    return ok(request, obj, "评分项目已创建", 201)


@router.post("/duty/evaluations", status_code=201)
def duty_score_create(request: Request, body: DutyEvaluationCreate, db: Session = Depends(get_db)):
    schedule = db.get(DutySchedule, body.duty_schedule_id)
    require_owned_class(request, schedule.class_id if schedule else "")
    return ok(request, duty.create_evaluation(db, body), "值日评分已保存", 201)


@router.get("/duty/statistics")
def duty_statistics(request: Request, class_id: str, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    duty.complete_overdue_assignments(db, class_id)
    return ok(request, duty.duty_statistics(db, class_id, start_date, end_date))
