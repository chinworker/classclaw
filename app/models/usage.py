from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.base import IdMixin
from app.utils.time import now


class UsageBase(DeclarativeBase):
    """Metadata owned exclusively by the separate usage database."""


class AiUsageRecord(UsageBase, IdMixin):
    """Usage metadata only; never prompts, replies or credentials."""

    __tablename__ = "ai_usage_records"
    __table_args__ = (Index("ix_ai_usage_created_operation", "created_at", "operation"),)

    source: Mapped[str] = mapped_column(String(50), default="openclaw_responses")
    operation: Mapped[str] = mapped_column(String(100), index=True)
    model: Mapped[str | None] = mapped_column(String(200))
    response_id: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
