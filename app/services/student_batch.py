from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.entities import Student
from app.schemas.student_batch import StudentBatchUpdate
from app.services.class_student import _student_integrity_error, get_class
from app.services.common import audit


def _students(db: Session, data: StudentBatchUpdate) -> list[Student]:
    get_class(db, data.class_id)
    rows = list(db.scalars(select(Student).where(
        Student.id.in_(data.student_ids), Student.class_id == data.class_id, Student.deleted_at.is_(None),
    )))
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(data.student_ids):
        raise AppError("CLASS_SCOPE_VIOLATION", "部分学生不存在、已删除或不属于当前班级", 403)
    for row in rows:
        if any(getattr(row, key) is not None and str(getattr(row, key)).strip() for key in data.only_if_empty):
            raise AppError("STUDENT_BATCH_CONFLICT", "部分学生的待补字段已有值，请重新核对范围", 409)
    return [by_id[student_id] for student_id in data.student_ids]


def prepare(db: Session, payload: dict[str, Any], preview: dict[str, Any]) -> tuple[dict, dict]:
    data = StudentBatchUpdate.model_validate(payload)
    rows = _students(db, data)
    before = {row.id: jsonable_encoder({key: getattr(row, key) for key in data.changes.model_fields_set}) for row in rows}
    # Snapshot only the changed fields; do not persist full student profiles.
    normalized = {**payload, "expected_values": before}
    preview = {**preview, "students": [
        {"student_id": row.id, "student_no": row.student_no, "name": row.name, "before": before[row.id]} for row in rows
    ]}
    return normalized, preview


def execute(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    expected = payload["expected_values"]
    data = StudentBatchUpdate.model_validate({key: value for key, value in payload.items() if key != "expected_values"})
    rows = _students(db, data)
    changes = data.changes.model_dump(exclude_unset=True)
    for row in rows:
        current = jsonable_encoder({key: getattr(row, key) for key in changes})
        if expected.get(row.id) != current:
            raise AppError("STUDENT_BATCH_CONFLICT", "学生档案在预览后已变化，请重新生成并核对预览", 409)
    for row in rows:
        for key, value in changes.items():
            setattr(row, key, value)
        audit(db, "update", "student", row.id, before=expected[row.id], after=jsonable_encoder(changes))
    try:
        db.flush()
    except IntegrityError as exc:
        # The proposal executor owns the transaction and rolls back the whole group.
        raise _student_integrity_error(exc) from exc
    return {"class_id": data.class_id, "student_ids": data.student_ids, "record_count": len(rows), "changes": jsonable_encoder(changes)}
