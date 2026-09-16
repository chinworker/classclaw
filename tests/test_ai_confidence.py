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


def test_low_confidence_timetable_items_are_kept_for_review_but_seating_is_not(db, sample, monkeypatch):
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

    assert timetable["analysis"]["accepted"] is True
    assert timetable["analysis"]["review_required"] is True
    assert [row["subject"] for row in timetable["items"]] == ["语文"]
    assert timetable["analysis"]["reasons"] == ["无法确定表格表示单周还是双周"]
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


def test_nested_timetable_matrix_from_onboarding_is_flattened_and_accepted(db, monkeypatch):
    session = approval.create_onboarding(
        db,
        ClassOnboardingCreate(initial_draft={"class_info": {"name": "课表矩阵测试班", "grade": "初一"}}),
    )

    prompts: list[str] = []

    async def responses(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {
            "draft_patch": {
                "base_timetable": [
                    [{"weekday": 1, "period_no": 1, "subject": "语文"}, {"weekday": 2, "period_no": 1, "subject": "数学"}],
                    [{"weekday": 1, "period_no": 2, "subject": "英语"}, {"weekday": 2, "period_no": 2, "subject": "科学"}],
                ],
                "periods": [{"period_no": 1, "name": None}, {"period_no": 2, "name": "午自习"}],
            },
            "confidence": 0.82,
            "reasons": ["表头、星期、节次与科目清晰"],
            "warnings": [],
            "summary": "课表识别完成",
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    result = asyncio.run(openclaw_bridge.analyze_onboarding_files(db, session.id, "timetable", [], session.revision))

    assert result["analysis"]["accepted"] is True
    assert "扁平数组" in prompts[0]
    assert "第5节" in prompts[0]
    db.refresh(session)
    items = session.draft_json["base_timetable"]
    assert len(items) == 4
    assert all(isinstance(row, dict) for row in items)
    assert {(row["weekday"], row["period_no"]) for row in items} == {(1, 1), (1, 2), (2, 1), (2, 2)}


def test_nested_timetable_matrix_from_file_import_is_flattened(db, sample, monkeypatch):
    cls, _, _ = sample

    async def responses(*_args, **_kwargs):
        return {
            "items": [
                [{"weekday": 1, "period_no": 1, "subject": "语文"}],
                [{"weekday": 1, "period_no": 2, "subject": "数学"}],
            ],
            "periods": [{"period_no": 1}, {"period_no": 2}],
            "confidence": 0.9,
            "reasons": ["星期、节次和科目清晰"],
            "warnings": [],
            "summary": "课表识别完成",
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    result = asyncio.run(openclaw_bridge.analyze_timetable_files(db, cls.id, []))

    assert result["analysis"]["accepted"] is True
    assert [(row["period_no"], row["subject"]) for row in result["items"]] == [(1, "语文"), (2, "数学")]


def test_low_confidence_onboarding_timetable_keeps_shifted_period_labels(db, monkeypatch):
    session = approval.create_onboarding(
        db,
        ClassOnboardingCreate(initial_draft={"class_info": {"name": "课表位移测试班", "grade": "初一"}}),
    )

    async def responses(*_args, **_kwargs):
        return {
            "draft_patch": {
                "base_timetable": [
                    [{"weekday": 1, "period_no": 1, "subject": "语文"}, {"weekday": 2, "period_no": 1, "subject": "数学"}],
                    [{"weekday": 1, "period_no": 2, "subject": "英语"}, {"weekday": 2, "period_no": 2, "subject": "科学"}],
                    [{"weekday": 1, "period_no": 3, "subject": "体育"}],
                ],
                "periods": [
                    {"period_no": 1, "name": None, "sort_order": 1},
                    {"period_no": 2, "name": "午自习", "sort_order": 2},
                    {"period_no": 3, "name": None, "sort_order": 3},
                ],
            },
            "confidence": 0.6,
            "reasons": ["图片下半部分反光，部分单元格不确定"],
            "warnings": [],
            "summary": "课表部分可用",
        }

    monkeypatch.setattr(openclaw_bridge, "_responses_json", responses)
    result = asyncio.run(openclaw_bridge.analyze_onboarding_files(db, session.id, "timetable", [], session.revision))

    assert result["analysis"]["accepted"] is True
    assert result["analysis"]["review_required"] is True
    db.refresh(session)
    assert len(session.draft_json["base_timetable"]) == 5
    names = {row["period_no"]: row.get("name") for row in session.draft_json["periods"]}
    assert names == {1: None, 2: "午自习", 3: "第2节"}


def test_every_web_ai_entry_uses_the_low_confidence_reason_dialog(client):
    components = client.get("/app/js/components.js").text
    assert "export function showAiRejection" in components
    assert "数据存在的问题" in components
    for path in ("onboarding.js", "timetablePage.js", "seating.js", "events.js"):
        source = client.get(f"/app/js/pages/{path}").text
        assert "showAiRejection" in source
        assert "analysis?.accepted" in source
