from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=50)
    room: str | None = None
    head_teacher: str | None = None
    semester_name: str | None = None
    semester_start: date | None = None
    semester_end: date | None = None

    @model_validator(mode="after")
    def validate_semester(self):
        if self.semester_start and self.semester_end and self.semester_start > self.semester_end:
            raise ValueError("semester_end不能早于semester_start")
        return self


class ClassUpdate(BaseModel):
    name: str | None = None
    grade: str | None = None
    room: str | None = None
    head_teacher: str | None = None
    semester_name: str | None = None
    semester_start: date | None = None
    semester_end: date | None = None
    status: Literal["active", "inactive"] | None = None


class StudentCreate(BaseModel):
    class_id: str
    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    gender: str | None = None
    phone: str | None = None
    boarding_status: str | None = None
    duty_role: str | None = None
    group_no: str | None = None
    tags: list[str] = Field(default_factory=list)
    family_status: str | None = None
    father_name: str | None = None
    father_phone: str | None = None
    father_note: str | None = None
    mother_name: str | None = None
    mother_phone: str | None = None
    mother_note: str | None = None
    enrollment_date: date | None = None
    status: str = "active"
    notes: str | None = None


class StudentUpdate(BaseModel):
    student_no: str | None = None
    name: str | None = None
    gender: str | None = None
    phone: str | None = None
    boarding_status: str | None = None
    duty_role: str | None = None
    group_no: str | None = None
    tags: list[str] | None = None
    family_status: str | None = None
    father_name: str | None = None
    father_phone: str | None = None
    father_note: str | None = None
    mother_name: str | None = None
    mother_phone: str | None = None
    mother_note: str | None = None
    enrollment_date: date | None = None
    status: str | None = None
    notes: str | None = None


class SeatingCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rows: int = Field(ge=1, le=30)
    cols: int = Field(ge=1, le=30)
    layout: list[list[str | None]]
    change_note: str | None = None


class SeatingSwap(BaseModel):
    student_a_id: str
    student_b_id: str
    change_note: str | None = None


class SeatingRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DutyRuleCreate(BaseModel):
    class_id: str
    name: str
    original_text: str | None = None
    rule_json: dict[str, Any]
    effective_from: date
    effective_to: date
    status: Literal["draft", "active", "disabled"] = "draft"


class DutyRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    original_text: str | None = None
    rule_json: dict[str, Any] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: Literal["draft", "active", "disabled"] | None = None


class DutyPreviewRequest(BaseModel):
    class_id: str
    name: str
    start_date: date
    end_date: date
    rule_id: str | None = None
    rule_json: dict[str, Any]


class DutyConfirmRequest(DutyPreviewRequest):
    preview_token: str | None = None


class DutyReplaceRequest(BaseModel):
    replacement_student_id: str
    note: str | None = None


class DutyAssignmentScore(BaseModel):
    score: float = Field(ge=0, le=5)
    note: str | None = None


class DutyEvaluationCreate(BaseModel):
    duty_schedule_id: str
    duty_date: date
    item_name: str
    evaluator: str | None = None
    comment: str | None = None
    details: list[dict[str, Any]]


class HomeworkCreate(BaseModel):
    class_id: str
    title: str
    subject: str
    description: str | None = None
    assigned_date: date
    due_at: datetime | None = None
    status: Literal["draft", "published", "closed", "cancelled"] = "published"
    source_message_id: str | None = None


class HomeworkStatusItem(BaseModel):
    student_id: str
    status: Literal["pending", "submitted", "late", "missing", "exempt", "revision_required", "revised"]
    submitted_at: datetime | None = None
    score: float | None = None
    level: str | None = None
    comment: str | None = None


class HomeworkBatchStatus(BaseModel):
    items: list[HomeworkStatusItem] = Field(min_length=1)


class StudentEventCreate(BaseModel):
    class_id: str
    student_id: str
    event_type: Literal["homework", "attendance", "behavior", "communication", "honor", "other"]
    subtype: str
    event_date: date
    event_time: str | None = None
    subject: str | None = None
    content: str
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    severity: Literal["normal", "attention", "serious"] = "normal"
    score_delta: float | None = None
    source_type: Literal["wechat_text", "wechat_image", "file", "web", "system"] = "web"
    source_message_id: str | None = None
    attachment_id: str | None = None


class AttendanceSet(BaseModel):
    class_id: str
    student_id: str
    attendance_date: date
    period: Literal["full_day", "morning", "afternoon", "recess", "care_1", "care_2"]
    status: Literal["present", "late", "absent", "leave"]
    note: str | None = None
    source_message_id: str | None = None


class ExamSubjectInput(BaseModel):
    subject: str
    full_score: float = Field(gt=0)


class ExamCreate(BaseModel):
    class_id: str
    name: str
    exam_date: date
    status: str = "active"
    subjects: list[ExamSubjectInput] = Field(min_length=1)


class ScoreInput(BaseModel):
    student_id: str
    subject: str
    score: float = Field(ge=0)
    note: str | None = None


class ScoreBatch(BaseModel):
    scores: list[ScoreInput] = Field(min_length=1)


class PeriodCreate(BaseModel):
    period_no: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=100)
    sort_order: int = 0
    enabled: bool = True


