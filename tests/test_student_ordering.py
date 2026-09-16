from datetime import date

from app.analytics import service as analytics
from app.database import build_engine
from app.models.entities import ClassRoom, DutyAssignment, DutySchedule, Student
from app.schemas.domain import DutyPreviewRequest
from app.services import duty
from app.services.class_student import search_students
from app.utils.student_sort import student_no_key

NUMBERS = ["10", "2", "1", "02", "３", "A10", "A2", "100000000000000000002", "100000000000000000001"]
EXPECTED = ["1", "2", "02", "３", "10", "100000000000000000001", "100000000000000000002", "A2", "A10"]


def roster(db):
    cls = ClassRoom(name="排序班", grade="高一")
    db.add(cls)
    db.flush()
    students = [Student(class_id=cls.id, student_no=no, name=f"学生{no}", status="active") for no in NUMBERS]
    db.add_all(students)
    db.commit()
    return cls, students


def test_student_number_sort_handles_numbers_full_width_and_long_ids():
    assert sorted(NUMBERS, key=student_no_key) == EXPECTED
    assert sorted([None, "", "10", "2"], key=student_no_key)[:2] == ["2", "10"]


def test_student_search_orders_before_pagination_and_preserves_filters(db):
    cls, _ = roster(db)
    pages = [search_students(db, class_id=cls.id, query=None, tag=None, status="active", page=n, page_size=2)
             for n in range(1, 6)]
    assert [student.student_no for page in pages for student in page["items"]] == EXPECTED
    assert all(page["total"] == len(NUMBERS) for page in pages)
    filtered = search_students(db, class_id=cls.id, query="A", tag=None, status=None, page=1, page_size=10)
    assert [s.student_no for s in filtered["items"]] == ["A2", "A10"]


def test_student_number_collation_is_registered_on_file_database_connections(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'sorting.db'}")
    try:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT '10' AS n UNION ALL SELECT '2' ORDER BY n COLLATE STUDENT_NO").scalars().all()
            assert rows == ["2", "10"]
    finally:
        engine.dispose()


def test_student_analytics_and_duty_default_to_student_number_order(db):
    cls, students = roster(db)
    day = date(2026, 9, 14)
    analyses = analytics._class_student_analyses(db, cls.id, day, day)
    assert [row["student"].student_no for row in analyses] == EXPECTED
    preview = duty.generate_preview(db, DutyPreviewRequest(
        class_id=cls.id, name="值日", start_date=day, end_date=day,
        rule_json={"items": [{"name": "扫地", "count": len(students)}]},
    ))
    by_id = {s.id: s.student_no for s in students}
    assert [by_id[row["student_id"]] for row in preview["assignments"]] == EXPECTED
    assert [by_id[row["student_id"]] for row in preview["workload"]] == EXPECTED
    schedule = DutySchedule(class_id=cls.id, name="值日", start_date=day, end_date=day)
    db.add(schedule)
    db.flush()
    for student in students:
        db.add(DutyAssignment(duty_schedule_id=schedule.id, student_id=student.id, duty_date=day, item_name="扫地"))
    db.commit()
    stats = duty.duty_statistics(db, cls.id, day, day)
    assert [by_id[row["student_id"]] for row in stats["students"]] == EXPECTED
