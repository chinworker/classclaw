from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import (
    AnalysisCache,
    AttendanceRecord,
    Exam,
    ExamSubject,
    Homework,
    HomeworkStudentStatus,
    Score,
    Student,
    StudentEvent,
)
from app.schemas.domain import (
    AttendanceSet,
    ExamCreate,
    HomeworkBatchStatus,
    HomeworkCreate,
    ScoreBatch,
    StudentEventCreate,
)
from app.services.class_student import get_class, get_student
from app.services.common import audit, entity_dict
from app.utils.time import now


def create_homework(db: Session, data: HomeworkCreate, *, commit: bool = True) -> Homework:
    get_class(db, data.class_id)
    obj = Homework(**data.model_dump())
    db.add(obj)
    db.flush()
    audit(db, "create", "homework", obj.id, after=entity_dict(obj), source_message_id=data.source_message_id)
    if commit:
        db.commit()
    return obj


def batch_homework_status(db: Session, homework_id: str, data: HomeworkBatchStatus, *, commit: bool = True) -> list[HomeworkStudentStatus]:
    homework = db.get(Homework, homework_id)
    if not homework:
        raise not_found("作业", homework_id)
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_([x.student_id for x in data.items])))}
    invalid = [item.student_id for item in data.items if item.student_id not in students or students[item.student_id].class_id != homework.class_id or students[item.student_id].deleted_at]
    if invalid:
        raise AppError("CLASS_MISMATCH", "学生不属于作业对应班级", details={"student_ids": invalid})
    result = []
    for item in data.items:
        obj = db.scalar(
            select(HomeworkStudentStatus).where(
                HomeworkStudentStatus.homework_id == homework_id,
                HomeworkStudentStatus.student_id == item.student_id,
            )
        )
        before = entity_dict(obj) if obj else None
        values = item.model_dump()
        submitted_at = values.get("submitted_at")
        if values["status"] == "submitted" and submitted_at and homework.due_at:
            deadline = homework.due_at
            if deadline.tzinfo is None and submitted_at.tzinfo is not None:
                submitted_at = submitted_at.replace(tzinfo=None)
            if submitted_at > deadline:
                values["status"] = "late"
        if obj:
            for key, value in values.items():
                setattr(obj, key, value)
        else:
            obj = HomeworkStudentStatus(homework_id=homework_id, **values)
            db.add(obj)
        db.flush()
        audit(db, "update_status", "homework_student_status", obj.id, before=before, after=entity_dict(obj))
        result.append(obj)
    if commit:
        db.commit()
    return result


def homework_summary(db: Session, homework_id: str) -> dict:
    homework = db.get(Homework, homework_id)
    if not homework:
        raise not_found("作业", homework_id)
    total_students = db.scalar(select(func.count(Student.id)).where(Student.class_id == homework.class_id, Student.deleted_at.is_(None), Student.status == "active")) or 0
    rows = db.execute(
        select(HomeworkStudentStatus.status, func.count(HomeworkStudentStatus.id))
        .where(HomeworkStudentStatus.homework_id == homework_id)
        .group_by(HomeworkStudentStatus.status)
    ).all()
    counts = {status: count for status, count in rows}
    complete = sum(counts.get(s, 0) for s in ("submitted", "late", "revised", "exempt"))
    explicit = sum(counts.values())
    counts["pending"] = counts.get("pending", 0) + max(0, total_students - explicit)
    return {"homework": homework, "student_count": total_students, "status_counts": counts, "completion_rate": complete / total_students if total_students else None}


def sync_missing_homework_events(db: Session, homework_id: str) -> dict:
    homework = db.get(Homework, homework_id)
    if not homework:
        raise not_found("作业", homework_id)
    missing_ids = list(
        db.scalars(
            select(HomeworkStudentStatus.student_id).where(
                HomeworkStudentStatus.homework_id == homework_id,
                HomeworkStudentStatus.status == "missing",
            )
        )
    )
    created = 0
    for sid in missing_ids:
        existing = db.scalar(
            select(StudentEvent).where(
                StudentEvent.student_id == sid,
                StudentEvent.source_type == "system",
                StudentEvent.source_message_id == homework_id,
                StudentEvent.subtype == "homework_missing",
                StudentEvent.deleted_at.is_(None),
            )
        )
        if existing:
            continue
        db.add(
            StudentEvent(
                class_id=homework.class_id,
                student_id=sid,
                event_type="homework",
                subtype="homework_missing",
                event_date=homework.assigned_date,
                subject=homework.subject,
                content=f"未交作业：{homework.title}",
                sentiment="negative",
                severity="attention",
                source_type="system",
                source_message_id=homework_id,
            )
        )
        created += 1
    audit(db, "sync_missing_events", "homework", homework_id, after={"created": created, "student_ids": missing_ids})
    db.commit()
    return {"created": created, "already_existing": len(missing_ids) - created}


