from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import Exam, Homework, Student
from app.services.student_ordering import student_order_by

# Operations whose payload carries a class_id field directly.
_CLASS_PAYLOAD_OPS = {
    "memory.upsert",
    "memory.forget",
    "student.create",
    "student.update.batch",
    "seating.update",
    "attendance.set",
    "homework.create",
    "student_event.create",
    "exam.create",
    "lesson_override.create",
    "duty.schedule.confirm",
    "classroom.broadcast.send",
    "classroom.volume.set",
}


def normalize_student_ref(value: Any) -> str:
    """Canonicalize a student reference: NFKC (full-width digits), trim, drop 第/号 decorations."""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if text.startswith("第"):
        text = text[1:].strip()
    while text.endswith(("号", "號")):
        text = text[:-1].strip()
    return text


def _numeric_key(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def _candidates(rows: list[Student]) -> list[dict[str, str]]:
    return [{"student_no": row.student_no, "name": row.name} for row in rows]


def _class_students(db: Session, class_id: str) -> list[Student]:
    return list(db.scalars(select(Student).where(Student.class_id == class_id, Student.deleted_at.is_(None)).order_by(*student_order_by())))


def _resolve_in(db: Session, rows: list[Student], class_id: str, ref: Any, *, allow_name: bool) -> Student:
    raw = str(ref).strip()
    if not raw:
        raise AppError("VALIDATION_ERROR", "缺少学生标识（请提供班内学号）", 422)
    for row in rows:
        if row.id == raw:  # UUID pass-through keeps web/legacy callers working.
            return row
    value = normalize_student_ref(raw)
    exact = [row for row in rows if normalize_student_ref(row.student_no) == value]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AppError("STUDENT_AMBIGUOUS", f"学号“{raw}”匹配到多名学生，请核对名单", 409, {"candidates": _candidates(exact)})
    number = _numeric_key(value)
    if number is not None:
        numeric = [row for row in rows if _numeric_key(normalize_student_ref(row.student_no)) == number]
        if len(numeric) == 1:
            return numeric[0]
        if len(numeric) > 1:
            raise AppError("STUDENT_AMBIGUOUS", f"学号“{raw}”对应多个写法，请使用名单中的准确学号", 409, {"candidates": _candidates(numeric)})
    if allow_name:
        named = [row for row in rows if row.name.strip() == value]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise AppError("STUDENT_AMBIGUOUS", f"班里有 {len(named)} 位“{value}”，请改用学号", 409, {"candidates": _candidates(named)})
    known = db.get(Student, raw)
    if known is not None:
        # UUID exists but is deleted or belongs to another class: keep the legacy 403 semantics.
        raise AppError("CLASS_SCOPE_VIOLATION", "学生不存在、已删除或不属于当前班级", 403, {"student_ref": raw, "class_id": class_id})
    raise AppError("STUDENT_NOT_FOUND", f"班级内找不到与“{raw}”匹配的学生", 404, {"student_ref": raw, "class_id": class_id})


def resolve_student(db: Session, class_id: str, ref: Any, *, allow_name: bool = True) -> Student:
    """Resolve a class-scoped reference (班内学号 / 唯一姓名 / UUID) to a live Student."""
    return _resolve_in(db, _class_students(db, class_id), class_id, ref, allow_name=allow_name)


def resolve_students(db: Session, class_id: str, refs: list[Any], *, allow_name: bool = True) -> list[Student]:
    rows = _class_students(db, class_id)
    return [_resolve_in(db, rows, class_id, ref, allow_name=allow_name) for ref in refs]


def _require_bound_scope(class_id: str | None, bound_class_id: str | None) -> None:
    if bound_class_id and class_id and class_id != bound_class_id:
        raise AppError(
            "CLASS_SCOPE_VIOLATION",
            "写入预览不属于本次分析的班级",
            403,
            {"class_id": class_id, "bound_class_id": bound_class_id},
        )


def _payload_class_id(p: dict[str, Any], bound_class_id: str | None) -> str:
    class_id = str(p["class_id"]) if p.get("class_id") else None
    _require_bound_scope(class_id, bound_class_id)
    if not class_id:
        if bound_class_id:
            class_id = bound_class_id
            p["class_id"] = bound_class_id
        else:
            raise AppError("VALIDATION_ERROR", "缺少班级上下文（class_id）", 422)
    return class_id


def _resolve_single(db: Session, class_id: str, row: dict[str, Any], field: str, rows: list[Student] | None = None) -> None:
    ref = row.pop("student_no", None) or row.get("student_id")
    if ref is None:
        raise AppError("VALIDATION_ERROR", f"缺少学生标识（{field}.student_no）", 422)
    roster = rows if rows is not None else _class_students(db, class_id)
    row["student_id"] = _resolve_in(db, roster, class_id, ref, allow_name=True).id


def _resolve_item(db: Session, rows: list[Student], class_id: str, item: Any) -> Any:
    """Rewrite one nested batch item; leave ref-less items for Pydantic to flag."""
    if not isinstance(item, dict):
        return item
    ref = item.get("student_no") or item.get("student_id")
    if ref is None:
        return item
    resolved = {**item, "student_id": _resolve_in(db, rows, class_id, ref, allow_name=True).id}
    resolved.pop("student_no", None)
    return resolved


def apply_student_refs(db: Session, operation_type: str, payload: dict[str, Any], *, bound_class_id: str | None = None) -> dict[str, Any]:
    """Deterministically rewrite 班内学号 references in an agent payload to student UUIDs.

    Runs before normalize_and_preview so the normalized payload (and every executor)
    keeps working with UUIDs. Resolution freezes the mapping at preview time, so a
    later student_no change cannot redirect an already reviewed write.
    """
    p = dict(payload)
    if operation_type == "class.onboarding.commit":
        return p
    if operation_type in _CLASS_PAYLOAD_OPS:
        _payload_class_id(p, bound_class_id)
    if operation_type == "arrangement.create" and (p.get("class_id") or bound_class_id):
        # 全局安排允许 class_id 为空；班级上下文存在时才校验/注入。
        _payload_class_id(p, bound_class_id)
    if operation_type == "duty.assignment.score":
        return p

    if operation_type == "student.update":
        if not p.get("class_id") and not bound_class_id and p.get("student_id"):
            # 兼容仅带 UUID 的旧调用：从学生档案推导班级上下文。
            existing = db.get(Student, str(p["student_id"]))
            if existing:
                p["class_id"] = existing.class_id
        class_id = _payload_class_id(p, bound_class_id)
        _resolve_single(db, class_id, p, "payload")
    elif operation_type == "student.update.batch":
        class_id = _payload_class_id(p, bound_class_id)
        refs = p.pop("student_nos", None) or p.get("student_ids")
        if isinstance(refs, str):
            refs = [refs]
        if not refs:
            raise AppError("VALIDATION_ERROR", "student.update.batch缺少student_nos", 422)
        p["student_ids"] = [row.id for row in resolve_students(db, class_id, list(refs))]
    elif operation_type == "classroom.broadcast.send":
        class_id = _payload_class_id(p, bound_class_id)
        refs = p.pop("student_nos", None) or p.get("student_ids")
        if isinstance(refs, str):
            refs = [refs]
        # 自定义句子按原文播报，不选学生；三段式必须解析出本班学生。
        p["student_ids"] = [row.id for row in resolve_students(db, class_id, list(refs))] if refs else []
    elif operation_type == "seating.update":
        class_id = _payload_class_id(p, bound_class_id)
        rows = _class_students(db, class_id)
        layout = p.get("layout")
        if isinstance(layout, list):
            p["layout"] = [
                line if not isinstance(line, list)
                else [None if cell is None or not str(cell).strip() else _resolve_in(db, rows, class_id, cell, allow_name=True).id for cell in line]
                for line in layout
            ]
    elif operation_type in {"attendance.set", "student_event.create"}:
        class_id = _payload_class_id(p, bound_class_id)
        _resolve_single(db, class_id, p, "payload")
    elif operation_type == "student_event.batch":
        items = p.get("items")
        if isinstance(items, list):
            resolved_items = []
            rosters: dict[str, list[Student]] = {}
            for item in items:
                if not isinstance(item, dict):
                    resolved_items.append(item)
                    continue
                row = dict(item)
                class_id = _payload_class_id(row, bound_class_id)
                if class_id not in rosters:
                    rosters[class_id] = _class_students(db, class_id)
                _resolve_single(db, class_id, row, "items", rosters[class_id])
                resolved_items.append(row)
            p["items"] = resolved_items
    elif operation_type == "homework.status.batch":
        homework = db.get(Homework, str(p.get("homework_id") or ""))
        if not homework:
            raise not_found("作业", str(p.get("homework_id") or ""))
        _require_bound_scope(homework.class_id, bound_class_id)
        rows = _class_students(db, homework.class_id)
        items = p.get("items")
        if isinstance(items, list):
            p["items"] = [_resolve_item(db, rows, homework.class_id, item) for item in items]
    elif operation_type == "score.batch":
        exam = db.get(Exam, str(p.get("exam_id") or ""))
        if not exam:
            raise not_found("考试", str(p.get("exam_id") or ""))
        _require_bound_scope(exam.class_id, bound_class_id)
        rows = _class_students(db, exam.class_id)
        scores = p.get("scores")
        if isinstance(scores, list):
            p["scores"] = [_resolve_item(db, rows, exam.class_id, row) for row in scores]
    return p
