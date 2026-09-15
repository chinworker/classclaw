from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.entities import (
    Arrangement,
    AttendanceRecord,
    DutyAssignment,
    DutyEvaluation,
    DutySchedule,
    Exam,
    Homework,
    HomeworkStudentStatus,
    Reminder,
    Score,
    SeatingSnapshot,
    Student,
    StudentEvent,
)
from app.services.class_student import get_class, get_student
from app.services.duty import duty_statistics
from app.services.timetable import daily_timetable
from app.utils.time import today


def _range(start_date: date | None, end_date: date | None, days: int = 30) -> tuple[date, date]:
    end = end_date or today()
    start = start_date or (end - timedelta(days=days - 1))
    if start > end:
        raise AppError("VALIDATION_ERROR", "end_date不能早于start_date")
    return start, end


def _score_timeline(db: Session, student_id: str, start: date, end: date, subject: str | None) -> list[dict]:
    stmt = (
        select(Exam.id, Exam.name, Exam.exam_date, Score.subject, Score.score, Score.full_score)
        .join(Score, Score.exam_id == Exam.id)
        .where(Score.student_id == student_id, Exam.exam_date.between(start, end))
        .order_by(Exam.exam_date)
    )
    if subject:
        stmt = stmt.where(Score.subject == subject)
    grouped: dict[str, dict] = {}
    for exam_id, name, exam_date, sub, score, full in db.execute(stmt):
        row = grouped.setdefault(exam_id, {"exam_id": exam_id, "exam_name": name, "exam_date": exam_date, "score": 0.0, "full_score": 0.0, "subjects": []})
        row["score"] += score
        row["full_score"] += full
        row["subjects"].append({"subject": sub, "score": score, "full_score": full})
    return list(grouped.values())


