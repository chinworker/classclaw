from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.entities import InteractionAnalysis, Student, StudentEvent, WriteProposal
from app.schemas.domain import InteractionAnalyzeCreate
from app.services import interactions, openclaw_bridge, openclaw_provisioning
from app.utils.time import now
from tests.helpers import teacher_for_class


def _card_event(cls, student_no, **changes):
    return {
        "operation_type": "student_event.create", "summary": "登记校内玩牌事件", "confidence": 0.95,
        "reasons": ["名单匹配唯一，日期和行为明确"],
        "payload": {"class_id": cls.id, "student_no": student_no, "event_type": "behavior",
                    "subtype": "校内玩牌", "event_date": "2026-10-22", "content": "带扑克牌到校并在校内玩牌",
                    "sentiment": "negative", "severity": "attention", **changes},
    }


@pytest.mark.parametrize("severity", ["low", "medium"])
def test_invalid_generated_event_fields_report_system_error_without_requesting_more_facts(client, db, sample, monkeypatch, severity):
    cls, _, students = sample

    async def extract(**_kwargs):
        return {"status": "ready", "summary": "登记学生事件", "confidence": 0.95, "reasons": ["事实完整"],
                "operations": [_card_event(cls, students[0].student_no, event_type="discipline", severity=severity,
                                           content="private-rejected-input")]}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", extract)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": cls.id, "text": "周四带牌到校玩"})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["proposals"] == []
    analysis = result["analysis"]
    assert analysis["questions_json"] == []
    structured = analysis["structured_json"]
    rejection = structured["rejected_operations"][0]
    assert rejection["code"] == "VALIDATION_ERROR"
    assert {field["field"] for field in rejection["fields"]} == {"event_type", "severity"}
    assert "behavior" in rejection["fields"][0]["message"]
    assert "系统生成问题" in structured["rejected_reasons"][0]
    assert "private-rejected-input" not in str(structured)
    assert db.scalar(select(func.count(WriteProposal.id))) == 0
    assert db.scalar(select(func.count(StudentEvent.id))) == 0


def test_resolved_name_event_previews_roster_name_and_only_writes_after_confirmation(client, db, sample, monkeypatch):
    cls, _, students = sample
    students[0].student_no, students[0].name = "32", "李欣辰"
    db.commit()

    async def extract(**kwargs):
        assert kwargs["raw_text"] == "这周四李欣成带扑克牌来学校玩"
        assert {"student_no": "32", "name": "李欣辰", "gender": None, "status": "active"} in kwargs["context"]["students"]
        return {"status": "ready", "summary": "登记学生事件", "confidence": 0.95, "reasons": ["唯一近音匹配"],
                "operations": [_card_event(cls, "32")]}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", extract)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": cls.id,
                           "text": "这周四李欣成带扑克牌来学校玩"})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["analysis"]["status"] == "awaiting_review"
    assert result["analysis"]["questions_json"] == []
    proposal = result["proposals"][0]
    summary = proposal["preview_json"]["summary"]
    assert summary["name"] == "李欣辰" and summary["student_no"] == "32"
    assert summary["event_date"] == "2026-10-22"
    assert summary["event_type"] == "behavior" and summary["severity"] == "attention"
    assert "student_id" not in summary
    assert db.scalar(select(func.count(StudentEvent.id))) == 0
    confirmed = client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm",
                            json={"revision": proposal["revision"], "confirmed_by": "班主任"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "completed"
    event = db.scalar(select(StudentEvent))
    assert event.student_id == students[0].id and event.content == summary["content"]
    assert students[0].name == "李欣辰"


def test_invalid_event_does_not_hide_an_independent_valid_preview(client, db, sample, monkeypatch):
    cls, _, students = sample

    async def extract(**_kwargs):
        return {"status": "ready", "confidence": 0.95, "reasons": ["两个事实明确"], "operations": [
            _card_event(cls, students[0].student_no),
            _card_event(cls, students[1].student_no, severity="medium"),
        ]}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", extract)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": cls.id, "text": "两名学生校内玩牌"})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["analysis"]["status"] == "awaiting_review"
    assert len(result["proposals"]) == 1
    assert result["analysis"]["structured_json"]["rejected_operations"][0]["fields"][0]["field"] == "severity"
    assert db.scalar(select(func.count(StudentEvent.id))) == 0


def batch_payload(cls, students, gender="男"):
    return {"class_id": cls.id, "student_ids": [student.id for student in students],
            "changes": {"gender": gender}, "only_if_empty": ["gender"]}


