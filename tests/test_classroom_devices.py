"""教室终端配对、凭据、心跳与命令账本。"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select

from app.models.entities import (
    ClassAgentBinding,
    ClassroomBroadcast,
    ClassroomCamera,
    ClassroomDevice,
    ClassroomDeviceCommand,
    ClassroomMediaSession,
)
from app.schemas.classroom import DeviceHeartbeat
from app.services import classroom_channel, classroom_devices
from app.utils.time import now
from tests.helpers import TERMINAL_CAPABILITIES, TERMINAL_INVENTORY, online_terminal, pair_terminal, report, teacher_for_class


def _pair_payload(code: str, **overrides):
    return {"pairing_code": code, "protocol_version": 1, "app_version": "1.0.0",
            "os_version": "Windows 10 教育版", "device_name": "教室终端",
            "capabilities": TERMINAL_CAPABILITIES, "inventory": TERMINAL_INVENTORY, **overrides}


def test_pairing_code_is_one_time_and_hashed(client, db, sample):
    cls, _, _ = sample
    issued = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={"name": "教室终端"}).json()["data"]
    code = issued["pairing_code"]
    assert issued["pairing_status"] == "unpaired" and issued["pairing_expires_at"]
    assert len(code) == 9 and code[4] == "-"
    # 配对码只以哈希入库，明文只在生成时返回一次。
    device = db.get(ClassroomDevice, issued["device_id"])
    assert device.pairing_code_hash and device.pairing_code_hash != code

    paired = client.post("/api/v1/classroom/device/pair", json=_pair_payload(code))
    assert paired.status_code == 201
    credential = paired.json()["data"]["credential"]
    assert credential and paired.json()["data"]["class_id"] == cls.id

    reused = client.post("/api/v1/classroom/device/pair", json=_pair_payload(code))
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "DEVICE_PAIRING_INVALID"

    db.refresh(device)
    assert device.pairing_status == "paired" and device.pairing_code_hash is None
    assert device.credential_hash and device.credential_hash != credential
    status = client.get(f"/api/v1/classes/{cls.id}/classroom/status").json()["data"]
    assert credential not in json.dumps(status)
    assert device.credential_hash not in json.dumps(status)


def test_pairing_rejects_bad_code_expiry_and_brute_force(client, db, sample):
    cls, _, _ = sample
    issued = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={}).json()["data"]
    wrong = client.post("/api/v1/classroom/device/pair", json=_pair_payload("ZZZZ-ZZZZ"))
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "DEVICE_PAIRING_INVALID"
    malformed = client.post("/api/v1/classroom/device/pair", json=_pair_payload("AB-CD"))
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "DEVICE_PAIRING_INVALID"
    # 长度不足由 Pydantic 在路由层拦下，不会进入配对逻辑。
    assert client.post("/api/v1/classroom/device/pair", json=_pair_payload("abc")).status_code == 422

    device = db.get(ClassroomDevice, issued["device_id"])
    device.pairing_expires_at = now() - timedelta(seconds=1)
    db.commit()
    expired = client.post("/api/v1/classroom/device/pair", json=_pair_payload(issued["pairing_code"]))
    assert expired.status_code == 400 and expired.json()["error"]["code"] == "DEVICE_PAIRING_EXPIRED"
    db.refresh(device)
    assert device.pairing_code_hash is None

    for _ in range(classroom_devices.PAIR_MAX_ATTEMPTS + 1):
        throttled = client.post("/api/v1/classroom/device/pair", json=_pair_payload("AAAA-BBBB"))
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "DEVICE_PAIRING_THROTTLED"


def test_pairing_refuses_second_terminal_and_mismatched_protocol(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    assert paired["protocol_version"] == 1
    again = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "DEVICE_ALREADY_PAIRED"

    cancelled = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={})
    assert cancelled.status_code == 409
    revoked = client.post(f"/api/v1/classes/{cls.id}/classroom/device/revoke")
    assert revoked.status_code == 200 and revoked.json()["data"]["pairing_status"] == "revoked"
    reissue = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={"name": "新教室电脑"})
    assert reissue.status_code == 201
    bad_protocol = client.post("/api/v1/classroom/device/pair",
                              json=_pair_payload(reissue.json()["data"]["pairing_code"], protocol_version=2))
    assert bad_protocol.status_code == 409
    assert bad_protocol.json()["error"]["code"] == "DEVICE_PROTOCOL_UNSUPPORTED"


def test_revoked_credential_cannot_reconnect(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    device = db.get(ClassroomDevice, paired["device_id"])
    assert classroom_devices.authenticate(db, device.id, paired["credential"]).id == device.id

    client.post(f"/api/v1/classes/{cls.id}/classroom/device/revoke")
    db.expire_all()
    try:
        classroom_devices.authenticate(db, device.id, paired["credential"])
    except Exception as exc:  # noqa: BLE001 - 断言错误码
        assert exc.code == "DEVICE_UNAUTHORIZED"
    else:
        raise AssertionError("撤销后的凭据仍可鉴权")
    assert client.get(f"/api/v1/classes/{cls.id}/classroom/status").json()["data"]["device"]["online"] is False


def test_offline_terminal_rejects_realtime_commands(client, db, sample):
    cls, _, students = sample
    pair_terminal(client, cls)
    status = client.get(f"/api/v1/classes/{cls.id}/classroom/status").json()["data"]
    assert status["device"]["online"] is False
    assert "离线" in status["note"] or "尚未配对" in status["note"]

    body = {"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"}
    sent = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts", json=body)
    assert sent.status_code == 409 and sent.json()["error"]["code"] == "DEVICE_OFFLINE"
    volume = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 40})
    assert volume.status_code == 409 and volume.json()["error"]["code"] == "DEVICE_OFFLINE"
    # 离线时不登记命令，也不积压补播。
    assert db.scalar(select(ClassroomDeviceCommand)) is None


def test_command_ledger_tracks_delivery_execution_and_receipts(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    device_id = paired["device_id"]
    with online_terminal(db, device_id, cls.id):
        sent = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                          json={"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"})
        assert sent.status_code == 201
        data = sent.json()["data"]
        command_id = data["command"]["command_id"]
        # 登记成功不等于已显示或已播报。
        assert data["command"]["status"] == "authorized"
        assert data["display_status"] == "authorized" and data["speak_status"] == "authorized"
        assert "尚未收到" in data["status_note"]

        assert report(db, device_id, command_id, "started")["status"] == "executing"
        db.expire_all()
        assert db.get(ClassroomDeviceCommand, command_id).status == "executing"

        done = report(db, device_id, command_id, "succeeded", display="shown", speak="spoken")
        assert done["status"] == "succeeded" and done["duplicate"] is False
        # 重复回执不改写已终结的结果。
        again = report(db, device_id, command_id, "failed", display="failed", speak="failed")
        assert again["status"] == "succeeded" and again["duplicate"] is True

        detail = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{data['broadcast_id']}").json()["data"]
        assert detail["display_status"] == "succeeded" and detail["speak_status"] == "succeeded"
        assert detail["status_note"] == "教室屏幕已显示，扬声器已播报"
        assert detail["command"]["result"] == {"display": "shown", "speak": "spoken", "state": "succeeded"}


def test_display_and_speak_results_are_reported_separately(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                          json={"mode": "three_part", "student_ids": [students[1].id], "predicate": "来老师办公室"}).json()["data"]
        report(db, paired["device_id"], data["command"]["command_id"], "failed",
               display="shown", speak="unavailable", error_code="NO_CHINESE_TTS")
        detail = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{data['broadcast_id']}").json()["data"]
        assert detail["display_status"] == "succeeded"
        assert detail["speak_status"] == "failed"
        assert "屏幕已显示" in detail["status_note"]
        assert detail["command"]["error_code"] == "NO_CHINESE_TTS"


def test_expired_commands_are_swept_and_never_replayed(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                          json={"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"}).json()["data"]
        command = db.get(ClassroomDeviceCommand, data["command"]["command_id"])
        command.status = "executing"
        command.expires_at = now() - timedelta(seconds=1)
        db.commit()

        assert classroom_devices.expire_overdue(db) == 1
        db.refresh(command)
        # 已开始执行却没有回执：结果未知，不能当成失败后自动重播。
        assert command.status == "unknown" and command.error_code == "COMMAND_RESULT_UNKNOWN"
        detail = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{data['broadcast_id']}").json()["data"]
        assert detail["speak_status"] == "unknown" and "结果未知" in detail["status_note"]

        undelivered = command
        undelivered.status = "authorized"
        undelivered.expires_at = now() - timedelta(seconds=1)
        db.commit()
        classroom_devices.expire_overdue(db)
        db.refresh(undelivered)
        assert undelivered.status == "expired" and undelivered.error_code == "COMMAND_EXPIRED"


def test_heartbeat_updates_reported_state_and_volume(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    device = db.get(ClassroomDevice, paired["device_id"])
    classroom_devices.heartbeat(db, device, DeviceHeartbeat(
        volume_level=35, muted=False, audio_output_name="教室音箱",
        inventory={"cameras": [{"name": "换了个摄像头", "identifier": "usb-cam-2"}], "microphones": [], "speakers": []}))
    status = client.get(f"/api/v1/classes/{cls.id}/classroom/status").json()["data"]["device"]
    assert status["volume_level"] == 35 and status["muted"] is False
    assert status["audio_output_name"] == "教室音箱"
    assert status["inventory"]["cameras"][0]["identifier"] == "usb-cam-2"
    assert status["capabilities"] == TERMINAL_CAPABILITIES


def test_output_configuration_must_come_from_reported_inventory(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        rejected = client.post(f"/api/v1/classes/{cls.id}/classroom/device/output",
                              json={"display_name": "教师笔记本副屏"})
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "DEVICE_OUTPUT_UNAVAILABLE"

        accepted = client.post(f"/api/v1/classes/{cls.id}/classroom/device/output",
                              json={"display_name": "外接投影", "audio_output_name": "教室音箱"})
        assert accepted.status_code == 200
        command = accepted.json()["data"]["command"]
        assert command["kind"] == "device.configure"
        assert db.get(ClassroomDeviceCommand, command["command_id"]).payload_json == {
            "display_name": "外接投影", "audio_output_name": "教室音箱"}


def test_volume_is_clamped_by_configured_ceiling(client, db, sample, monkeypatch):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 101}).status_code == 422

        capped = replace(classroom_devices.settings,
                         classroom=classroom_devices.settings.classroom.model_copy(update={"volume_ceiling": 60}))
        monkeypatch.setattr(classroom_devices, "settings", capped)
        over = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 80})
        assert over.status_code == 422 and over.json()["error"]["code"] == "VOLUME_ABOVE_CEILING"

        accepted = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 40})
        assert accepted.status_code == 200
        payload = accepted.json()["data"]
        assert payload["requested_volume"] == 40
        # 服务器不假装已经生效：设备实际值只能来自终端回执。
        assert payload["reported_volume"] is None
        assert "终端回执" in payload["note"]
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={}).status_code == 422


def test_volume_requires_reported_volume_capability(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls, capabilities={**TERMINAL_CAPABILITIES, "volume_control": False})
    with online_terminal(db, paired["device_id"], cls.id):
        response = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 40})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "VOLUME_UNSUPPORTED"


def test_head_teacher_cannot_reach_another_class_terminal(client, db, sample):
    cls, other, _ = sample
    pair_terminal(client, cls)
    headers = teacher_for_class(client, other, db, "teacher-other")
    forbidden = client.get(f"/api/v1/classes/{cls.id}/classroom/status", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "CLASS_ACCESS_DENIED"
    assert client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={}, headers=headers).status_code == 403
    own = client.get(f"/api/v1/classes/{other.id}/classroom/status", headers=headers)
    assert own.status_code == 200 and own.json()["data"]["device"] is None


def test_unbind_keeps_class_agent_and_other_channels(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    db.add(ClassAgentBinding(class_id=cls.id, agent_name="班级助手", workspace_path="/tmp/class-agent",
                            openclaw_agent_id="class-agent", status="active"))
    db.commit()
    result = client.delete(f"/api/v1/classes/{cls.id}/classroom/device")
    assert result.status_code == 200 and result.json()["data"]["unbound"] is True
    assert db.scalar(select(ClassroomDevice)) is None
    assert db.scalar(select(ClassAgentBinding)) is not None


def test_class_deletion_revokes_terminal_and_removes_classroom_records(client, db, sample, deletion_gateway):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    camera = {"name": "教室摄像头", "access_path": "windows_capture", "source_kind": "windows_device",
              "device_identifier": "usb-cam-1", "microphone_identifier": "mic-1", "audio_capable": True}
    with online_terminal(db, paired["device_id"], cls.id):
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/camera", json=camera).status_code == 201
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={}).status_code == 201
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                           json={"mode": "three_part", "student_ids": [students[0].id],
                                 "predicate": "去扫地"}).status_code == 201
        deleted = client.delete(f"/api/v1/classes/{cls.id}")
    assert deleted.status_code == 200, deleted.text
    for model in (ClassroomDevice, ClassroomCamera, ClassroomBroadcast, ClassroomDeviceCommand, ClassroomMediaSession):
        assert db.scalar(select(model)) is None, model.__tablename__
    # 控制连接已关闭，不会留下指向已删除班级的在线终端。
    assert classroom_channel.online_device_ids() == set()
    counts = deleted.json()["data"].get("deleted_counts", {})
    assert counts.get("classroom_devices") == 1 and counts.get("classroom_broadcasts") == 1