def create_student_event(db: Session, data: StudentEventCreate, *, commit: bool = True) -> StudentEvent:
    student = get_student(db, data.student_id)
    if student.class_id != data.class_id:
        raise AppError("CLASS_MISMATCH", "学生不属于指定班级")
    if data.source_message_id:
        existing = db.scalar(
            select(StudentEvent).where(
                StudentEvent.student_id == data.student_id,
                StudentEvent.source_type == data.source_type,
                StudentEvent.source_message_id == data.source_message_id,
                StudentEvent.subtype == data.subtype,
                StudentEvent.deleted_at.is_(None),
            )
        )
        if existing:
            return existing
    obj = StudentEvent(**data.model_dump())
    db.add(obj)
    db.flush()
    audit(db, "create", "student_event", obj.id, after=entity_dict(obj), source_message_id=data.source_message_id)
    if commit:
        db.commit()
    return obj


def batch_student_events(db: Session, items: list[StudentEventCreate], *, commit: bool = True) -> list[StudentEvent]:
    if not items:
        raise AppError("VALIDATION_ERROR", "事件列表不能为空")
    student_ids = {item.student_id for item in items}
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_(student_ids)))}
    invalid = [item.student_id for item in items if item.student_id not in students or students[item.student_id].deleted_at or students[item.student_id].class_id != item.class_id]
    if invalid:
        raise AppError("CLASS_MISMATCH", "批量事件包含不存在或班级不匹配的学生", details={"student_ids": sorted(set(invalid))})
    result = []
    for data in items:
        if data.source_message_id:
            existing = db.scalar(select(StudentEvent).where(StudentEvent.student_id == data.student_id, StudentEvent.source_type == data.source_type, StudentEvent.source_message_id == data.source_message_id, StudentEvent.subtype == data.subtype, StudentEvent.deleted_at.is_(None)))
            if existing:
                result.append(existing)
                continue
        obj = StudentEvent(**data.model_dump())
        db.add(obj)
        db.flush()
        audit(db, "create", "student_event", obj.id, after=entity_dict(obj), source_message_id=data.source_message_id)
        result.append(obj)
    if commit:
        db.commit()
    return result


def revoke_student_event(db: Session, event_id: str) -> StudentEvent:
    obj = db.get(StudentEvent, event_id)
    if not obj or obj.deleted_at:
        raise not_found("学生事件", event_id)
    before = entity_dict(obj)
    obj.deleted_at = now()
    audit(db, "revoke", "student_event", obj.id, before=before, after=entity_dict(obj))
    db.commit()
    return obj


def set_attendance(db: Session, data: AttendanceSet, *, commit: bool = True) -> AttendanceRecord:
    student = get_student(db, data.student_id)
    if student.class_id != data.class_id:
        raise AppError("CLASS_MISMATCH", "学生不属于指定班级")
    obj = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.student_id == data.student_id,
            AttendanceRecord.attendance_date == data.attendance_date,
            AttendanceRecord.period == data.period,
        )
    )
    before = entity_dict(obj) if obj else None
    if obj:
        for key, value in data.model_dump().items():
            setattr(obj, key, value)
    else:
        obj = AttendanceRecord(**data.model_dump())
        db.add(obj)
    db.flush()
    audit(db, "set", "attendance", obj.id, before=before, after=entity_dict(obj), source_message_id=data.source_message_id)
    if commit:
        db.commit()
    return obj