def student_comprehensive(
    db: Session,
    student_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    subject: str | None = None,
    event_type: str | None = None,
) -> dict:
    start, end = _range(start_date, end_date)
    student = get_student(db, student_id, include_deleted=True)
    homework_rows = list(
        db.execute(
            select(Homework.id, Homework.title, Homework.assigned_date, HomeworkStudentStatus.status)
            .outerjoin(
                HomeworkStudentStatus,
                (HomeworkStudentStatus.homework_id == Homework.id) & (HomeworkStudentStatus.student_id == student_id),
            )
            .where(Homework.class_id == student.class_id, Homework.assigned_date.between(start, end), Homework.status.not_in(["draft", "cancelled"]))
            .order_by(Homework.assigned_date)
        )
    )
    hw_counts = Counter((status or "pending") for _, _, _, status in homework_rows)
    completed = sum(hw_counts[s] for s in ("submitted", "late", "revised", "exempt"))
    attendance_rows = list(
        db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.student_id == student_id,
                AttendanceRecord.attendance_date.between(start, end),
                AttendanceRecord.period != "recess",
            )
        )
    )
    att_counts = Counter(r.status for r in attendance_rows)
    slots = ((end - start).days + 1) * 2
    absent_equivalent = 0
    for r in attendance_rows:
        weight = 2 if r.period == "full_day" else (1 if r.period in {"morning", "afternoon"} else 0)
        if r.status in {"absent", "leave"}:
            absent_equivalent += weight
    attendance_rate = max(0.0, min(1.0, (slots - absent_equivalent) / slots)) if slots else None
    event_stmt = select(StudentEvent).where(
        StudentEvent.student_id == student_id,
        StudentEvent.event_date.between(start, end),
        StudentEvent.deleted_at.is_(None),
    )
    if event_type:
        event_stmt = event_stmt.where(StudentEvent.event_type == event_type)
    events = list(db.scalars(event_stmt.order_by(StudentEvent.event_date.desc())))
    event_counts = Counter(e.sentiment for e in events)
    serious = [e for e in events if e.severity == "serious"]
    scores = _score_timeline(db, student_id, start, end, subject)
    score_change = None
    if len(scores) >= 2:
        first_pct = scores[-2]["score"] / scores[-2]["full_score"] if scores[-2]["full_score"] else 0
        last_pct = scores[-1]["score"] / scores[-1]["full_score"] if scores[-1]["full_score"] else 0
        score_change = round(last_pct - first_pct, 4)
    duties = list(
        db.scalars(
            select(DutyAssignment)
            .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
            .where(DutyAssignment.student_id == student_id, DutyAssignment.duty_date.between(start, end))
        )
    )
    snapshot = db.scalar(
        select(SeatingSnapshot).where(SeatingSnapshot.class_id == student.class_id).order_by(SeatingSnapshot.snapshot_at.desc()).limit(1)
    )
    seat = None
    if snapshot:
        for row_index, row in enumerate(snapshot.layout_json, 1):
            for col_index, sid in enumerate(row, 1):
                if sid == student_id:
                    seat = {"row": row_index, "col": col_index, "snapshot_id": snapshot.id}
    evidence: list[dict[str, Any]] = []
    evidence.extend({"id": e.id, "date": e.event_date, "type": f"event:{e.event_type}", "summary": e.content[:120]} for e in events[:20])
    evidence.extend({"id": hid, "date": assigned, "type": "homework", "summary": f"{title}：{status or 'pending'}"} for hid, title, assigned, status in homework_rows if (status or "pending") in {"missing", "late", "revision_required"})
    evidence.extend({"id": r.id, "date": r.attendance_date, "type": "attendance", "summary": f"{r.period}:{r.status}"} for r in attendance_rows if r.status != "present")
    attention = []
    if hw_counts["missing"]:
        attention.append({"type": "homework", "message": f"未交作业{hw_counts['missing']}次"})
    if att_counts["late"] or att_counts["absent"]:
        attention.append({"type": "attendance", "message": f"迟到{att_counts['late']}次，缺勤{att_counts['absent']}次"})
    if serious:
        attention.append({"type": "behavior", "message": f"严重事件{len(serious)}条"})
    if score_change is not None and score_change <= -0.1:
        attention.append({"type": "score", "message": f"最近两次考试标准化成绩下降{abs(score_change):.1%}"})
    positive = [{"id": e.id, "date": e.event_date, "summary": e.content[:120]} for e in events if e.sentiment == "positive"][:10]
    warnings = []
    if not homework_rows:
        warnings.append("时间范围内没有作业数据")
    if not attendance_rows:
        warnings.append("无异常考勤记录；按业务规则视为出勤，但无法证明每次点名均已完成")
    if not scores:
        warnings.append("时间范围内没有成绩数据")
    return {
        "student": student,
        "period": {"start_date": start, "end_date": end},
        "data_coverage": {"homework_count": len(homework_rows), "attendance_exception_records": len(attendance_rows), "event_count": len(events), "exam_count": len(scores), "duty_count": len(duties)},
        "metrics": {
            "homework": {"status_counts": dict(hw_counts), "completion_rate": completed / len(homework_rows) if homework_rows else None, "missing_count": hw_counts["missing"], "late_count": hw_counts["late"], "revision_required_count": hw_counts["revision_required"]},
            "attendance": {"attendance_rate": attendance_rate, "late_count": att_counts["late"], "absent_count": att_counts["absent"], "leave_count": att_counts["leave"]},
            "behavior": {"positive_count": event_counts["positive"], "negative_count": event_counts["negative"], "serious_count": len(serious)},
            "scores": {"timeline": scores, "latest_change": score_change},
            "duty": {"total": len(duties), "completed": sum(d.status == "completed" for d in duties), "completion_rate": sum(d.status == "completed" for d in duties) / len(duties) if duties else None},
            "seat": seat,
        },
        "trends": ([{"metric": "score_normalized", "change": score_change, "direction": "up" if score_change > 0 else "down" if score_change < 0 else "flat"}] if score_change is not None else []),
        "attention_items": attention,
        "positive_items": positive,
        "evidence": evidence,
        "warnings": warnings,
    }


