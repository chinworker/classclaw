"""Agent / Channels 走 WriteProposal 的点名广播与音量操作。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.models.entities import AttendanceRecord, ClassroomBroadcast, ClassroomDeviceCommand, WriteProposal
from tests.helpers import online_terminal, pair_terminal


def _propose(client, operation: str, payload: dict):
    response = client.post("/api/v1/write-proposals", json={"operation_type": operation, "payload": payload})
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def _confirm(client, proposal: dict, **extra):
    return client.post(f"/api/v1/write-proposals/{proposal['id']}/confirm",
                       json={"revision": proposal["revision"], "confirmed_by": "李老师", **extra})


def test_broadcast_proposal_resolves_student_nos_and_freezes_the_sentence(client, db, sample):
    cls, _, students = sample
    pair_terminal(client, cls)
    proposal = _propose(client, "classroom.broadcast.send", {
        "class_id": cls.id, "mode": "three_part", "student_nos": ["3号", "1"],
        "time_phrase": "现在", "predicate": "去扫地", "merge_mode": "per_student"})
    preview = proposal["preview_json"]
    assert preview["ready"] is True and preview["title"] == "教室点名广播"
    # 学号解析后仍按自然升序冻结，预览展示学号与姓名而不是 UUID。
    assert preview["summary"]["texts"] == ["请张三同学现在去扫地。", "请王五同学现在去扫地。"]
    assert preview["summary"]["recipients"] == [{"student_no": "001", "name": "张三"}, {"student_no": "003", "name": "王五"}]
    rendered = json.dumps(preview, ensure_ascii=False)
    assert students[0].id not in rendered and students[2].id not in rendered
    assert "不是考勤" in preview["confirmation_message"]
    # 预览阶段不写广播记录，也不登记命令。
    assert db.scalar(select(ClassroomBroadcast)) is None
    assert db.scalar(select(ClassroomDeviceCommand)) is None
    assert proposal["normalized_payload_json"]["segments"][0]["text"] == "请张三同学现在去扫地。"


def test_confirm_registers_broadcast_and_reports_delivery_not_success(client, db, sample):
    cls, _, _students = sample
    paired = pair_terminal(client, cls)
    proposal = _propose(client, "classroom.broadcast.send", {
        "class_id": cls.id, "mode": "three_part", "student_nos": ["2"], "predicate": "来老师办公室"})
    with online_terminal(db, paired["device_id"], cls.id):
        confirmed = _confirm(client, proposal)
    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["status"] == "completed"
    result = data["result_json"]
    assert result["texts"] == ["请李四同学现在来老师办公室。"]
    # 提案完成只代表已登记下发，不代表已经发出声音。
    assert "以教室终端回执为准" in result["note"]
    assert result["delivery"]["pushed"] is True
    command = db.get(ClassroomDeviceCommand, result["command_id"])
    assert command.kind == "broadcast.show" and command.source_type == "agent"
    assert command.status == "authorized"
    assert db.get(ClassroomBroadcast, result["broadcast_id"]).speak_status == "authorized"
    assert db.scalar(select(AttendanceRecord)) is None

    # 重复确认不会重复播报，也不会新增广播记录。
    replay = _confirm(client, proposal)
    assert replay.status_code == 200
    assert len(list(db.scalars(select(ClassroomBroadcast)))) == 1
    assert len(list(db.scalars(select(ClassroomDeviceCommand)))) == 1


def test_confirm_is_refused_when_the_terminal_is_offline(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    proposal = _propose(client, "classroom.broadcast.send",
                        {"class_id": cls.id, "mode": "custom", "text": "请现在保持安静"})
    confirmed = _confirm(client, proposal)
    assert confirmed.status_code == 409
    assert confirmed.json()["error"]["code"] == "DEVICE_OFFLINE"
    db.expire_all()
    # 离线时整批回滚：既不留下广播记录，也不把提案标记为已完成。
    assert db.get(WriteProposal, proposal["id"]).status == "pending_review"
    assert db.scalar(select(ClassroomBroadcast)) is None


def test_broadcast_proposal_cannot_cross_classes(client, db, sample):
    cls, other, _ = sample
    paired = pair_terminal(client, cls)
    proposal = _propose(client, "classroom.broadcast.send",
                        {"class_id": cls.id, "mode": "custom", "text": "请现在保持安静"})
    with online_terminal(db, paired["device_id"], cls.id):
        denied = _confirm(client, proposal, bound_class_id=other.id)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CLASS_SCOPE_VIOLATION"
    assert db.scalar(select(ClassroomBroadcast)) is None

    scoped = _propose(client, "classroom.broadcast.send",
                      {"class_id": other.id, "mode": "custom", "text": "外班广播"})
    assert scoped["normalized_payload_json"]["class_id"] == other.id


def test_unknown_student_no_is_rejected_at_preview(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    response = client.post("/api/v1/write-proposals", json={
        "operation_type": "classroom.broadcast.send",
        "payload": {"class_id": cls.id, "mode": "three_part", "student_nos": ["99"], "predicate": "去扫地"}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STUDENT_NOT_FOUND"


def test_custom_sentence_keeps_the_original_wording(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    text = "请今天负责卫生的同学现在带好工具到教室后门集合。"
    proposal = _propose(client, "classroom.broadcast.send", {"class_id": cls.id, "mode": "custom", "text": text})
    assert proposal["preview_json"]["summary"]["texts"] == [text]
    with online_terminal(db, paired["device_id"], cls.id):
        confirmed = _confirm(client, proposal)
    payload = db.get(ClassroomDeviceCommand, confirmed.json()["data"]["result_json"]["command_id"]).payload_json
    # 终端收到的就是同一段冻结文本，没有被追加“请”“同学”或改写。
    assert [segment["text"] for segment in payload["segments"]] == [text]


def test_volume_proposal_previews_and_registers_a_command(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    proposal = _propose(client, "classroom.volume.set", {"class_id": cls.id, "volume": 40})
    assert proposal["preview_json"]["title"] == "调整教室音量"
    assert proposal["preview_json"]["summary"]["volume"] == 40
    with online_terminal(db, paired["device_id"], cls.id):
        confirmed = _confirm(client, proposal)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["data"]["result_json"]
    assert result["requested_volume"] == 40
    assert result["delivery"]["pushed"] is True
    command = db.get(ClassroomDeviceCommand, result["command_id"])
    assert command.kind == "volume.set" and command.source_type == "agent"
    assert command.payload_json == {"volume": 40, "mute": None, "restore_after_broadcast": False}


def test_batch_confirm_dispatches_every_terminal_command(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    first = _propose(client, "classroom.broadcast.send",
                     {"class_id": cls.id, "mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"})
    second = _propose(client, "classroom.volume.set", {"class_id": cls.id, "volume": 25})
    with online_terminal(db, paired["device_id"], cls.id):
        response = client.post("/api/v1/write-proposals/confirm-batch", json={
            "items": [{"proposal_id": first["id"], "revision": first["revision"]},
                      {"proposal_id": second["id"], "revision": second["revision"]}],
            "confirmed_by": "李老师"})
    assert response.status_code == 200, response.text
    rows = {row.id: row for row in db.scalars(select(WriteProposal))}
    assert rows[first["id"]].status == "completed" and rows[second["id"]].status == "completed"
    assert rows[first["id"]].result_json["delivery"]["pushed"] is True
    assert rows[second["id"]].result_json["delivery"]["pushed"] is True
    assert len(list(db.scalars(select(ClassroomDeviceCommand)))) == 2