class TimetableItem(BaseModel):
    weekday: int = Field(ge=1, le=7)
    period_no: int = Field(ge=1)
    subject: str
    teacher: str | None = None
    room: str | None = None


class TimetableReplace(BaseModel):
    items: list[TimetableItem] = Field(min_length=1)


class TimetableImportApply(BaseModel):
    periods: list[PeriodCreate] = Field(min_length=1)
    items: list[TimetableItem] = Field(min_length=1)


class LessonOverrideCreate(BaseModel):
    class_id: str
    lesson_date: date
    period_no: int = Field(ge=1)
    replacement_subject: str | None = None
    replacement_teacher: str | None = None
    replacement_room: str | None = None
    status: Literal["normal", "cancelled"] = "normal"
    reason: str


class LessonSwapRequest(BaseModel):
    class_id: str
    lesson_date: date | None = None
    lesson_date_a: date | None = None
    lesson_date_b: date | None = None
    period_a: int
    period_b: int
    reason: str

    @model_validator(mode="after")
    def normalize_dates(self):
        if self.lesson_date:
            self.lesson_date_a = self.lesson_date_a or self.lesson_date
            self.lesson_date_b = self.lesson_date_b or self.lesson_date
        if not self.lesson_date_a or not self.lesson_date_b:
            raise ValueError("课程互换需要填写两节课的日期")
        if self.lesson_date_a == self.lesson_date_b and self.period_a == self.period_b:
            raise ValueError("不能互换同一节课")
        return self


class LessonBatchChangeRequest(BaseModel):
    class_id: str
    start_date: date
    end_date: date
    weekdays: list[int]
    period_no: int
    replacement_subject: str | None = None
    replacement_teacher: str | None = None
    replacement_room: str | None = None
    status: Literal["normal", "cancelled"] = "normal"
    reason: str


class ArrangementCreate(BaseModel):
    class_id: str | None = None
    title: str
    summary: str | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    priority: Literal["high", "medium", "low"] = "medium"
    status: Literal["pending", "in_progress", "completed", "cancelled", "overdue"] = "pending"
    source_type: str | None = None
    source_message_id: str | None = None
    attachment_id: str | None = None
    reminder_times: list[datetime] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def default_and_validate_reminders(self):
        anchor = self.start_at or self.due_at
        if not self.reminder_times and anchor:
            self.reminder_times = [anchor - timedelta(hours=3)]
        if self.reminder_times and not anchor:
            raise ValueError("设置提醒前必须提供start_at或due_at")
        unique: list[datetime] = []
        seen: set[str] = set()
        for remind_at in self.reminder_times:
            key = remind_at.isoformat()
            if key not in seen:
                seen.add(key)
                unique.append(remind_at)
        self.reminder_times = unique
        return self


class InteractionAnalyzeCreate(BaseModel):
    channel: str = Field(min_length=1, max_length=50)
    external_message_id: str | None = Field(default=None, max_length=200)
    sender_id: str | None = Field(default=None, max_length=100)
    message_type: str = Field(default="text", max_length=30)
    text: str | None = Field(default=None, max_length=100_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)
    class_id: str | None = None
    onboarding_session_id: str | None = None
    requested_by: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=200)


class OnboardingAnalyzeText(BaseModel):
    target_section: Literal["class_info", "subjects", "students", "timetable", "all"]
    expected_revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=100_000)


class StudentEventAnalyzeRequest(BaseModel):
    student_id: str
    event_date: date
    content: str = Field(min_length=1, max_length=5000)
    subject: str | None = Field(default=None, max_length=100)


class AttachmentLinkCreate(BaseModel):
    entity_type: str
    entity_id: str


class ORMOut(ORMModel):
    id: str


class EvidenceItem(BaseModel):
    source_type: str
    source_id: str | None = None
    attachment_id: str | None = None
    location: str | None = None
    summary: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class WriteProposalCreate(BaseModel):
    operation_type: str
    payload: dict[str, Any]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    requested_by: str | None = None
    source_message_id: str | None = None
    idempotency_key: str | None = None
    expires_in_minutes: int = Field(default=30, ge=1, le=1440)
    onboarding_session_id: str | None = None


class WriteProposalConfirm(BaseModel):
    revision: int = Field(ge=1)
    confirmed_by: str = Field(min_length=1, max_length=200)
    confirmation_note: str | None = Field(default=None, max_length=500)
    bound_class_id: str | None = None


class WriteProposalConfirmItem(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)


class WriteProposalBatchConfirm(BaseModel):
    items: list[WriteProposalConfirmItem] = Field(min_length=1, max_length=50)
    confirmed_by: str = Field(min_length=1, max_length=200)
    confirmation_note: str | None = Field(default=None, max_length=500)
    bound_class_id: str | None = None


class ClassOnboardingCreate(BaseModel):
    created_by: str | None = None
    source_message_id: str | None = None
    initial_draft: dict[str, Any] = Field(default_factory=dict)
    field_evidence: dict[str, Any] = Field(default_factory=dict)


class ClassOnboardingUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    current_step: str | None = None
    draft_patch: dict[str, Any] = Field(default_factory=dict)
    field_evidence_patch: dict[str, Any] = Field(default_factory=dict)
    replace_lists: bool = False


class WechatLoginWait(BaseModel):
    current_qr_data_url: str | None = Field(default=None, max_length=20_000)
