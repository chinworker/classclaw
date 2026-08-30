from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import require_owned_class, require_owned_record, require_owned_student, scoped_class_id
from app.database import get_db
from app.models.entities import AttendanceRecord, Exam, Homework, HomeworkStudentStatus, Score, StudentEvent
from app.schemas.domain import AttendanceSet, ExamCreate, HomeworkBatchStatus, HomeworkCreate, ScoreBatch, StudentEventCreate
from app.services import academic as service


router = APIRouter(tags=["作业、表现、考勤与成绩"])


@router.post("/homework", status_code=201)
def homework_create(request: Request, body: HomeworkCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.create_homework(db, body), "作业已创建", 201)


@router.get("/homework")
def homework_list(request: Request, class_id: str | None = None, subject: str | None = None, status: str | None = None, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    class_id = scoped_class_id(request, class_id)
    stmt = select(Homework)
    if class_id:
        stmt = stmt.where(Homework.class_id == class_id)
    if subject:
        stmt = stmt.where(Homework.subject == subject)
    if status:
        stmt = stmt.where(Homework.status == status)
    if start_date:
        stmt = stmt.where(Homework.assigned_date >= start_date)
    if end_date:
        stmt = stmt.where(Homework.assigned_date <= end_date)
    return ok(request, list(db.scalars(stmt.order_by(Homework.assigned_date.desc()))))


@router.put("/homework/{homework_id}/students")
def homework_update_status(request: Request, homework_id: str, body: HomeworkBatchStatus, db: Session = Depends(get_db)):
    require_owned_record(request, db, Homework, homework_id)
    return ok(request, service.batch_homework_status(db, homework_id, body), "作业状态已批量更新")


@router.get("/homework/{homework_id}/summary")
def homework_summary(request: Request, homework_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, Homework, homework_id)
    return ok(request, service.homework_summary(db, homework_id))


@router.get("/homework/{homework_id}/missing")
def homework_missing_list(request: Request, homework_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, Homework, homework_id)
    rows = list(db.scalars(select(HomeworkStudentStatus).where(HomeworkStudentStatus.homework_id == homework_id, HomeworkStudentStatus.status == "missing")))
    return ok(request, rows)


@router.post("/homework/{homework_id}/sync-missing-events")
def homework_sync_events(request: Request, homework_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, Homework, homework_id)
    return ok(request, service.sync_missing_homework_events(db, homework_id))


@router.post("/student-events", status_code=201)
def student_event_create(request: Request, body: StudentEventCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    require_owned_student(request, db, body.student_id)
    return ok(request, service.create_student_event(db, body), "学生事件已登记", 201)


@router.post("/student-events/batch", status_code=201)
def student_event_batch_create(request: Request, body: list[StudentEventCreate], db: Session = Depends(get_db)):
    for item in body:
        require_owned_class(request, item.class_id)
        require_owned_student(request, db, item.student_id)
    return ok(request, service.batch_student_events(db, body), "学生事件已批量登记", 201)


@router.get("/student-events")
def student_event_query(request: Request, student_id: str | None = None, class_id: str | None = None, event_type: str | None = None, subject: str | None = None, start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db)):
    class_id = scoped_class_id(request, class_id)
    if student_id:
        require_owned_student(request, db, student_id)
    stmt = select(StudentEvent).where(StudentEvent.deleted_at.is_(None))
    if student_id:
        stmt = stmt.where(StudentEvent.student_id == student_id)
    if class_id:
        stmt = stmt.where(StudentEvent.class_id == class_id)
    if event_type:
        stmt = stmt.where(StudentEvent.event_type == event_type)
    if subject:
        stmt = stmt.where(StudentEvent.subject == subject)
    if start_date:
        stmt = stmt.where(StudentEvent.event_date >= start_date)
    if end_date:
        stmt = stmt.where(StudentEvent.event_date <= end_date)
    return ok(request, list(db.scalars(stmt.order_by(StudentEvent.event_date.desc()))))


@router.delete("/student-events/{event_id}")
def student_event_revoke(request: Request, event_id: str, db: Session = Depends(get_db)):
    require_owned_record(request, db, StudentEvent, event_id)
    return ok(request, service.revoke_student_event(db, event_id), "学生事件已撤销")


@router.put("/attendance")
def attendance_set(request: Request, body: AttendanceSet, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    require_owned_student(request, db, body.student_id)
    return ok(request, service.set_attendance(db, body), "考勤已更新")


@router.get("/attendance")
def attendance_query(request: Request, class_id: str | None = None, student_id: str | None = None, start_date: date | None = None, end_date: date | None = None, period: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    class_id = scoped_class_id(request, class_id)
    if student_id:
        require_owned_student(request, db, student_id)
    stmt = select(AttendanceRecord)
    if class_id:
        stmt = stmt.where(AttendanceRecord.class_id == class_id)
    if student_id:
        stmt = stmt.where(AttendanceRecord.student_id == student_id)
    if start_date:
        stmt = stmt.where(AttendanceRecord.attendance_date >= start_date)
    if end_date:
        stmt = stmt.where(AttendanceRecord.attendance_date <= end_date)
    if period:
        stmt = stmt.where(AttendanceRecord.period == period)
    if status:
        stmt = stmt.where(AttendanceRecord.status == status)
    return ok(request, list(db.scalars(stmt.order_by(AttendanceRecord.attendance_date.desc()))))


@router.get("/attendance/summary")
def attendance_summary(request: Request, class_id: str, start_date: date, end_date: date, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.attendance_summary(db, class_id, start_date, end_date))


@router.post("/exams", status_code=201)
def exam_create(request: Request, body: ExamCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.create_exam(db, body), "考试已创建", 201)


@router.post("/exams/{exam_id}/scores", status_code=201)
def score_batch_save(request: Request, exam_id: str, body: ScoreBatch, db: Session = Depends(get_db)):
    require_owned_record(request, db, Exam, exam_id)
    return ok(request, service.save_scores(db, exam_id, body), "成绩已批量保存", 201)


@router.get("/exams/{exam_id}/scores")
def score_query(request: Request, exam_id: str, student_id: str | None = None, subject: str | None = None, db: Session = Depends(get_db)):
    require_owned_record(request, db, Exam, exam_id)
    if student_id:
        require_owned_student(request, db, student_id)
    stmt = select(Score).where(Score.exam_id == exam_id)
    if student_id:
        stmt = stmt.where(Score.student_id == student_id)
    if subject:
        stmt = stmt.where(Score.subject == subject)
    return ok(request, list(db.scalars(stmt.order_by(Score.subject, Score.class_rank))))


@router.get("/exams/{exam_id}/statistics")
def exam_statistics(request: Request, exam_id: str, subject: str | None = None, db: Session = Depends(get_db)):
    require_owned_record(request, db, Exam, exam_id)
    return ok(request, service.score_statistics(db, exam_id, subject))