def _class_student_analyses(
    db: Session,
    class_id: str,
    start: date,
    end: date,
    subject: str | None = None,
    event_type: str | None = None,
) -> list[dict]:
    """Build all student summaries with a fixed number of database queries."""
    students = list(
        db.scalars(
            select(Student)
            .where(Student.class_id == class_id, Student.deleted_at.is_(None), Student.status == "active")
            .order_by(Student.student_no)
        )
    )
    if not students:
        return []
    student_ids = [student.id for student in students]
    homework = list(
        db.execute(
            select(Homework.id, Homework.title, Homework.assigned_date)
            .where(Homework.class_id == class_id, Homework.assigned_date.between(start, end), Homework.status.not_in(["draft", "cancelled"]))
            .order_by(Homework.assigned_date)
        )
    )
    homework_status = {
        (homework_id, student_id): status
        for homework_id, student_id, status in db.execute(
            select(HomeworkStudentStatus.homework_id, HomeworkStudentStatus.student_id, HomeworkStudentStatus.status)
            .join(Homework, Homework.id == HomeworkStudentStatus.homework_id)
            .where(Homework.class_id == class_id, Homework.assigned_date.between(start, end), HomeworkStudentStatus.student_id.in_(student_ids))
        )
    }
    attendance_by_student: dict[str, list[AttendanceRecord]] = defaultdict(list)
    for row in db.scalars(
        select(AttendanceRecord).where(
            AttendanceRecord.class_id == class_id,
            AttendanceRecord.student_id.in_(student_ids),
            AttendanceRecord.attendance_date.between(start, end),
            AttendanceRecord.period != "recess",
        )
    ):
        attendance_by_student[row.student_id].append(row)
    event_stmt = select(StudentEvent).where(
        StudentEvent.class_id == class_id,
        StudentEvent.student_id.in_(student_ids),
        StudentEvent.event_date.between(start, end),
        StudentEvent.deleted_at.is_(None),
    )
    if event_type:
        event_stmt = event_stmt.where(StudentEvent.event_type == event_type)
    events_by_student: dict[str, list[StudentEvent]] = defaultdict(list)
    for row in db.scalars(event_stmt.order_by(StudentEvent.event_date.desc())):
        events_by_student[row.student_id].append(row)
    score_stmt = (
        select(Score.student_id, Exam.id, Exam.name, Exam.exam_date, Score.subject, Score.score, Score.full_score)
        .join(Exam, Exam.id == Score.exam_id)
        .where(Exam.class_id == class_id, Score.student_id.in_(student_ids), Exam.exam_date.between(start, end))
        .order_by(Exam.exam_date)
    )
    if subject:
        score_stmt = score_stmt.where(Score.subject == subject)
    scores_by_student: dict[str, dict[str, dict]] = defaultdict(dict)
    for student_id, exam_id, name, exam_date, score_subject, score, full_score in db.execute(score_stmt):
        timeline = scores_by_student[student_id]
        item = timeline.setdefault(exam_id, {"exam_id": exam_id, "exam_name": name, "exam_date": exam_date, "score": 0.0, "full_score": 0.0, "subjects": []})
        item["score"] += score
        item["full_score"] += full_score
        item["subjects"].append({"subject": score_subject, "score": score, "full_score": full_score})
    duties_by_student: dict[str, list[DutyAssignment]] = defaultdict(list)
    for row in db.scalars(
        select(DutyAssignment)
        .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
        .where(DutySchedule.class_id == class_id, DutyAssignment.student_id.in_(student_ids), DutyAssignment.duty_date.between(start, end))
    ):
        duties_by_student[row.student_id].append(row)
    snapshot = db.scalar(select(SeatingSnapshot).where(SeatingSnapshot.class_id == class_id).order_by(SeatingSnapshot.snapshot_at.desc()).limit(1))
    seats: dict[str, dict] = {}
    if snapshot:
        for row_index, row in enumerate(snapshot.layout_json, 1):
            for col_index, student_id in enumerate(row, 1):
                if student_id:
                    seats[student_id] = {"row": row_index, "col": col_index, "snapshot_id": snapshot.id}

    analyses = []
    slots = ((end - start).days + 1) * 2
    for student in students:
        homework_rows = [(hid, title, assigned, homework_status.get((hid, student.id), "pending")) for hid, title, assigned in homework]
        hw_counts = Counter(status for _, _, _, status in homework_rows)
        completed = sum(hw_counts[value] for value in ("submitted", "late", "revised", "exempt"))
        attendance_rows = attendance_by_student[student.id]
        att_counts = Counter(row.status for row in attendance_rows)
        absent_equivalent = sum(
            (2 if row.period == "full_day" else 1 if row.period in {"morning", "afternoon"} else 0)
            for row in attendance_rows
            if row.status in {"absent", "leave"}
        )
        attendance_rate = max(0.0, min(1.0, (slots - absent_equivalent) / slots)) if slots else None
        events = events_by_student[student.id]
        event_counts = Counter(row.sentiment for row in events)
        serious = [row for row in events if row.severity == "serious"]
        scores = list(scores_by_student[student.id].values())
        score_change = None
        if len(scores) >= 2:
            first_pct = scores[-2]["score"] / scores[-2]["full_score"] if scores[-2]["full_score"] else 0
            last_pct = scores[-1]["score"] / scores[-1]["full_score"] if scores[-1]["full_score"] else 0
            score_change = round(last_pct - first_pct, 4)
        duties = duties_by_student[student.id]
        evidence: list[dict[str, Any]] = [
            {"id": row.id, "date": row.event_date, "type": f"event:{row.event_type}", "summary": row.content[:120]}
            for row in events[:20]
        ]
        evidence.extend(
            {"id": hid, "date": assigned, "type": "homework", "summary": f"{title}：{status}"}
            for hid, title, assigned, status in homework_rows
            if status in {"missing", "late", "revision_required"}
        )
        evidence.extend(
            {"id": row.id, "date": row.attendance_date, "type": "attendance", "summary": f"{row.period}:{row.status}"}
            for row in attendance_rows
            if row.status != "present"
        )
        attention = []
        if hw_counts["missing"]:
            attention.append({"type": "homework", "message": f"未交作业{hw_counts['missing']}次"})
        if att_counts["late"] or att_counts["absent"]:
            attention.append({"type": "attendance", "message": f"迟到{att_counts['late']}次，缺勤{att_counts['absent']}次"})
        if serious:
            attention.append({"type": "behavior", "message": f"严重事件{len(serious)}条"})
        if score_change is not None and score_change <= -0.1:
            attention.append({"type": "score", "message": f"最近两次考试标准化成绩下降{abs(score_change):.1%}"})
        warnings = []
        if not homework_rows:
            warnings.append("时间范围内没有作业数据")
        if not attendance_rows:
            warnings.append("无异常考勤记录；按业务规则视为出勤，但无法证明每次点名均已完成")
        if not scores:
            warnings.append("时间范围内没有成绩数据")
        analyses.append({
            "student": student,
            "period": {"start_date": start, "end_date": end},
            "data_coverage": {"homework_count": len(homework_rows), "attendance_exception_records": len(attendance_rows), "event_count": len(events), "exam_count": len(scores), "duty_count": len(duties)},
            "metrics": {
                "homework": {"status_counts": dict(hw_counts), "completion_rate": completed / len(homework_rows) if homework_rows else None, "missing_count": hw_counts["missing"], "late_count": hw_counts["late"], "revision_required_count": hw_counts["revision_required"]},
                "attendance": {"attendance_rate": attendance_rate, "late_count": att_counts["late"], "absent_count": att_counts["absent"], "leave_count": att_counts["leave"]},
                "behavior": {"positive_count": event_counts["positive"], "negative_count": event_counts["negative"], "serious_count": len(serious)},
                "scores": {"timeline": scores, "latest_change": score_change},
                "duty": {"total": len(duties), "completed": sum(row.status == "completed" for row in duties), "completion_rate": sum(row.status == "completed" for row in duties) / len(duties) if duties else None},
                "seat": seats.get(student.id),
            },
            "trends": ([{"metric": "score_normalized", "change": score_change, "direction": "up" if score_change > 0 else "down" if score_change < 0 else "flat"}] if score_change is not None else []),
            "attention_items": attention,
            "positive_items": [{"id": row.id, "date": row.event_date, "summary": row.content[:120]} for row in events if row.sentiment == "positive"][:10],
            "evidence": evidence,
            "warnings": warnings,
        })
    return analyses


