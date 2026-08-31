from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import (
    DutyAssignment,
    DutyEvaluation,
    DutyEvaluationDetail,
    DutyRule,
    DutySchedule,
    DutyScoreItem,
    Student,
)
from app.schemas.domain import DutyConfirmRequest, DutyEvaluationCreate, DutyPreviewRequest, DutyRuleCreate, DutyRuleUpdate
from app.services.class_student import get_class, get_student
from app.services.common import audit, entity_dict
from app.utils.time import now


def validate_rule_json(rule: dict) -> dict:
    errors: list[dict] = []
    items = rule.get("items")
    if not isinstance(items, list) or not items:
        errors.append({"field": "items", "message": "至少需要一个值日项目"})
    else:
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("name") or not isinstance(item.get("count"), int) or item["count"] < 1:
                errors.append({"field": f"items.{index}", "message": "项目需要name及正整数count"})
    workdays = rule.get("workdays", [1, 2, 3, 4, 5])
    if not isinstance(workdays, list) or any(d not in range(1, 8) for d in workdays):
        errors.append({"field": "workdays", "message": "workdays只能为1到7"})
    for pair in rule.get("incompatible_pairs", []):
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            errors.append({"field": "incompatible_pairs", "message": "互斥约束必须为两个不同学生ID"})
    if errors:
        raise AppError("DUTY_RULE_INVALID", "值日规则无效", details={"errors": errors})
    return {
        "valid": True,
        "normalized": {
            **rule,
            "workdays": workdays,
            "exclude_students": list(dict.fromkeys(rule.get("exclude_students", []))),
            "skip_dates": list(dict.fromkeys(rule.get("skip_dates", []))),
        },
    }


def save_rule(db: Session, data: DutyRuleCreate) -> DutyRule:
    get_class(db, data.class_id)
    normalized = validate_rule_json(data.rule_json)["normalized"]
    if data.effective_from > data.effective_to:
        raise AppError("DUTY_RULE_INVALID", "规则结束日期不能早于开始日期")
    obj = DutyRule(**data.model_dump(exclude={"rule_json"}), rule_json=normalized)
    db.add(obj)
    db.flush()
    audit(db, "create", "duty_rule", obj.id, after=entity_dict(obj))
    db.commit()
    return obj


