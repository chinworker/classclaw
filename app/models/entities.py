from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import IdMixin, SoftDeleteMixin, TimestampMixin
from app.utils.time import now


class ClassRoom(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "classes"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(50), nullable=False)
    room: Mapped[str | None] = mapped_column(String(100))
    head_teacher: Mapped[str | None] = mapped_column(String(100))
    semester_name: Mapped[str | None] = mapped_column(String(100))
    semester_start: Mapped[date | None] = mapped_column(Date)
    semester_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'head_teacher')", name="ck_user_role"),
        Index("uq_users_single_admin", "role", unique=True, sqlite_where=text("role = 'admin'")),
    )

    username: Mapped[str] = mapped_column(String(100, collation="NOCASE"), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="head_teacher")
    display_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_session_user_expires", "user_id", "expires_at"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassAgentBinding(Base, IdMixin, TimestampMixin):
    """One isolated OpenClaw agent and one channel account per class."""

    __tablename__ = "class_agent_bindings"
    __table_args__ = (
        UniqueConstraint("class_id", name="uq_class_agent_binding_class"),
        UniqueConstraint("openclaw_agent_id", name="uq_class_agent_binding_agent"),
        UniqueConstraint("channel_id", "channel_account_id", name="uq_class_agent_binding_channel_account"),
        Index("ix_class_agent_binding_status", "status", "updated_at"),
    )

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    openclaw_agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(200))
    workspace_path: Mapped[str] = mapped_column(String(500))
    channel_id: Mapped[str] = mapped_column(String(100), default="openclaw-weixin")
    channel_account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_agent")
    qr_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class SystemSetting(Base, IdMixin, TimestampMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class Student(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("class_id", "student_no", name="uq_student_class_no"),
        Index("ix_students_class_name", "class_id", "name"),
    )

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    student_no: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(50))
    boarding_status: Mapped[str | None] = mapped_column(String(30))
    duty_role: Mapped[str | None] = mapped_column(String(100))
    group_no: Mapped[str | None] = mapped_column(String(30))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    family_status: Mapped[str | None] = mapped_column(String(100))
    father_name: Mapped[str | None] = mapped_column(String(100))
    father_phone: Mapped[str | None] = mapped_column(String(50))
    father_note: Mapped[str | None] = mapped_column(Text)
    mother_name: Mapped[str | None] = mapped_column(String(100))
    mother_phone: Mapped[str | None] = mapped_column(String(50))
    mother_note: Mapped[str | None] = mapped_column(Text)
    enrollment_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[str | None] = mapped_column(Text)