def student_compare(db: Session, student_id: str, start: date, end: date, compare_start: date, compare_end: date, subject: str | None = None) -> dict:
    current = student_comprehensive(db, student_id, start, end, subject)
    previous = student_comprehensive(db, student_id, compare_start, compare_end, subject)
    deltas = {}
    for group, keys in {"homework": ["completion_rate", "missing_count", "late_count"], "attendance": ["attendance_rate", "late_count", "absent_count"], "behavior": ["positive_count", "negative_count"]}.items():
        deltas[group] = {}
        for key in keys:
            a, b = current["metrics"][group].get(key), previous["metrics"][group].get(key)
            deltas[group][key] = (a - b) if a is not None and b is not None else None
    return {"current": current, "previous": previous, "deltas": deltas}


def class_comprehensive(db: Session, class_id: str, start_date: date | None = None, end_date: date | None = None, subject: str | None = None, event_type: str | None = None) -> dict:
    start, end = _range(start_date, end_date)
    cls = get_class(db, class_id, include_inactive=True)
    analyses = _class_student_analyses(db, class_id, start, end, subject, event_type)
    students = [analysis["student"] for analysis in analyses]
    def avg(values):
        present = [v for v in values if v is not None]
        return round(sum(present) / len(present), 4) if present else None
    subject_values: dict[str, list[float]] = defaultdict(list)
    for a in analyses:
        for exam in a["metrics"]["scores"]["timeline"]:
            for row in exam["subjects"]:
                subject_values[row["subject"]].append(row["score"])
    attention = attention_students(db, class_id, start, end, precomputed=analyses)
    duty = duty_statistics(db, class_id, start, end)
    events = list(db.scalars(select(StudentEvent).where(StudentEvent.class_id == class_id, StudentEvent.event_date.between(start, end), StudentEvent.deleted_at.is_(None))))
    subtype_negative = Counter(e.subtype for e in events if e.sentiment == "negative")
    subtype_positive = Counter(e.subtype for e in events if e.sentiment == "positive")
    return {
        "class": cls,
        "period": {"start_date": start, "end_date": end},
        "student_count": len(students),
        "metrics": {
            "homework_completion_rate": avg([a["metrics"]["homework"]["completion_rate"] for a in analyses]),
            "missing_homework_students": sum(a["metrics"]["homework"]["missing_count"] > 0 for a in analyses),
            "missing_homework_count": sum(a["metrics"]["homework"]["missing_count"] for a in analyses),
            "attendance_rate": avg([a["metrics"]["attendance"]["attendance_rate"] for a in analyses]),
            "late_students": sum(a["metrics"]["attendance"]["late_count"] > 0 for a in analyses),
            "absent_students": sum(a["metrics"]["attendance"]["absent_count"] > 0 for a in analyses),
            "subject_averages": {name: round(sum(values) / len(values), 2) for name, values in subject_values.items()},
            "duty": duty,
        },
        "frequent_negative_behaviors": subtype_negative.most_common(10),
        "frequent_positive_behaviors": subtype_positive.most_common(10),
        "attention_students": attention["students"],
        "data_quality": data_quality(db, class_id, start, end),
        "warnings": (["班级当前没有在读学生"] if not students else []),
    }


