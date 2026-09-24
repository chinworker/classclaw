"""终端 WebSocket 控制通道：鉴权、心跳、命令投递与回执。"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.models.entities import ClassroomBroadcast, ClassroomDevice, ClassroomDeviceCommand
from tests.helpers import TERMINAL_CAPABILITIES, TERMINAL_INVENTORY, online_terminal, pair_terminal

CHANNEL = "/api/v1/classroom/device-channel"


@pytest.fixture()
def channel_db(client, db, monkeypatch):
    """控制通道在独立线程里开短会话；测试用同一引擎上的独立 Session 替换。"""
    from app.api.v1 import classroom_terminal

    @contextmanager
    def scope():
        session = Session(bind=db.get_bind(), expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(classroom_terminal, "writer_session", scope)
    return db


def _hello(device_id: str, credential: str, **overrides) -> dict:
    return {"type": "hello", "device_id": device_id, "credential": credential, "protocol_version": 1,
            "app_version": "1.0.0", "os_version": "Windows 10 教育版", "capabilities": TERMINAL_CAPABILITIES,
            "inventory": TERMINAL_INVENTORY, "display_name": "教室一体机", "audio_output_name": "教室音箱",
            "volume_level": 30, **overrides}


def test_channel_delivers_frozen_broadcast_and_records_receipts(client, db, sample, channel_db):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome" and welcome["class_id"] == cls.id
        assert welcome["protocol_version"] == 1 and welcome["command_ttl_seconds"] > 0

        ws.send_json({"type": "heartbeat", "volume_level": 55, "muted": False})
        assert ws.receive_json()["type"] == "heartbeat_ack"
        db.expire_all()
        device = db.get(ClassroomDevice, paired["device_id"])
        assert device.volume_level == 55 and device.last_seen_at is not None

        sent = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                           json={"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"})
        assert sent.status_code == 201, sent.text
        command = ws.receive_json()
        assert command["type"] == "command" and command["kind"] == "broadcast.show"
        assert command["command_id"] == sent.json()["data"]["command"]["command_id"]
        assert [row["text"] for row in command["payload"]["segments"]] == ["请张三同学现在去扫地。"]
        # 终端只收到学号与姓名，不收到学生 UUID。
        assert students[0].id not in json.dumps(command)

        ws.send_json({"type": "command_result", "command_id": command["command_id"], "state": "started"})
        assert ws.receive_json()["status"] == "executing"
        db.expire_all()
        # 通道按顺序处理消息，收到回执就说明送达状态已经落库。
        assert db.get(ClassroomDeviceCommand, command["command_id"]).status == "executing"
        assert db.get(ClassroomDeviceCommand, command["command_id"]).delivered_at is not None

        ws.send_json({"type": "command_result", "command_id": command["command_id"], "state": "succeeded",
                      "display": "shown", "speak": "spoken"})
        assert ws.receive_json()["status"] == "succeeded"
        db.expire_all()
        broadcast = db.get(ClassroomBroadcast, sent.json()["data"]["broadcast_id"])
        assert broadcast.display_status == "succeeded" and broadcast.speak_status == "succeeded"


def test_command_authorized_before_a_reconnect_is_not_replayed(client, db, sample, channel_db):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    # 用内存通道登记命令：连接在推送前断开，命令保持 authorized 且未过期。
    with online_terminal(db, paired["device_id"], cls.id):
        sent = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                           json={"mode": "custom", "text": "请现在保持安静"})
    assert sent.status_code == 201
    command_id = sent.json()["data"]["command"]["command_id"]
    db.expire_all()
    assert db.get(ClassroomDeviceCommand, command_id).status == "authorized"

    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"
        db.expire_all()
        assert db.get(ClassroomDeviceCommand, command_id).status == "expired"


def test_channel_rejects_bad_credential_and_unknown_message(client, db, sample, channel_db):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    # 服务端先发一帧错误说明原因，再关闭连接。
    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], "wrong-credential"))
        error = ws.receive_json()
        assert error == {"type": "error", "code": "DEVICE_UNAUTHORIZED", "message": "终端凭据无效或已撤销"}
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
    assert denied.value.code == 4401

    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["code"] == "PROTOCOL_ERROR"
        with pytest.raises(WebSocketDisconnect) as protocol:
            ws.receive_json()
    assert protocol.value.code == 4400

    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"type": "run_shell", "command": "shutdown"})
        error = ws.receive_json()
        assert error["type"] == "error" and error["code"] == "PROTOCOL_ERROR"
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"


def test_revoking_the_credential_drops_the_live_connection(client, db, sample, channel_db):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        assert ws.receive_json()["type"] == "welcome"
        assert client.post(f"/api/v1/classes/{cls.id}/classroom/device/revoke").status_code == 200
        # 终端能区分“被撤销”和“网络断开”，不会拿着失效凭据反复重连。
        assert ws.receive_json()["code"] == "DEVICE_REVOKED"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401
    db.expire_all()
    device = db.get(ClassroomDevice, paired["device_id"])
    assert device.pairing_status == "revoked" and device.credential_hash is None
    # 既有凭据立即失效，不能重连。
    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        assert ws.receive_json()["code"] == "DEVICE_UNAUTHORIZED"


def test_pending_commands_settle_when_the_terminal_disappears(client, db, sample, channel_db):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        sent = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 45})
    assert sent.status_code == 200
    command_id = sent.json()["data"]["command_id"]

    with client.websocket_connect(CHANNEL) as ws:
        ws.send_json(_hello(paired["device_id"], paired["credential"]))
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat_ack"

    # 连接结束后账本必须给出真实状态，而不是永远停在已登记。
    assert client.get(f"/api/v1/classes/{cls.id}/classroom/status").status_code == 200
    db.expire_all()
    command = db.get(ClassroomDeviceCommand, command_id)
    assert command.status == "expired"
    assert command.error_code in {"DEVICE_DISCONNECTED", "COMMAND_EXPIRED"}
