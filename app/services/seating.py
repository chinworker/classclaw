from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models.entities import SeatingSnapshot, Student
from app.schemas.domain import SeatingCreate, SeatingSwap
from app.services.class_student import get_class
from app.services.common import audit, entity_dict


def current_snapshot(db: Session, class_id: str, required: bool = True) -> SeatingSnapshot | None:
    get_class(db, class_id, include_inactive=True)
    snapshot = db.scalar(
        select(SeatingSnapshot)
        .where(SeatingSnapshot.class_id == class_id)
        .order_by(SeatingSnapshot.snapshot_at.desc(), SeatingSnapshot.created_at.desc(), SeatingSnapshot.id.desc())
        .limit(1)
    )
    if required and not snapshot:
        raise not_found("当前座位表", class_id)
    return snapshot


def validate_layout(db: Session, class_id: str, rows: int, cols: int, layout: list[list[str | None]]) -> None:
    if len(layout) != rows or any(len(row) != cols for row in layout):
        raise AppError(
            "SEAT_LAYOUT_INVALID",
            "座位表行列与rows/cols不一致",
            details={"rows": rows, "cols": cols, "actual_rows": len(layout), "actual_cols": [len(r) for r in layout]},
        )
    ids = [sid for row in layout for sid in row if sid]
    if len(ids) != len(set(ids)):
        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        raise AppError("SEAT_STUDENT_DUPLICATE", "同一学生在座位表中出现多次", details={"student_ids": duplicates})
    if not ids:
        return
    students = list(db.scalars(select(Student).where(Student.id.in_(ids))))
    found = {s.id for s in students}
    if found != set(ids):
        raise AppError("STUDENT_NOT_FOUND", "座位表包含不存在的学生", details={"student_ids": sorted(set(ids) - found)})
    invalid_class = [s.id for s in students if s.class_id != class_id]
    if invalid_class:
        raise AppError("CLASS_MISMATCH", "座位表包含其他班级学生", details={"student_ids": invalid_class})
    inactive = [s.id for s in students if s.deleted_at or s.status != "active"]
    if inactive:
        raise AppError("SEAT_LAYOUT_INVALID", "已删除或已转出的学生不能加入新座位表", details={"student_ids": inactive})


def _positions(layout: list[list[str | None]]) -> dict[str, tuple[int, int]]:
    return {sid: (r + 1, c + 1) for r, row in enumerate(layout) for c, sid in enumerate(row) if sid}


def layout_changes(previous: list[list[str | None]] | None, current: list[list[str | None]]) -> list[dict]:
    old = _positions(previous or [])
    new = _positions(current)
    changes = []
    for sid in sorted(set(old) | set(new)):
        if old.get(sid) != new.get(sid):
            changes.append({"student_id": sid, "from": old.get(sid), "to": new.get(sid)})
    return changes


def create_snapshot(db: Session, class_id: str, data: SeatingCreate, action: str = "create", *, commit: bool = True) -> dict:
    get_class(db, class_id)
    validate_layout(db, class_id, data.rows, data.cols, data.layout)
    previous = current_snapshot(db, class_id, required=False)
    snapshot = SeatingSnapshot(
        class_id=class_id,
        rows=data.rows,
        cols=data.cols,
        layout_json=data.layout,
        change_note=data.change_note,
    )
    db.add(snapshot)
    db.flush()
    audit(db, action, "seating_snapshot", snapshot.id, before=entity_dict(previous) if previous else None, after=entity_dict(snapshot))
    changes = layout_changes(previous.layout_json if previous else None, data.layout)
    if commit:
        db.commit()
    return {"snapshot": snapshot, "changes": changes}


def swap_students(db: Session, class_id: str, data: SeatingSwap) -> dict:
    current = current_snapshot(db, class_id)
    assert current is not None
    layout = deepcopy(current.layout_json)
    pos: dict[str, tuple[int, int]] = {}
    for r, row in enumerate(layout):
        for c, sid in enumerate(row):
            if sid in {data.student_a_id, data.student_b_id}:
                pos[sid] = (r, c)
    missing = {data.student_a_id, data.student_b_id} - set(pos)
    if missing:
        raise AppError("SEAT_LAYOUT_INVALID", "要交换的学生不在当前座位表", details={"student_ids": sorted(missing)})
    a, b = pos[data.student_a_id], pos[data.student_b_id]
    layout[a[0]][a[1]], layout[b[0]][b[1]] = layout[b[0]][b[1]], layout[a[0]][a[1]]
    return create_snapshot(
        db,
        class_id,
        SeatingCreate(rows=current.rows, cols=current.cols, layout=layout, change_note=data.change_note or "交换座位"),
        action="swap",
    )


def restore_snapshot(db: Session, class_id: str, snapshot_id: str) -> dict:
    source = db.get(SeatingSnapshot, snapshot_id)
    if not source:
        raise not_found("座位快照", snapshot_id)
    if source.class_id != class_id:
        raise AppError("CLASS_MISMATCH", "快照不属于该班级")
    return create_snapshot(
        db,
        class_id,
        SeatingCreate(rows=source.rows, cols=source.cols, layout=deepcopy(source.layout_json), change_note=f"恢复快照 {snapshot_id}"),
        action="restore",
    )