def class_compare(db: Session, class_id: str, start: date, end: date, compare_start: date, compare_end: date, subject: str | None = None) -> dict:
    current = class_comprehensive(db, class_id, start, end, subject)
    previous = class_comprehensive(db, class_id, compare_start, compare_end, subject)
    keys = ["homework_completion_rate", "missing_homework_count", "attendance_rate", "late_students", "absent_students"]
    deltas = {key: (current["metrics"][key] - previous["metrics"][key] if current["metrics"][key] is not None and previous["metrics"][key] is not None else None) for key in keys}
    return {"current": current, "previous": previous, "deltas": deltas}


def attention_students(db: Session, class_id: str, start_date: date | None = None, end_date: date | None = None, *, precomputed: list[dict] | None = None) -> dict:
    start, end = _range(start_date, end_date)
    if precomputed is None:
        precomputed = _class_student_analyses(db, class_id, start, end)
    results = []
    for analysis in precomputed:
        m = analysis["metrics"]
        triggers = []
        if m["homework"]["missing_count"] >= 3:
            triggers.append({"rule": "missing_homework_3", "value": m["homework"]["missing_count"]})
        if m["attendance"]["late_count"] >= 3:
            triggers.append({"rule": "late_3", "value": m["attendance"]["late_count"]})
        if m["behavior"]["serious_count"] > 0:
            triggers.append({"rule": "serious_negative_event", "value": m["behavior"]["serious_count"]})
        if m["scores"]["latest_change"] is not None and m["scores"]["latest_change"] <= -0.1:
            triggers.append({"rule": "score_drop_10pct", "value": m["scores"]["latest_change"]})
        if len(triggers) >= 2:
            triggers.append({"rule": "multiple_dimensions", "value": len(triggers)})
        if triggers:
            results.append({"student": analysis["student"], "triggered_rules": triggers, "evidence": analysis["evidence"][:10], "suggested_focus": [item["type"] for item in analysis["attention_items"]]})
    return {"class_id": class_id, "period": {"start_date": start, "end_date": end}, "students": results, "note": "关注结果是当前时间范围内的规则触发，不是永久标签"}


