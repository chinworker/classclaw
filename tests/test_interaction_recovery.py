from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.entities import InteractionAnalysis, Student, WriteProposal
from app.schemas.domain import InteractionAnalyzeCreate
from app.services import interactions, openclaw_bridge, openclaw_provisioning
from app.utils.time import now
from tests.helpers import teacher_for_class


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
