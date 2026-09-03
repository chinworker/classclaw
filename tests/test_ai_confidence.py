from __future__ import annotations

import asyncio

from app.schemas.domain import ClassOnboardingCreate
from app.services import approval, openclaw_bridge
from app.services.ai_confidence import AI_CONFIDENCE_THRESHOLD, evaluate_ai_output
from app.utils.time import today


def test_confidence_contract_requires_threshold_and_nonempty_ai_reasons():
    accepted = evaluate_ai_output({"confidence": AI_CONFIDENCE_THRESHOLD, "reasons": ["关键字段清晰且一致"]})
    low = evaluate_ai_output({"confidence": 0.74, "reasons": ["图片中的学号模糊"]})
    missing_reason = evaluate_ai_output({"confidence": 0.99})

    assert accepted["accepted"] is True
    assert low["accepted"] is False
    assert low["reasons"] == ["图片中的学号模糊"]
    assert missing_reason["accepted"] is False
    assert "原因项" in missing_reason["reasons"][0]


def test_low_confidence_onboarding_data_does_not_change_draft(db, monkeypatch):
    session = approval.create_onboarding(
        db,
        ClassOnboardingCreate(
            initial_draft={
                "class_info": {"name": "高一三班", "grade": "高一"},
                "students": [{"student_no": "001", "name": "原学生"}],
            }
        ),
    )

    async def responses(*_args, **_kwargs):
        return {
            "draft_patch": {"students": [{"student_no": "008", "name": "疑似姓名"}]},
            "confidence": 0.42,
            "reasons": ["第 2 行姓名被图片阴影遮挡"],
            "warnings": [],
            "summary": "名单不清晰",
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    result = asyncio.run(openclaw_bridge.analyze_onboarding_files(db, session.id, "students", [], session.revision))

    db.refresh(session)
    assert result["analysis"]["accepted"] is False
    assert result["analysis"]["reasons"] == ["第 2 行姓名被图片阴影遮挡"]
    assert session.revision == 1
    assert session.draft_json["students"] == [{"student_no": "001", "name": "原学生"}]


def test_low_confidence_timetable_and_seating_data_are_not_returned(db, sample, monkeypatch):
    cls, _, students = sample
    responses = [
        {
            "periods": [{"period_no": 1}],
            "items": [{"weekday": 1, "period_no": 1, "subject": "语文"}],
            "confidence": 0.5,
            "reasons": ["无法确定表格表示单周还是双周"],
            "warnings": [],
            "summary": "课表存在单双周歧义",
        },
        {
            "rows": 1,
            "cols": 1,
            "layout": [[students[0].student_no]],
            "confidence": 0.6,
            "reasons": ["图片中没有标明讲台方向"],
            "warnings": [],
            "summary": "座位方向不明确",
        },
    ]

    async def response(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(openclaw_bridge, "_responses_json", response)
    timetable = asyncio.run(openclaw_bridge.analyze_timetable_files(db, cls.id, []))
    seating = asyncio.run(openclaw_bridge.analyze_seating_files(db, cls.id, []))

    assert timetable["analysis"]["accepted"] is False
    assert timetable["items"] == [] and timetable["periods"] == []
    assert seating["analysis"]["accepted"] is False
    assert "layout" not in seating


def test_low_confidence_student_event_cannot_be_saved_from_analysis(db, sample, monkeypatch):
    cls, _, students = sample

    async def response(*_args, **_kwargs):
        return {
            "event_type": "behavior",
            "subtype": "表现",
            "sentiment": "neutral",
            "severity": "normal",
            "subject": None,
            "summary": "描述过于笼统",
            "confidence": 0.35,
            "reasons": ["“表现不太好”没有说明具体行为"],
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", response)
    result = asyncio.run(
        openclaw_bridge.analyze_student_event(db, cls.id, students[0].id, today(), "今天表现不太好", None)
    )

    assert result["analysis"]["accepted"] is False
    assert result["event"] is None
    assert result["analysis"]["reasons"] == ["“表现不太好”没有说明具体行为"]


def test_every_web_ai_entry_uses_the_low_confidence_reason_dialog(client):
    components = client.get("/app/js/components.js").text
    assert "export function showAiRejection" in components
    assert "数据存在的问题" in components
    for path in ("onboarding.js", "timetablePage.js", "seating.js", "events.js"):
        source = client.get(f"/app/js/pages/{path}").text
        assert "showAiRejection" in source
        assert "analysis?.accepted" in source