def attendance_summary(db: Session, class_id: str, start_date: date, end_date: date) -> dict:
    get_class(db, class_id, include_inactive=True)
    if start_date > end_date:
        raise AppError("VALIDATION_ERROR", "end_date不能早于start_date")
    students = list(db.scalars(select(Student.id).where(Student.class_id == class_id, Student.deleted_at.is_(None), Student.status == "active")))
    records = list(
        db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.class_id == class_id,
                AttendanceRecord.attendance_date.between(start_date, end_date),
                AttendanceRecord.period != "recess",
            )
        )
    )
    by_slot = {(r.student_id, r.attendance_date, r.period): r.status for r in records}
    days = (end_date - start_date).days + 1
    total_slots = len(students) * days * 2
    present_equivalent = 0
    counts = defaultdict(int)
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        for sid in students:
            full = by_slot.get((sid, day, "full_day"))
            for period in ("morning", "afternoon"):
                status = by_slot.get((sid, day, period), full or "present")
                counts[status] += 1
                if status in {"present", "late"}:
                    present_equivalent += 1
    return {
        "class_id": class_id,
        "start_date": start_date,
        "end_date": end_date,
        "student_count": len(students),
        "slot_count": total_slots,
        "status_counts": dict(counts),
        "attendance_rate": present_equivalent / total_slots if total_slots else None,
        "note": "大课间不计入出勤率；无记录默认出勤；全天口径按上午和下午两个半天计算",
    }


def create_exam(db: Session, data: ExamCreate, *, commit: bool = True) -> Exam:
    get_class(db, data.class_id)
    names = [s.subject for s in data.subjects]
    if len(names) != len(set(names)):
        raise AppError("VALIDATION_ERROR", "考试科目不能重复")
    exam = Exam(**data.model_dump(exclude={"subjects"}))
    db.add(exam)
    db.flush()
    for subject in data.subjects:
        db.add(ExamSubject(exam_id=exam.id, **subject.model_dump()))
    audit(db, "create", "exam", exam.id, after={"exam": entity_dict(exam), "subjects": [s.model_dump() for s in data.subjects]})
    if commit:
        db.commit()
    return exam


def delete_exam(db: Session, exam_id: str, *, commit: bool = True) -> dict:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise not_found("考试", exam_id)
    score_count = db.scalar(select(func.count(Score.id)).where(Score.exam_id == exam_id)) or 0
    before = {"name": exam.name, "exam_date": exam.exam_date.isoformat(), "status": exam.status, "score_count": score_count}
    name = exam.name
    db.execute(delete(Score).where(Score.exam_id == exam_id))
    db.execute(delete(ExamSubject).where(ExamSubject.exam_id == exam_id))
    db.execute(delete(AnalysisCache).where(AnalysisCache.kind == f"exam_statistics:{exam_id}"))
    audit(db, "delete", "exam", exam_id, before=before)
    db.delete(exam)
    db.flush()
    if commit:
        db.commit()
    return {"id": exam_id, "name": name, "deleted_scores": score_count}


