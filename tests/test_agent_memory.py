"""班级 Agent 记忆：预览确认、重复合并、更正冲突与临时作息到期恢复。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.models.entities import ClassAgentMemory
from app.services import agent_memory
from app.utils.time import now
from tests.helpers import teacher_for_class


def _propose(client, operation: str, payload: dict):
    response = client.post("/api/v1/write-proposals", json={"operation_type": operation, "payload": payload})
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def _confirm(client, proposal: dict):
    return client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm",
                       json={"revision": proposal["revision"], "confirmed_by": "李老师"})


def _save(client, class_id: str, entries: list[dict]):
    proposal = _propose(client, "memory.upsert", {"class_id": class_id, "entries": entries})
    confirmed = _confirm(client, proposal)
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["data"]


def _schedule(name: str, start: str, end: str, weekdays: list[int], **extra) -> dict:
    return {"kind": "schedule", "name": name, "start_time": start, "end_time": end, "weekdays": weekdays, **extra}


def _rows(db, class_id: str) -> list[ClassAgentMemory]:
    return list(db.scalars(select(ClassAgentMemory).where(ClassAgentMemory.class_id == class_id)))


def test_memory_upsert_preview_confirm_and_read(client, db, sample):
    cls = sample[0]
    proposal = _propose(client, "memory.upsert", {"class_id": cls.id, "entries": [
        _schedule("早读", "07:30", "08:00", [1, 2, 3, 4, 5]),
        {"kind": "preference", "name": "称呼", "content": "学生称呼班主任为李老师"},
    ]})
    summary = proposal["preview_json"]["summary"]
    assert [change["action"] for change in summary["changes"]] == ["记住", "记住"]
    assert db.scalar(select(func.count(ClassAgentMemory.id))) == 0

    confirmed = _confirm(client, proposal)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["result_json"]["changed_count"] == 2

    listed = client.get(f"/api/v1/classes/{cls.id}/agent-memories").json()["data"]
    assert listed["total"] == 2 and listed["limit"] == agent_memory.MAX_MEMORIES
    monday = now().date() - timedelta(days=now().date().isoweekday() - 1)
    effective = agent_memory.read(db, cls.id, on_date=monday)["effective"]
    assert {item["name"] for item in effective} == {"早读", "称呼"}
    sunday = agent_memory.read(db, cls.id, on_date=monday + timedelta(days=6))["effective"]
    assert {item["name"] for item in sunday} == {"称呼"}


def test_memory_duplicate_is_merged_without_new_row(client, db, sample):
    cls = sample[0]
    _save(client, cls.id, [{"kind": "preference", "name": "班服", "content": "周一穿班服"}])

    identical = client.post("/api/v1/write-proposals", json={"operation_type": "memory.upsert", "payload": {
        "class_id": cls.id, "entries": [{"kind": "preference", "name": "班服", "content": "周一穿班服"}]}})
    assert identical.status_code == 409
    assert identical.json()["error"]["code"] == "MEMORY_UNCHANGED"

    _save(client, cls.id, [{"kind": "preference", "name": "班服", "aliases": ["校服"], "content": "周一穿班服"}])
    assert len(_rows(db, cls.id)) == 1

    # 用别名复述并补充内容时匹配合并到原条目，不新建记忆。
    _save(client, cls.id, [{"kind": "preference", "name": "校服", "content": "周一、周三穿班服"}])
    rows = _rows(db, cls.id)
    assert len(rows) == 1
    assert rows[0].name == "班服" and rows[0].aliases_json == ["校服"]
    assert rows[0].content == "周一、周三穿班服" and rows[0].revision == 3


def test_memory_correction_conflicts_require_explicit_target(client, db, sample):
    cls = sample[0]
    _save(client, cls.id, [_schedule("大课间", "09:40", "10:10", [1, 2, 3, 4, 5])])
    memory_id = _rows(db, cls.id)[0].id

    # 名称相同但适用星期不同：无法判断是更正还是新规则，必须澄清。
    ambiguous = client.post("/api/v1/write-proposals", json={"operation_type": "memory.upsert", "payload": {
        "class_id": cls.id, "entries": [_schedule("大课间", "09:50", "10:20", [1, 2, 3])]}})
    assert ambiguous.status_code == 409
    assert ambiguous.json()["error"]["code"] == "MEMORY_CONFLICT"

    # 带日期的临时调整不能用 memory_id 覆盖长期约定。
    overriding = client.post("/api/v1/write-proposals", json={"operation_type": "memory.upsert", "payload": {
        "class_id": cls.id, "entries": [_schedule("大课间", "14:00", "14:30", [1], memory_id=memory_id,
                                                  valid_from="2026-09-21", valid_to="2026-09-21")]}})
    assert overriding.status_code == 409
    assert overriding.json()["error"]["code"] == "MEMORY_SCOPE_CONFLICT"

    proposal = _propose(client, "memory.upsert", {"class_id": cls.id, "entries": [
        _schedule("大课间", "09:50", "10:20", [1, 2, 3, 4, 5], memory_id=memory_id)]})
    assert proposal["preview_json"]["summary"]["changes"][0]["action"] == "更正"
    assert _confirm(client, proposal).status_code == 200
    row = _rows(db, cls.id)[0]
    assert (row.start_time, row.end_time, row.revision) == ("09:50", "10:20", 2)


def test_memory_temporary_override_and_expiry_recovery(client, db, sample):
    cls = sample[0]
    temp_day = now().date() - timedelta(days=7)
    weekday = temp_day.isoweekday()
    _save(client, cls.id, [_schedule("大课间", "09:40", "10:10", [weekday])])
    _save(client, cls.id, [_schedule("大课间", "14:00", "14:30", [weekday],
                                     valid_from=temp_day.isoformat(), valid_to=temp_day.isoformat())])

    on_temp = agent_memory.read(db, cls.id, on_date=temp_day)
    assert [(item["name"], item["start_time"]) for item in on_temp["effective"]] == [("大课间", "14:00")]

    after = agent_memory.read(db, cls.id, on_date=temp_day + timedelta(days=7))
    assert [(item["name"], item["start_time"]) for item in after["effective"]] == [("大课间", "09:40")]

    current = agent_memory.read(db, cls.id)
    assert [(item["name"], item["start_time"]) for item in current["items"]] == [("大课间", "09:40")]
    with_expired = agent_memory.read(db, cls.id, include_expired=True)
    expired = [item for item in with_expired["items"] if item["expired"]]
    assert [(item["name"], item["start_time"]) for item in expired] == [("大课间", "14:00")]


def test_memory_forget_flow(client, db, sample):
    cls = sample[0]
    _save(client, cls.id, [{"kind": "preference", "name": "班服", "content": "周一穿班服"},
                           {"kind": "fact", "name": "放学时间", "content": "17:30 放学"}])
    keep, drop = sorted(_rows(db, cls.id), key=lambda row: row.name)

    missing = client.post("/api/v1/write-proposals", json={"operation_type": "memory.forget", "payload": {
        "class_id": cls.id, "memory_ids": ["00000000-0000-0000-0000-000000000000"]}})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "MEMORY_NOT_FOUND"

    proposal = _propose(client, "memory.forget", {"class_id": cls.id, "memory_ids": [drop.id]})
    change = proposal["preview_json"]["summary"]["changes"][0]
    assert change["action"] == "忘记" and change["after"] is None
    assert _confirm(client, proposal).status_code == 200
    assert [row.name for row in _rows(db, cls.id)] == [keep.name]


def test_memory_preview_goes_stale_after_concurrent_change(client, db, sample):
    cls = sample[0]
    _save(client, cls.id, [{"kind": "preference", "name": "班服", "content": "周一穿班服"}])
    memory_id = _rows(db, cls.id)[0].id
    stale = _propose(client, "memory.upsert", {"class_id": cls.id, "entries": [
        {"kind": "preference", "name": "班服", "content": "周二穿班服", "memory_id": memory_id}]})
    _save(client, cls.id, [{"kind": "fact", "name": "放学时间", "content": "17:30 放学"}])

    confirmed = _confirm(client, stale)
    assert confirmed.status_code == 409
    assert confirmed.json()["error"]["code"] == "MEMORY_STALE"
    db.expire_all()
    row = next(row for row in _rows(db, cls.id) if row.name == "班服")
    assert row.content == "周一穿班服" and row.revision == 1


def test_memory_batch_confirm_rejects_overlapping_changes(client, db, sample):
    cls = sample[0]
    _save(client, cls.id, [{"kind": "preference", "name": "班服", "content": "周一穿班服"}])
    memory_id = _rows(db, cls.id)[0].id
    proposals = [
        _propose(client, "memory.upsert", {"class_id": cls.id, "entries": [
            {"kind": "preference", "name": "班服", "content": content, "memory_id": memory_id}]})
        for content in ("周二穿班服", "周三穿班服")
    ]
    response = client.post("/api/v1/write-proposals/confirm-batch", json={
        "items": [{"proposal_id": proposal["id"], "revision": proposal["revision"]} for proposal in proposals],
        "confirmed_by": "李老师"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MEMORY_CONFLICT"
    db.expire_all()
    rows = _rows(db, cls.id)
    assert len(rows) == 1 and rows[0].content == "周一穿班服" and rows[0].revision == 1


def test_memory_read_is_scoped_to_owning_teacher(client, db, sample):
    cls, other = sample[0], sample[1]
    _save(client, cls.id, [{"kind": "preference", "name": "班服", "content": "周一穿班服"}])
    headers = teacher_for_class(client, other, db, "memory-outsider")

    forbidden = client.get(f"/api/v1/classes/{cls.id}/agent-memories", headers=headers)
    assert forbidden.status_code == 403
    own = client.get(f"/api/v1/classes/{other.id}/agent-memories", headers=headers)
    assert own.status_code == 200 and own.json()["data"]["total"] == 0
