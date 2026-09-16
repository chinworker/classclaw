from __future__ import annotations

from app.models.entities import Student


def student_order_by() -> tuple:
    """Apply before LIMIT/OFFSET; UUID is only a final tie-breaker."""
    return (Student.student_no.collate("STUDENT_NO"), Student.class_id, Student.id)