def list_exams(
    db: Session,
    *,
    class_id: str | None = None,
    status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    stmt = select(Exam)
    if class_id:
        stmt = stmt.where(Exam.class_id == class_id)
    if status:
        stmt = stmt.where(Exam.status == status)
    if start_date:
        stmt = stmt.where(Exam.exam_date >= start_date)
    if end_date:
        stmt = stmt.where(Exam.exam_date <= end_date)
    exams = list(db.scalars(stmt.order_by(Exam.exam_date.desc(), Exam.created_at.desc())))
    if not exams:
        return []

    subjects_by_exam: dict[str, list[dict]] = defaultdict(list)
    subjects = db.scalars(
        select(ExamSubject)
        .where(ExamSubject.exam_id.in_([exam.id for exam in exams]))
        .order_by(ExamSubject.exam_id, ExamSubject.subject)
    )
    for subject in subjects:
        subjects_by_exam[subject.exam_id].append({"id": subject.id, "subject": subject.subject, "full_score": subject.full_score})
    return [{**entity_dict(exam), "subjects": subjects_by_exam[exam.id]} for exam in exams]


def save_scores(db: Session, exam_id: str, data: ScoreBatch, *, commit: bool = True) -> list[Score]:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise not_found("考试", exam_id)
    subjects = {s.subject: s for s in db.scalars(select(ExamSubject).where(ExamSubject.exam_id == exam_id))}
    student_ids = {row.student_id for row in data.scores}
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_(student_ids)))}
    seen: set[tuple[str, str]] = set()
    errors = []
    for index, row in enumerate(data.scores):
        key = (row.student_id, row.subject)
        if key in seen:
            errors.append({"index": index, "message": "同一学生同一科目重复"})
        seen.add(key)
        student = students.get(row.student_id)
        subject = subjects.get(row.subject)
        if not student or student.deleted_at or student.class_id != exam.class_id:
            errors.append({"index": index, "student_id": row.student_id, "message": "学生不属于考试班级"})
        elif not subject:
            errors.append({"index": index, "subject": row.subject, "message": "科目不属于该考试"})
        elif row.score > subject.full_score:
            errors.append({"index": index, "student_id": row.student_id, "subject": row.subject, "score": row.score, "full_score": subject.full_score})
    if errors:
        raise AppError("SCORE_EXCEEDS_FULL_SCORE" if any("full_score" in e for e in errors) else "VALIDATION_ERROR", "批量成绩校验失败，未写入任何数据", details={"errors": errors})

    result = []
    for row in data.scores:
        obj = db.scalar(select(Score).where(Score.exam_id == exam_id, Score.student_id == row.student_id, Score.subject == row.subject))
        full_score = subjects[row.subject].full_score
        if obj:
            before = entity_dict(obj)
            obj.score = row.score
            obj.full_score = full_score
            obj.note = row.note
        else:
            before = None
            obj = Score(exam_id=exam_id, student_id=row.student_id, subject=row.subject, score=row.score, full_score=full_score, note=row.note)
            db.add(obj)
        db.flush()
        audit(db, "upsert", "score", obj.id, before=before, after=entity_dict(obj))
        result.append(obj)
    db.flush()
    for subject in {row.subject for row in data.scores}:
        subject_scores = list(db.scalars(select(Score).where(Score.exam_id == exam_id, Score.subject == subject).order_by(Score.score.desc())))
        previous_score = None
        rank = 0
        for index, row in enumerate(subject_scores, 1):
            if previous_score is None or row.score < previous_score:
                rank = index
                previous_score = row.score
            row.class_rank = rank
    if commit:
        db.commit()
    return result


STATISTICS_DISTRIBUTION_BANDS = [
    {"label": "不及格", "min_pct": 0.0, "max_pct": 0.6},
    {"label": "及格", "min_pct": 0.6, "max_pct": 0.7},
    {"label": "中等", "min_pct": 0.7, "max_pct": 0.8},
    {"label": "良好", "min_pct": 0.8, "max_pct": 0.9},
    {"label": "优秀", "min_pct": 0.9, "max_pct": 1.0},
]