def update_rule(db: Session, rule_id: str, data: DutyRuleUpdate) -> DutyRule:
    obj = db.get(DutyRule, rule_id)
    if not obj:
        raise not_found("值日规则", rule_id)
    before = entity_dict(obj)
    values = data.model_dump(exclude_unset=True)
    if values.get("rule_json") is not None:
        values["rule_json"] = validate_rule_json(values["rule_json"])["normalized"]
    effective_from = values.get("effective_from", obj.effective_from)
    effective_to = values.get("effective_to", obj.effective_to)
    if effective_from > effective_to:
        raise AppError("DUTY_RULE_INVALID", "规则结束日期不能早于开始日期")
    for key, value in values.items():
        setattr(obj, key, value)
    audit(db, "update", "duty_rule", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def delete_rule(db: Session, rule_id: str) -> dict:
    obj = db.get(DutyRule, rule_id)
    if not obj:
        raise not_found("值日规则", rule_id)
    before = entity_dict(obj)
    db.delete(obj)
    audit(db, "delete", "duty_rule", rule_id, before=before, after=None)
    db.commit()
    return {"deleted_id": rule_id, "deleted_name": before["name"]}


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def generate_preview(db: Session, data: DutyPreviewRequest | DutyConfirmRequest) -> dict:
    get_class(db, data.class_id)
    if data.start_date > data.end_date:
        raise AppError("DUTY_RULE_INVALID", "排班结束日期不能早于开始日期")
    normalized = validate_rule_json(data.rule_json)["normalized"]
    students = list(
        db.scalars(
            select(Student).where(
                Student.class_id == data.class_id,
                Student.deleted_at.is_(None),
                Student.status == "active",
            )
        )
    )
    by_id = {s.id: s for s in students}
    excluded = set(normalized.get("exclude_students", []))
    unknown = excluded - set(by_id)
    pairs = [set(pair) for pair in normalized.get("incompatible_pairs", [])]
    available = {sid: set(days) for sid, days in normalized.get("student_weekdays", {}).items()}
    max_count = normalized.get("max_per_student")
    workdays = set(normalized["workdays"])
    skip_dates = set(normalized.get("skip_dates", []))
    counts: Counter[str] = Counter()
    assignments: list[dict] = []
    conflicts: list[dict] = []

    for duty_date in _date_range(data.start_date, data.end_date):
        if duty_date.isoweekday() not in workdays or duty_date.isoformat() in skip_dates:
            continue
        selected_today: set[str] = set()
        for item in normalized["items"]:
            needed = item["count"]
            fixed = [sid for sid in item.get("fixed_students", []) if sid in by_id]
            selected: list[str] = []
            for sid in fixed:
                if sid not in selected_today and sid not in excluded and len(selected) < needed:
                    selected.append(sid)
            eligible = []
            for student in students:
                sid = student.id
                if sid in selected or sid in selected_today or sid in excluded:
                    continue
                if sid in available and duty_date.isoweekday() not in available[sid]:
                    continue
                if max_count is not None and counts[sid] >= int(max_count):
                    continue
                if any(sid in pair and bool((pair - {sid}) & (selected_today | set(selected))) for pair in pairs):
                    continue
                preference = 0
                if item.get("boarding_preferred") and student.boarding_status in {"boarding", "住校", "住宿"}:
                    preference = -1
                eligible.append((counts[sid], preference, student.student_no, sid))
            eligible.sort()
            selected.extend(row[3] for row in eligible[: max(0, needed - len(selected))])
            if len(selected) < needed:
                conflicts.append(
                    {
                        "rule": "staffing_shortage",
                        "date": duty_date,
                        "item_name": item["name"],
                        "missing_count": needed - len(selected),
                        "involved_students": selected,
                        "suggestion": "减少每项人数、放宽互斥/可用星期限制或扩大候选学生",
                    }
                )
            for sid in selected:
                selected_today.add(sid)
                counts[sid] += 1
                assignments.append(
                    {
                        "duty_date": duty_date,
                        "item_name": item["name"],
                        "area": item.get("area"),
                        "student_id": sid,
                        "student_name": by_id[sid].name,
                        "group_name": by_id[sid].group_no,
                    }
                )
    if not students:
        conflicts.append({"rule": "no_students", "missing_count": 0, "suggestion": "先添加在读学生"})
    spread = (max(counts.values()) - min(counts.values())) if counts else 0
    token_source = json.dumps(
        {"class_id": data.class_id, "start": str(data.start_date), "end": str(data.end_date), "rule": normalized},
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "preview_token": hashlib.sha256(token_source.encode()).hexdigest(),
        "assignments": assignments,
        "conflicts": conflicts,
        "workload": [{"student_id": sid, "student_name": by_id[sid].name, "count": counts[sid]} for sid in sorted(by_id)],
        "balance": {"max_min_spread": spread, "is_balanced": spread <= 1},
        "warnings": ([{"code": "UNKNOWN_EXCLUDED_STUDENTS", "student_ids": sorted(unknown)}] if unknown else []),
        "writes_performed": 0,
    }


def confirm_schedule(db: Session, data: DutyConfirmRequest, *, commit: bool = True) -> dict:
    preview = generate_preview(db, data)
    if data.preview_token and data.preview_token != preview["preview_token"]:
        raise AppError("DUTY_SCHEDULE_CONFLICT", "规则或日期已变化，请重新预览")
    if preview["conflicts"]:
        raise AppError("DUTY_SCHEDULE_CONFLICT", "排班规则存在冲突，不能确认", details={"conflicts": preview["conflicts"]})
    schedule = DutySchedule(
        class_id=data.class_id,
        rule_id=data.rule_id,
        name=data.name,
        start_date=data.start_date,
        end_date=data.end_date,
        status="active",
        confirmed_at=now(),
    )
    db.add(schedule)
    db.flush()
    for row in preview["assignments"]:
        db.add(DutyAssignment(duty_schedule_id=schedule.id, **{k: v for k, v in row.items() if k != "student_name"}))
    audit(db, "confirm", "duty_schedule", schedule.id, after={"schedule": entity_dict(schedule), "assignment_count": len(preview["assignments"])})
    if commit:
        db.commit()
    return {"schedule": schedule, "assignment_count": len(preview["assignments"]), "workload": preview["workload"]}


def replace_assignment(db: Session, assignment_id: str, replacement_student_id: str, note: str | None) -> DutyAssignment:
    original = db.get(DutyAssignment, assignment_id)
    if not original:
        raise not_found("值日安排", assignment_id)
    student = get_student(db, replacement_student_id)
    schedule = db.get(DutySchedule, original.duty_schedule_id)
    if not schedule or student.class_id != schedule.class_id:
        raise AppError("CLASS_MISMATCH", "替换学生不属于值日安排班级")
    before = entity_dict(original)
    original.status = "replaced"
    replacement = DutyAssignment(
        duty_schedule_id=original.duty_schedule_id,
        duty_date=original.duty_date,
        item_name=original.item_name,
        area=original.area,
        student_id=replacement_student_id,
        group_name=student.group_no,
        status="pending",
        replacement_for_assignment_id=original.id,
        note=note,
    )
    db.add(replacement)
    db.flush()
    audit(db, "replace", "duty_assignment", original.id, before=before, after={"original": entity_dict(original), "replacement": entity_dict(replacement)})
    db.commit()
    return replacement


def complete_assignment(db: Session, assignment_id: str) -> DutyAssignment:
    obj = db.get(DutyAssignment, assignment_id)
    if not obj:
        raise not_found("值日安排", assignment_id)
    before = entity_dict(obj)
    obj.status = "completed"
    obj.completed_at = now()
    audit(db, "complete", "duty_assignment", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def score_assignment(db: Session, assignment_id: str, score: float, note: str | None = None, *, commit: bool = True) -> DutyAssignment:
    obj = db.get(DutyAssignment, assignment_id)
    if not obj:
        raise not_found("值日安排", assignment_id)
    if obj.status == "replaced":
        raise AppError("DUTY_SCORE_INVALID", "已替换的值日安排不能评分")
    before = entity_dict(obj)
    obj.score = float(score)
    obj.status = "completed"
    obj.completed_at = now()
    if note is not None:
        obj.note = note
    audit(db, "score", "duty_assignment", obj.id, before=before, after=entity_dict(obj))
    if commit:
        db.commit()
    return obj


def complete_overdue_assignments(db: Session, class_id: str, as_of: date | None = None) -> int:
    """Lazily reconcile past unscored duty as full-score completed work."""
    target = as_of or now().date()
    rows = list(
        db.scalars(
            select(DutyAssignment)
            .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
            .where(
                DutySchedule.class_id == class_id,
                DutyAssignment.duty_date < target,
                DutyAssignment.status == "pending",
            )
        )
    )
    if not rows:
        return 0
    for obj in rows:
        obj.score = 5.0
        obj.status = "completed"
        obj.completed_at = now()
    audit(db, "auto_score", "duty_assignment", class_id, after={"count": len(rows), "score": 5, "before_date": target.isoformat()})
    db.commit()
    return len(rows)


def create_evaluation(db: Session, data: DutyEvaluationCreate) -> DutyEvaluation:
    schedule = db.get(DutySchedule, data.duty_schedule_id)
    if not schedule:
        raise not_found("值日计划", data.duty_schedule_id)
    detail_rows = []
    weighted_score = 0.0
    weighted_max = 0.0
    for row in data.details:
        item = db.get(DutyScoreItem, row.get("score_item_id"))
        if not item or item.class_id != schedule.class_id or not item.enabled:
            raise AppError("DUTY_SCORE_INVALID", "评分项目无效", details={"score_item_id": row.get("score_item_id")})
        score = float(row.get("score", -1))
        if score < 0 or score > item.max_score:
            raise AppError("DUTY_SCORE_INVALID", "评分超出允许范围", details={"score_item_id": item.id, "max_score": item.max_score})
        detail_rows.append((item, score, row.get("comment")))
        weighted_score += score * item.weight
        weighted_max += item.max_score * item.weight
    evaluation = DutyEvaluation(
        duty_schedule_id=data.duty_schedule_id,
        duty_date=data.duty_date,
        item_name=data.item_name,
        total_score=round(weighted_score, 2),
        max_score=round(weighted_max, 2),
        evaluator=data.evaluator,
        comment=data.comment,
    )
    db.add(evaluation)
    db.flush()
    for item, score, comment in detail_rows:
        db.add(DutyEvaluationDetail(evaluation_id=evaluation.id, score_item_id=item.id, score=score, comment=comment))
    audit(db, "create", "duty_evaluation", evaluation.id, after=entity_dict(evaluation))
    db.commit()
    return evaluation


def duty_statistics(db: Session, class_id: str, start_date: date | None, end_date: date | None) -> dict:
    stmt = (
        select(DutyAssignment.student_id, DutyAssignment.status, func.count(DutyAssignment.id))
        .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
        .where(DutySchedule.class_id == class_id)
    )
    if start_date:
        stmt = stmt.where(DutyAssignment.duty_date >= start_date)
    if end_date:
        stmt = stmt.where(DutyAssignment.duty_date <= end_date)
    rows = db.execute(stmt.group_by(DutyAssignment.student_id, DutyAssignment.status)).all()
    by_student: dict[str, dict] = defaultdict(lambda: {"total": 0, "completed": 0})
    for sid, status, count in rows:
        by_student[sid]["total"] += count
        if status == "completed":
            by_student[sid]["completed"] += count
    score_stmt = (
        select(DutyAssignment.student_id, func.avg(DutyAssignment.score))
        .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
        .where(DutySchedule.class_id == class_id, DutyAssignment.score.is_not(None))
    )
    if start_date:
        score_stmt = score_stmt.where(DutyAssignment.duty_date >= start_date)
    if end_date:
        score_stmt = score_stmt.where(DutyAssignment.duty_date <= end_date)
    for sid, average in db.execute(score_stmt.group_by(DutyAssignment.student_id)).all():
        by_student[sid]["average_score"] = round(float(average), 2)
    totals = [row["total"] for row in by_student.values()]
    return {
        "students": [{"student_id": sid, **stats} for sid, stats in by_student.items()],
        "balance": {"max_min_spread": max(totals) - min(totals) if totals else 0, "is_balanced": not totals or max(totals) - min(totals) <= 1},
    }
