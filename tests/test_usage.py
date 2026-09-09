from __future__ import annotations

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.entities import ClassRoom
from app.models.usage import AiUsageRecord
from app.services import usage


def test_usage_is_independent_of_request_session(db: Session, usage_db):
    pending = ClassRoom(name="未提交的班级", grade="高一")
    db.add(pending)
    usage.record_openclaw_usage(
        {
            "id": "response-1",
            "model": "openclaw/classclaw-extractor",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_tokens_details": {"cached_tokens": 20},
            },
        },
        user="classclaw-onboarding-import-test",
        model="openclaw/fallback",
    )

    assert pending in db.new
    db.rollback()
    assert "ai_usage_records" not in inspect(db.bind).get_table_names()
    record = usage_db.scalar(select(AiUsageRecord))
    assert record is not None
    assert record.operation == "onboarding-import"
    assert record.model == "openclaw/classclaw-extractor"
    assert record.total_tokens == 150
    assert record.cached_input_tokens == 20