def _exam_statistics_fingerprint(db: Session, exam_id: str) -> str:
    subjects = [
        {"id": s.id, "subject": s.subject, "full_score": round(s.full_score, 4)}
        for s in db.scalars(select(ExamSubject).where(ExamSubject.exam_id == exam_id).order_by(ExamSubject.subject))
    ]
    count, max_updated, max_id = db.execute(
        select(func.count(Score.id), func.max(Score.updated_at), func.max(Score.id)).where(Score.exam_id == exam_id)
    ).one()
    raw = json.dumps(
        {"subjects": subjects, "count": count, "max_updated": max_updated.isoformat() if max_updated else None, "max_id": max_id},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(f"exam_statistics:{exam_id}:{raw}".encode()).hexdigest()


def _statistics_cache_put(db: Session, kind: str, key: str, payload: dict) -> None:
    db.merge(AnalysisCache(key=key, kind=kind, payload=payload))
    db.flush()
    limit = settings.analysis_cache_max_entries
    total = db.scalar(select(func.count(AnalysisCache.key))) or 0
    if total > limit:
        stale_keys = list(
            db.scalars(
                select(AnalysisCache.key)
                .order_by(AnalysisCache.created_at.asc(), AnalysisCache.key.asc())
                .limit(total - limit)
            )
        )
        if stale_keys:
            db.execute(delete(AnalysisCache).where(AnalysisCache.key.in_(stale_keys)))
            db.flush()


def _compute_exam_statistics(db: Session, exam: Exam, subject: str | None = None) -> dict:
    stmt = select(Score).where(Score.exam_id == exam.id)
    if subject:
        stmt = stmt.where(Score.subject == subject)
    scores = list(db.scalars(stmt))
    subjects_meta = {s.subject: s for s in db.scalars(select(ExamSubject).where(ExamSubject.exam_id == exam.id))}

    grouped: dict[str, list[Score]] = defaultdict(list)
    for score in scores:
        grouped[score.subject].append(score)
    subjects_result = []
    for name in sorted(grouped):
        rows = grouped[name]
        full = rows[0].full_score
        values = [r.score for r in rows]
        pcts = [v / full for v in values] if full else [0.0] * len(values)
        distribution = []
        for index, band in enumerate(STATISTICS_DISTRIBUTION_BANDS):
            if index == len(STATISTICS_DISTRIBUTION_BANDS) - 1:
                count = sum(1 for p in pcts if p >= band["min_pct"])
            else:
                count = sum(1 for p in pcts if band["min_pct"] <= p < band["max_pct"])
            distribution.append({"label": band["label"], "min_pct": band["min_pct"], "max_pct": band["max_pct"], "count": count})
        subjects_result.append(
            {
                "subject": name,
                "full_score": full,
                "count": len(values),
                "average": round(statistics.fmean(values), 2),
                "median": round(statistics.median(values), 2),
                "std_dev": round(statistics.pstdev(values), 2),
                "highest": max(values),
                "lowest": min(values),
                "pass_rate": sum(v >= full * 0.6 for v in values) / len(values) if full else None,
                "excellent_rate": sum(v >= full * 0.9 for v in values) / len(values) if full else None,
                "distribution": distribution,
            }
        )

    by_student: dict[str, list[Score]] = defaultdict(list)
    for score in scores:
        by_student[score.student_id].append(score)
    students = {
        s.id: s
        for s in db.scalars(select(Student).where(Student.id.in_(list(by_student))))
        if s.deleted_at is None
    }
    totals = []
    for student_id, rows in by_student.items():
        student = students.get(student_id)
        if not student:
            continue
        total_score = round(sum(r.score for r in rows), 2)
        total_full = round(sum(subjects_meta[r.subject].full_score for r in rows if r.subject in subjects_meta), 2)
        totals.append(
            {
                "student_id": student_id,
                "student_name": student.name,
                "student_no": student.student_no,
                "total_score": total_score,
                "total_full": total_full,
                "rate": round(total_score / total_full, 4) if total_full else None,
                "subjects": {r.subject: r.score for r in sorted(rows, key=lambda r: r.subject)},
            }
        )
    totals.sort(key=lambda t: (-t["total_score"], t["student_no"] or ""))
    rank = 0
    previous = None
    for index, item in enumerate(totals, 1):
        if previous is None or item["total_score"] < previous:
            rank = index
            previous = item["total_score"]
        item["rank"] = rank

    return {
        "summary": {
            "exam_id": exam.id,
            "name": exam.name,
            "exam_date": exam.exam_date.isoformat(),
            "status": exam.status,
            "subject_count": len(subjects_meta),
            "score_count": len(scores),
            "student_count": len(totals),
            "single_subject": len(subjects_meta) == 1,
        },
        "subjects": subjects_result,
        "totals": totals,
        "generated_at": now().isoformat(),
    }


def score_statistics(db: Session, exam_id: str, subject: str | None = None, *, commit: bool = True) -> dict:
    exam = db.get(Exam, exam_id)
    if not exam:
        raise not_found("考试", exam_id)
    if subject:
        return _compute_exam_statistics(db, exam, subject)
    kind = f"exam_statistics:{exam_id}"
    key = _exam_statistics_fingerprint(db, exam_id)
    cached = db.get(AnalysisCache, key)
    if cached and isinstance(cached.payload, dict):
        return {**cached.payload, "cache_hit": True}
    payload = _compute_exam_statistics(db, exam)
    try:
        _statistics_cache_put(db, kind, key, payload)
        if commit:
            db.commit()
    except OperationalError:
        # 缓存写入是尽力而为：读会话与单写者并发冲突时放弃本次缓存，统计结果照常返回。
        db.rollback()
    return {**payload, "cache_hit": False}
