from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AiUsageRecord
from app.services import usage


def test_usage_reuses_request_session(db: Session):
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
        db=db,
    )

    record = db.scalar(select(AiUsageRecord))
    assert record is not None
    assert record.operation == "onboarding-import"
    assert record.model == "openclaw/classclaw-extractor"
    assert record.total_tokens == 150
    assert record.cached_input_tokens == 20