def cross_module(db: Session, class_id: str, start_date: date | None = None, end_date: date | None = None) -> dict:
    start, end = _range(start_date, end_date)
    analyses = _class_student_analyses(db, class_id, start, end)
    rows = []
    for a in analyses:
        sid = a["student"].id
        rows.append({
            "student_id": sid,
            "missing_homework": a["metrics"]["homework"]["missing_count"],
            "late_count": a["metrics"]["attendance"]["late_count"],
            "negative_events": a["metrics"]["behavior"]["negative_count"],
            "homework_completion_rate": a["metrics"]["homework"]["completion_rate"],
            "score_change": a["metrics"]["scores"]["latest_change"],
            "duty_completion_rate": a["metrics"]["duty"]["completion_rate"],
        })
    findings = []
    for row in rows:
        if row["missing_homework"] and row["late_count"]:
            findings.append({"student_id": row["student_id"], "observation": "未交作业与迟到在同一周期内同时出现", "language_constraint": "可能相关，不能解释为因果"})
        if row["score_change"] is not None and row["score_change"] < 0 and row["negative_events"]:
            findings.append({"student_id": row["student_id"], "observation": "成绩下降与负向事件在同一周期内同时出现", "language_constraint": "值得关注，不能解释为因果"})
    return {"class_id": class_id, "period": {"start_date": start, "end_date": end}, "student_metrics": rows, "findings": findings, "principle": "仅报告同时出现、可能相关和值得关注，不推断因果关系"}


def data_quality(db: Session, class_id: str, start_date: date | None = None, end_date: date | None = None) -> dict:
    start, end = _range(start_date, end_date)
    student_count = db.scalar(select(func.count(Student.id)).where(Student.class_id == class_id, Student.deleted_at.is_(None), Student.status == "active")) or 0
    homework_count = db.scalar(select(func.count(Homework.id)).where(Homework.class_id == class_id, Homework.assigned_date.between(start, end))) or 0
    event_count = db.scalar(select(func.count(StudentEvent.id)).where(StudentEvent.class_id == class_id, StudentEvent.event_date.between(start, end), StudentEvent.deleted_at.is_(None))) or 0
    attendance_count = db.scalar(select(func.count(AttendanceRecord.id)).where(AttendanceRecord.class_id == class_id, AttendanceRecord.attendance_date.between(start, end))) or 0
    exam_count = db.scalar(select(func.count(Exam.id)).where(Exam.class_id == class_id, Exam.exam_date.between(start, end))) or 0
    missing = [name for name, count in {"students": student_count, "homework": homework_count, "events": event_count, "exams": exam_count}.items() if count == 0]
    return {"period": {"start_date": start, "end_date": end}, "counts": {"students": student_count, "homework": homework_count, "events": event_count, "attendance_exception_records": attendance_count, "exams": exam_count}, "missing_dimensions": missing, "warnings": (["考勤表只保存例外或明确点名记录，0条不等于未出勤"] if attendance_count == 0 else [])}