class ClassSubject(Base, IdMixin, TimestampMixin):
    __tablename__ = "class_subjects"
    __table_args__ = (UniqueConstraint("class_id", "name", name="uq_class_subject_name"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_full_score: Mapped[float] = mapped_column(Float, default=100)
    teacher: Mapped[str | None] = mapped_column(String(100))
    weekly_periods: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SeatingSnapshot(Base, IdMixin):
    __tablename__ = "seating_snapshots"
    __table_args__ = (Index("ix_seating_current", "class_id", "snapshot_at"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    rows: Mapped[int] = mapped_column(Integer)
    cols: Mapped[int] = mapped_column(Integer)
    layout_json: Mapped[list] = mapped_column(JSON)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DutyRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "duty_rules"

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    original_text: Mapped[str | None] = mapped_column(Text)
    rule_json: Mapped[dict] = mapped_column(JSON)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")


class DutySchedule(Base, IdMixin):
    __tablename__ = "duty_schedules"

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("duty_rules.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DutyAssignment(Base, IdMixin):
    __tablename__ = "duty_assignments"
    __table_args__ = (Index("ix_duty_assignment_date", "duty_date", "student_id"),)

    duty_schedule_id: Mapped[str] = mapped_column(ForeignKey("duty_schedules.id", ondelete="CASCADE"), index=True)
    duty_date: Mapped[date] = mapped_column(Date)
    item_name: Mapped[str] = mapped_column(String(100))
    area: Mapped[str | None] = mapped_column(String(100))
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    group_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    replacement_for_assignment_id: Mapped[str | None] = mapped_column(ForeignKey("duty_assignments.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DutyScoreItem(Base, IdMixin):
    __tablename__ = "duty_score_items"
    __table_args__ = (UniqueConstraint("class_id", "name", name="uq_duty_score_item_name"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    max_score: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class DutyEvaluation(Base, IdMixin, TimestampMixin):
    __tablename__ = "duty_evaluations"
    __table_args__ = (UniqueConstraint("duty_schedule_id", "duty_date", "item_name", name="uq_duty_evaluation"),)

    duty_schedule_id: Mapped[str] = mapped_column(ForeignKey("duty_schedules.id", ondelete="CASCADE"), index=True)
    duty_date: Mapped[date] = mapped_column(Date, index=True)
    item_name: Mapped[str] = mapped_column(String(100))
    total_score: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float)
    evaluator: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)


class DutyEvaluationDetail(Base, IdMixin):
    __tablename__ = "duty_evaluation_details"
    __table_args__ = (UniqueConstraint("evaluation_id", "score_item_id", name="uq_duty_eval_detail"),)

    evaluation_id: Mapped[str] = mapped_column(ForeignKey("duty_evaluations.id", ondelete="CASCADE"), index=True)
    score_item_id: Mapped[str] = mapped_column(ForeignKey("duty_score_items.id", ondelete="RESTRICT"))
    score: Mapped[float] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)


class Homework(Base, IdMixin, TimestampMixin):
    __tablename__ = "homework"

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    assigned_date: Mapped[date] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="published")
    source_message_id: Mapped[str | None] = mapped_column(String(100))


class HomeworkStudentStatus(Base, IdMixin, TimestampMixin):
    __tablename__ = "homework_student_statuses"
    __table_args__ = (UniqueConstraint("homework_id", "student_id", name="uq_homework_student"),)

    homework_id: Mapped[str] = mapped_column(ForeignKey("homework.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str | None] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(Text)
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))


class StudentEvent(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "student_events"
    __table_args__ = (
        Index("ix_student_event_range", "student_id", "event_date"),
        UniqueConstraint("student_id", "source_type", "source_message_id", "subtype", name="uq_student_event_source"),
    )

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    subtype: Mapped[str] = mapped_column(String(100))
    event_date: Mapped[date] = mapped_column(Date)
    event_time: Mapped[str | None] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str] = mapped_column(String(20), default="neutral")
    severity: Mapped[str] = mapped_column(String(20), default="normal")
    score_delta: Mapped[float | None] = mapped_column(Float)
    source_type: Mapped[str] = mapped_column(String(30), default="web")
    source_message_id: Mapped[str | None] = mapped_column(String(100))
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))


class AttendanceRecord(Base, IdMixin, TimestampMixin):
    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("student_id", "attendance_date", "period", name="uq_attendance_slot"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    attendance_date: Mapped[date] = mapped_column(Date)
    period: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(String(100))


class Exam(Base, IdMixin, TimestampMixin):
    __tablename__ = "exams"

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    exam_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")


class ExamSubject(Base, IdMixin):
    __tablename__ = "exam_subjects"
    __table_args__ = (UniqueConstraint("exam_id", "subject", name="uq_exam_subject"),)

    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(100))
    full_score: Mapped[float] = mapped_column(Float)


class Score(Base, IdMixin, TimestampMixin):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_id", "subject", name="uq_score_exam_student_subject"),
        CheckConstraint("score >= 0", name="ck_score_nonnegative"),
    )

    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), index=True)
    subject: Mapped[str] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float)
    full_score: Mapped[float] = mapped_column(Float)
    class_rank: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class ClassPeriod(Base, IdMixin):
    __tablename__ = "class_periods"
    __table_args__ = (UniqueConstraint("class_id", "period_no", name="uq_class_period"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    period_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BaseTimetable(Base, IdMixin):
    __tablename__ = "base_timetable"
    __table_args__ = (UniqueConstraint("class_id", "weekday", "period_no", name="uq_base_timetable"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    period_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(100))
    teacher: Mapped[str | None] = mapped_column(String(100))
    room: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class LessonOverride(Base, IdMixin, TimestampMixin):
    __tablename__ = "lesson_overrides"
    __table_args__ = (UniqueConstraint("class_id", "lesson_key", name="uq_lesson_override"),)

    class_id: Mapped[str] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    lesson_key: Mapped[str] = mapped_column(String(30), index=True)
    lesson_date: Mapped[date] = mapped_column(Date, index=True)
    period_no: Mapped[int] = mapped_column(Integer)
    original_subject: Mapped[str] = mapped_column(String(100))
    original_teacher: Mapped[str | None] = mapped_column(String(100))
    replacement_subject: Mapped[str | None] = mapped_column(String(100))
    replacement_teacher: Mapped[str | None] = mapped_column(String(100))
    replacement_room: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="normal")
    reason: Mapped[str] = mapped_column(Text)


class Arrangement(Base, IdMixin, TimestampMixin):
    __tablename__ = "arrangements"

    class_id: Mapped[str | None] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    source_type: Mapped[str | None] = mapped_column(String(30))
    source_message_id: Mapped[str | None] = mapped_column(String(100))
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(Base, IdMixin):
    __tablename__ = "reminders"

    arrangement_id: Mapped[str] = mapped_column(ForeignKey("arrangements.id", ondelete="CASCADE"), index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Attachment(Base, IdMixin):
    __tablename__ = "attachments"

    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    stored_path: Mapped[str] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_message_id: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AttachmentLink(Base, IdMixin):
    __tablename__ = "attachment_links"
    __table_args__ = (
        UniqueConstraint("attachment_id", "entity_type", "entity_id", name="uq_attachment_link"),
        Index("ix_attachment_link_entity", "entity_type", "entity_id"),
    )

    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InteractionAnalysis(Base, IdMixin, TimestampMixin):
    __tablename__ = "interaction_analyses"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_interaction_analysis_idempotency"),
        Index("ix_interaction_analysis_status_created", "status", "created_at"),
    )

    channel: Mapped[str] = mapped_column(String(50))
    input_kind: Mapped[str] = mapped_column(String(30), default="natural_language")
    external_message_id: Mapped[str | None] = mapped_column(String(200))
    sender_id: Mapped[str | None] = mapped_column(String(100))
    class_id: Mapped[str | None] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"), index=True)
    onboarding_session_id: Mapped[str | None] = mapped_column(ForeignKey("class_onboarding_sessions.id", ondelete="SET NULL"), index=True)
    attachment_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="received")
    intent: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    questions_json: Mapped[list] = mapped_column(JSON, default=list)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    structured_json: Mapped[dict] = mapped_column(JSON, default=dict)
    proposal_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    analyzed_by: Mapped[str | None] = mapped_column(String(100))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),)

    operator_type: Mapped[str] = mapped_column(String(30), default="user")
    operator_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    before_json: Mapped[dict | list | None] = mapped_column(JSON)
    after_json: Mapped[dict | list | None] = mapped_column(JSON)
    source_message_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WriteProposal(Base, IdMixin, TimestampMixin):
    __tablename__ = "write_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_write_proposal_idempotency"),
        Index("ix_write_proposal_status_expiry", "status", "expires_at"),
    )

    operation_type: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[dict] = mapped_column(JSON)
    normalized_payload_json: Mapped[dict] = mapped_column(JSON)
    preview_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending_review")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    confirmed_by: Mapped[str | None] = mapped_column(String(200))
    source_message_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict | list | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    onboarding_session_id: Mapped[str | None] = mapped_column(ForeignKey("class_onboarding_sessions.id", ondelete="SET NULL"), index=True)


class ClassOnboardingSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "class_onboarding_sessions"

    status: Mapped[str] = mapped_column(String(30), default="draft")
    current_step: Mapped[str] = mapped_column(String(50), default="class_info")
    draft_json: Mapped[dict] = mapped_column(JSON, default=dict)
    field_evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(200))
    source_message_id: Mapped[str | None] = mapped_column(String(200))
    class_id: Mapped[str | None] = mapped_column(ForeignKey("classes.id", ondelete="SET NULL"), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
