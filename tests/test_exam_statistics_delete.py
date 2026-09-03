from dataclasses import replace
from datetime import date

from sqlalchemy import func, select

from app.config import settings
from app.models.entities import AnalysisCache, AuditLog, Exam, ExamSubject, Score
from app.schemas.domain import ExamCreate, ExamSubjectInput, ScoreBatch, ScoreInput
from app.services import academic


def _create_two_subject_exam(db, cls):
    exam = academic.create_exam(
        db,
        ExamCreate(
            class_id=cls.id,
            name="期中考试",
            exam_date=date(2026, 9, 20),
            subjects=[ExamSubjectInput(subject="数学", full_score=100), ExamSubjectInput(subject="语文", full_score=120)],
        ),
    )
    return exam


def _save_default_scores(db, exam, students):
    academic.save_scores(
        db,
        exam.id,
        ScoreBatch(
            scores=[
                ScoreInput(student_id=students[0].id, subject="数学", score=90),
                ScoreInput(student_id=students[0].id, subject="语文", score=108),
                ScoreInput(student_id=students[1].id, subject="数学", score=60),
                ScoreInput(student_id=students[1].id, subject="语文", score=66),
                ScoreInput(student_id=students[2].id, subject="数学", score=45),
                ScoreInput(student_id=students[2].id, subject="语文", score=96),
            ]
        ),
    )


def test_score_statistics_multi_dimensions_and_cache(client, db, sample):
    cls, _, students = sample
    exam = _create_two_subject_exam(db, cls)
    _save_default_scores(db, exam, students)

    first = academic.score_statistics(db, exam.id)
    assert first["cache_hit"] is False
    summary = first["summary"]
    assert summary["subject_count"] == 2
    assert summary["score_count"] == 6
    assert summary["student_count"] == 3
    assert summary["single_subject"] is False

    math = next(s for s in first["subjects"] if s["subject"] == "数学")
    assert math["full_score"] == 100
    assert math["count"] == 3
    assert math["average"] == 65.0
    assert math["median"] == 60.0
    assert math["std_dev"] == round(((25**2 + 5**2 + 20**2) / 3) ** 0.5, 2)
    assert math["highest"] == 90
    assert math["lowest"] == 45
    assert math["pass_rate"] == 2 / 3
    assert math["excellent_rate"] == 1 / 3
    assert [(band["label"], band["count"]) for band in math["distribution"]] == [
        ("不及格", 1),
        ("及格", 1),
        ("中等", 0),
        ("良好", 0),
        ("优秀", 1),
    ]

    totals = first["totals"]
    assert [t["student_name"] for t in totals] == ["张三", "王五", "李四"]
    assert [t["rank"] for t in totals] == [1, 2, 3]
    assert totals[0]["total_score"] == 198
    assert totals[0]["total_full"] == 220
    assert totals[0]["rate"] == round(198 / 220, 4)

    second = academic.score_statistics(db, exam.id)
    assert second["cache_hit"] is True
    assert second["generated_at"] == first["generated_at"]
    assert second["subjects"] == first["subjects"]

    academic.save_scores(db, exam.id, ScoreBatch(scores=[ScoreInput(student_id=students[1].id, subject="数学", score=95)]))
    third = academic.score_statistics(db, exam.id)
    assert third["cache_hit"] is False
    assert third["generated_at"] != first["generated_at"]
    math3 = next(s for s in third["subjects"] if s["subject"] == "数学")
    assert math3["average"] == round((90 + 95 + 45) / 3, 2)


def test_score_statistics_single_subject_exam(client, db, sample):
    cls, _, students = sample
    exam = academic.create_exam(
        db,
        ExamCreate(class_id=cls.id, name="数学单科测", exam_date=date(2026, 9, 21), subjects=[ExamSubjectInput(subject="数学", full_score=100)]),
    )
    academic.save_scores(db, exam.id, ScoreBatch(scores=[ScoreInput(student_id=students[0].id, subject="数学", score=88)]))

    stats = academic.score_statistics(db, exam.id)
    assert stats["summary"]["single_subject"] is True
    assert len(stats["subjects"]) == 1
    assert stats["totals"][0]["total_score"] == 88
    assert stats["totals"][0]["total_full"] == 100
    assert stats["totals"][0]["rate"] == 0.88
    assert stats["totals"][0]["rank"] == 1


def test_analysis_cache_eviction_when_too_large(db, sample, monkeypatch):
    from dataclasses import replace as dataclass_replace

    monkeypatch.setattr(academic, "settings", dataclass_replace(settings, analysis_cache_max_entries=2))
    cls, _, students = sample
    for index in range(3):
        exam = academic.create_exam(
            db,
            ExamCreate(class_id=cls.id, name=f"月考{index}", exam_date=date(2026, 9, 22 + index), subjects=[ExamSubjectInput(subject="数学", full_score=100)]),
        )
        academic.save_scores(db, exam.id, ScoreBatch(scores=[ScoreInput(student_id=students[0].id, subject="数学", score=80 + index)]))
        stats = academic.score_statistics(db, exam.id)
        assert stats["cache_hit"] is False
    assert db.scalar(select(func.count(AnalysisCache.key))) == 2
    remaining_kinds = set(db.scalars(select(AnalysisCache.kind)))
    assert f"exam_statistics:{exam.id}" in remaining_kinds


def test_exam_delete_removes_scores_subjects_and_cache(client, db, sample):
    cls, _, students = sample
    exam = _create_two_subject_exam(db, cls)
    _save_default_scores(db, exam, students)
    academic.score_statistics(db, exam.id)
    assert db.scalar(select(func.count(AnalysisCache.key))) == 1

    response = client.delete(f"/api/v1/exams/{exam.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "期中考试"
    assert data["deleted_scores"] == 6

    assert db.get(Exam, exam.id) is None
    assert db.scalar(select(func.count(Score.id)).where(Score.exam_id == exam.id)) == 0
    assert db.scalar(select(func.count(ExamSubject.id)).where(ExamSubject.exam_id == exam.id)) == 0
    assert db.scalar(select(func.count(AnalysisCache.key))) == 0
    assert db.scalar(select(AuditLog).where(AuditLog.action == "delete", AuditLog.entity_id == exam.id)) is not None

    missing = client.delete(f"/api/v1/exams/{exam.id}")
    assert missing.status_code == 403
