from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.entities import AuditLog, SeatingSnapshot, Student
from app.schemas.domain import SeatingCreate, SeatingSwap, StudentCreate
from app.services import class_student, seating


def test_student_number_unique_but_same_name_allowed(db, sample):
    cls, _, _ = sample
    created = class_student.create_student(db, StudentCreate(class_id=cls.id, student_no="004", name="张三"))
    assert created.name == "张三"
    result = class_student.search_students(db, class_id=cls.id, query="张三", tag=None, status=None, page=1, page_size=20, exact_name=True)
    assert result["ambiguous"] is True and result["total"] == 2
    try:
        class_student.create_student(db, StudentCreate(class_id=cls.id, student_no="004", name="新人"))
    except AppError as exc:
        assert exc.code == "STUDENT_NO_CONFLICT"
    else:
        raise AssertionError("重复学号应被拒绝")


def test_soft_delete_keeps_audit(db, sample):
    student = sample[2][0]
    class_student.delete_student(db, student.id)
    assert db.get(Student, student.id).deleted_at is not None
    assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == student.id)) >= 1


def test_seating_snapshot_swap_restore_and_validation(db, sample):
    cls, other, students = sample
    first = seating.create_snapshot(db, cls.id, SeatingCreate(rows=2, cols=2, layout=[[students[0].id, students[1].id], [None, students[2].id]]))
    second = seating.swap_students(db, cls.id, SeatingSwap(student_a_id=students[0].id, student_b_id=students[2].id))
    assert first["snapshot"].id != second["snapshot"].id
    assert first["snapshot"].layout_json[0][0] == students[0].id
    restored = seating.restore_snapshot(db, cls.id, first["snapshot"].id)
    assert restored["snapshot"].id not in {first["snapshot"].id, second["snapshot"].id}
    assert seating.current_snapshot(db, cls.id).layout_json == first["snapshot"].layout_json
    for bad in (
        SeatingCreate(rows=1, cols=2, layout=[[students[0].id]]),
        SeatingCreate(rows=1, cols=2, layout=[[students[0].id, students[0].id]]),
        SeatingCreate(rows=1, cols=1, layout=[[students[3].id]]),
    ):
        try:
            seating.create_snapshot(db, cls.id, bad)
        except AppError:
            pass
        else:
            raise AssertionError("无效座位表应被拒绝")