def test_full_roster_gender_fill_uses_two_reviewed_groups(client, db, sample, monkeypatch):
    cls, _, initial = sample
    roster = [*initial[:3], *(Student(class_id=cls.id, student_no=f"{i:03}", name=f"学生{i}") for i in range(4, 43))]
    roster[2].gender = "女"
    roster[-1].gender = " "
    db.add_all(roster)
    db.commit()

    async def analyze_interaction(**kwargs):
        context = kwargs["context"]
        assert len(context["students"]) == 42
        assert all("id" not in row for row in context["students"])
        assert next(row for row in context["students"] if row["student_no"] == "003")["gender"] == "女"
        groups = {"男": [], "女": []}
        for row in context["students"]:
            if not (row["gender"] or "").strip():
                groups["男" if int(row["student_no"]) <= 21 else "女"].append(row["student_no"])
        return {"status": "ready", "confidence": 1, "reasons": ["范围和规则明确"], "operations": [
            {"operation_type": "student.update.batch", "confidence": 1, "reasons": ["按数值学号补充空值"],
             "payload": {"class_id": cls.id, "student_nos": nos, "changes": {"gender": gender}, "only_if_empty": ["gender"]}}
            for gender, nos in groups.items()
        ]}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", analyze_interaction)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": cls.id,
                           "text": "只给未设置性别的同学补充性别，21号及以前男，其他女"})
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert result["analysis"]["status"] == "awaiting_review"
    proposals = result["proposals"]
    assert len(proposals) == 2
    assert [row["preview_json"]["summary"]["record_count"] for row in proposals] == [20, 21]
    assert sum(len(row["preview_json"]["students"]) for row in proposals) == 41
    assert roster[0].gender is None
    fetched = client.get(f"/api/v1/interaction-analyses/{result['analysis']['id']}")
    assert fetched.status_code == 200
    assert len(fetched.json()["data"]["proposals"]) == 2
    confirmed = client.post("/api/v1/write-proposals/confirm-batch", json={"confirmed_by": "班主任", "bound_class_id": cls.id,
                            "items": [{"proposal_id": p["id"], "revision": p["revision"]} for p in proposals]})
    assert confirmed.status_code == 200, confirmed.text
    assert all(p["status"] == "completed" for p in confirmed.json()["data"])
    assert roster[0].gender == roster[20].gender == "男"
    assert roster[2].gender == "女"
    assert all(row.gender == "女" for row in roster[21:])


@pytest.mark.parametrize("invalid", ["foreign", "deleted", "existing", "duplicate", "empty"])
def test_gender_batch_rejects_invalid_scope_without_writing(client, db, sample, invalid):
    cls, _, students = sample
    targets = students[:2]
    if invalid == "foreign":
        targets = [students[0], students[3]]
    elif invalid == "deleted":
        students[1].deleted_at = now()
    elif invalid == "existing":
        students[1].gender = "女"
    elif invalid == "duplicate":
        targets = [students[0], students[0]]
    elif invalid == "empty":
        targets = []
    db.commit()
    response = client.post("/api/v1/write-proposals", json={"operation_type": "student.update.batch",
                           "payload": batch_payload(cls, targets)})
    assert response.status_code in {400, 403, 409, 422}, response.text
    assert students[0].gender is None
    assert db.scalar(select(func.count(WriteProposal.id))) == 0


def test_gender_batch_conflict_rolls_back_the_whole_confirmation(client, db, sample):
    cls, _, students = sample
    proposals = [client.post("/api/v1/write-proposals", json={"operation_type": "student.update.batch",
                 "payload": batch_payload(cls, [student])}).json()["data"] for student in students[:2]]
    students[1].gender = "女"
    db.commit()
    response = client.post("/api/v1/write-proposals/confirm-batch", json={"confirmed_by": "班主任", "bound_class_id": cls.id,
                           "items": [{"proposal_id": p["id"], "revision": p["revision"]} for p in proposals]})
    assert response.status_code == 409
    assert students[0].gender is None
    assert students[1].gender == "女"
    assert all(db.get(WriteProposal, p["id"]).status == "pending_review" for p in proposals)