def morning_briefing(db: Session, class_id: str, briefing_date: date | None = None) -> dict:
    day = briefing_date or today()
    get_class(db, class_id)
    timetable = daily_timetable(db, class_id, day)
    duties = list(
        db.scalars(
            select(DutyAssignment)
            .join(DutySchedule, DutySchedule.id == DutyAssignment.duty_schedule_id)
            .where(DutySchedule.class_id == class_id, DutyAssignment.duty_date == day, DutyAssignment.status != "replaced")
        )
    )
    yesterday_scores = list(
        db.scalars(
            select(DutyEvaluation)
            .join(DutySchedule, DutySchedule.id == DutyEvaluation.duty_schedule_id)
            .where(DutySchedule.class_id == class_id, DutyEvaluation.duty_date == day - timedelta(days=1))
        )
    )
    start_dt = datetime.combine(day, time.min)
    end_dt = datetime.combine(day + timedelta(days=3), time.max)
    arrangements = list(
        db.scalars(
            select(Arrangement).where(
                (Arrangement.class_id == class_id) | Arrangement.class_id.is_(None),
                Arrangement.status.not_in(["completed", "cancelled"]),
                or_(
                    and_(Arrangement.due_at.is_not(None), Arrangement.due_at <= end_dt),
                    and_(Arrangement.start_at.is_not(None), Arrangement.start_at >= start_dt, Arrangement.start_at <= end_dt),
                ),
            ).order_by(Arrangement.due_at)
        )
    )
    today_arrangements = [
        item
        for item in arrangements
        if (item.start_at and item.start_at.date() == day) or (item.due_at and item.due_at.date() == day)
    ]
    reminder_rows = list(
        db.scalars(
            select(Reminder)
            .where(
                Reminder.arrangement_id.in_([item.id for item in today_arrangements]),
                Reminder.status != "cancelled",
            )
            .order_by(Reminder.remind_at)
        )
    ) if today_arrangements else []
    reminders_by_arrangement: dict[str, list[Reminder]] = defaultdict(list)
    for reminder in reminder_rows:
        reminders_by_arrangement[reminder.arrangement_id].append(reminder)
    missing = list(
        db.execute(
            select(Homework.id, Homework.title, func.count(HomeworkStudentStatus.id))
            .join(HomeworkStudentStatus, HomeworkStudentStatus.homework_id == Homework.id)
            .where(Homework.class_id == class_id, HomeworkStudentStatus.status == "missing")
            .group_by(Homework.id, Homework.title)
        )
    )
    attention = attention_students(db, class_id, day - timedelta(days=13), day)
    expected_items = set()
    for schedule in db.scalars(select(DutySchedule).where(DutySchedule.class_id == class_id, DutySchedule.status == "active", DutySchedule.start_date <= day, DutySchedule.end_date >= day)):
        expected_items.update(a.item_name for a in db.scalars(select(DutyAssignment).where(DutyAssignment.duty_schedule_id == schedule.id)))
    assigned_items = {a.item_name for a in duties}
    return {
        "class_id": class_id,
        "date": day,
        "timetable": timetable,
        "lesson_overrides": [row for row in timetable if row["is_changed"]],
        "duty_assignments": duties,
        "duty_vacancies": sorted(expected_items - assigned_items),
        "yesterday_duty_scores": yesterday_scores,
        "today_reminders": [
            {
                "arrangement_id": item.id,
                "title": item.title,
                "summary": item.summary,
                "start_at": item.start_at,
                "due_at": item.due_at,
                "priority": item.priority,
                "status": item.status,
                "reminder_times": [row.remind_at for row in reminders_by_arrangement[item.id]],
            }
            for item in today_arrangements
        ],
        "due_today": [a for a in arrangements if a.due_at and a.due_at.date() == day],
        "next_3_days": [a for a in arrangements if a.due_at and day < a.due_at.date() <= day + timedelta(days=3)],
        "overdue": [a for a in arrangements if a.due_at and a.due_at.date() < day],
        "missing_homework": [{"homework_id": hid, "title": title, "count": count} for hid, title, count in missing],
        "attention_students": attention["students"],
        "data_quality": data_quality(db, class_id, day - timedelta(days=13), day),
    }