def test_analysis_cannot_propose_a_foreign_class_batch(client, db, sample, monkeypatch):
    cls, other, students = sample

    async def foreign(**_kwargs):
        return {"status": "ready", "confidence": 1, "reasons": ["测试越界提取"], "operations": [
            {"operation_type": "student.update.batch", "confidence": 1, "reasons": ["测试越界提取"],
             "payload": batch_payload(other, [students[3]])},
        ]}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", foreign)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": cls.id, "text": "补充性别"})
    assert response.status_code == 201
    result = response.json()["data"]
    assert result["analysis"]["status"] == "needs_clarification"
    assert result["proposals"] == []
    assert db.scalar(select(func.count(WriteProposal.id))) == 0
    assert students[3].gender is None


def test_analysis_read_enforces_ownership_of_nested_analysis(client, db, sample):
    cls, other, _ = sample
    headers = teacher_for_class(client, cls, db, "analysisreader")
    rows = [InteractionAnalysis(class_id=row.id, channel="web", input_kind="natural_language", status="failed") for row in (cls, other)]
    db.add_all(rows)
    db.commit()
    assert client.get(f"/api/v1/interaction-analyses/{rows[0].id}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/interaction-analyses/{rows[1].id}", headers=headers).status_code == 403


def test_cancelled_analysis_cannot_remain_in_progress(db, sample, monkeypatch):
    async def cancelled(**_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(interactions.analyze(db, InteractionAnalyzeCreate(channel="web", class_id=sample[0].id,
                    external_message_id="cancelled-message", text="补充性别")))
    row = db.scalar(select(InteractionAnalysis).where(InteractionAnalysis.idempotency_key == "web:cancelled-message"))
    assert row.status == "failed"
    assert row.analyzed_at is not None
    assert row.proposal_ids_json == []


def test_incomplete_extraction_cannot_create_a_preview(client, db, sample, monkeypatch):
    async def truncated(*_args, **_kwargs):
        return {"status": "incomplete", "output": [{"type": "message", "content": [{"type": "output_text", "text":
            '{"status":"ready","confidence":1,"reasons":["部分输出"],"operations":[]}',
        }]}]}

    monkeypatch.setattr(openclaw_bridge, "_request_responses", truncated)
    response = client.post("/api/v1/interaction-analyses", json={"channel": "web", "class_id": sample[0].id, "text": "补充性别"})
    assert response.status_code == 502
    assert "分析未完整完成" in response.json()["error"]["message"]
    assert db.scalar(select(func.count(WriteProposal.id))) == 0
    assert db.scalar(select(InteractionAnalysis.status)) == "failed"


def _thinking_client(monkeypatch, error_factory, status = 400):
    class Client:
        async def post(self, _url, **_kwargs):
            return httpx.Response(status, json={"ok": False, "error": error_factory()})

    monkeypatch.setattr(openclaw_provisioning, "get_http_client", lambda: Client())


@pytest.mark.parametrize("legacy", [False, True])
def test_unsupported_thinking_reports_supported_levels(monkeypatch, legacy):
    _thinking_client(monkeypatch, lambda: {"message": 'thinkingLevel "xhigh" is not supported for kimi/kimi-for-coding (use off|low|medium|high)'} if legacy else {
        "code": "CHAT_THINKING_UNSUPPORTED", "supported_levels": ["off", "low", "medium", "high", "private-value"]})
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_provisioning.set_web_session_thinking("session", "xhigh"))
    assert caught.value.code == "CHAT_THINKING_UNSUPPORTED"
    assert "请选择：关闭、低、中、高" in caught.value.message
    assert caught.value.details["supported_levels"] == ["off", "low", "medium", "high"]


def test_thinking_service_outage_is_not_reported_as_model_incompatibility(monkeypatch):
    _thinking_client(monkeypatch, lambda: {"message": "private gateway detail"}, status=503)
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_provisioning.set_web_session_thinking("session", "off"))
    assert caught.value.code == "CHAT_THINKING_UNAVAILABLE"
    assert "private" not in caught.value.message
    assert "模型" not in caught.value.message


@pytest.mark.parametrize("legacy", [False, True])
def test_binary_thinking_error_keeps_low_as_the_enabled_option(monkeypatch, legacy):
    _thinking_client(monkeypatch, lambda: {"message": 'thinkingLevel "minimal" is not supported for kimi/kimi-for-coding (use off|on)'} if legacy else {
        "code": "CHAT_THINKING_UNSUPPORTED", "supported_levels": ["off", "low"], "supported_level_labels": {"low": "on"}})
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_provisioning.set_web_session_thinking("session", "minimal"))
    assert "请选择：关闭、开启" in caught.value.message
    assert caught.value.details["supported_levels"] == ["off", "low"]
    assert caught.value.details["supported_level_labels"] == {"off": "关闭", "low": "开启"}
