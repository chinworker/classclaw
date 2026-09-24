"""真实操作顺序与协议边界回归，避免只检查登记成功。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.entities import ClassroomCamera, ClassroomDevice, ClassroomDeviceCommand, ClassroomMediaSession
from app.schemas.classroom import DeviceHeartbeat
from app.services import classroom_devices, classroom_media
from app.utils.time import now
from tests.helpers import online_terminal, pair_terminal, report
from tests.test_classroom_camera_media import NETWORK_CAMERA, WINDOWS_CAMERA, _connect


def test_broadcast_control_targets_original_command_and_keeps_its_results(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        base = f"/api/v1/classes/{cls.id}/classroom/broadcasts"
        first = client.post(base, json={"mode": "custom", "text": "请保持安静"}).json()["data"]
        original_id = first["command"]["command_id"]
        report(db, paired["device_id"], original_id, "succeeded", display="shown", speak="spoken")
        client.post(base, json={"mode": "custom", "text": "请第二组准备"})
        stopped = client.post(f"{base}/{first['broadcast_id']}/stop").json()["data"]["command"]
        command = db.get(ClassroomDeviceCommand, stopped["command_id"])
        assert command.payload_json == {"target_command_id": original_id}
        report(db, paired["device_id"], command.id, "succeeded", speak="skipped")
        detail = client.get(f"{base}/{first['broadcast_id']}").json()["data"]
        assert detail["display_status"] == detail["speak_status"] == "succeeded"
        assert detail["command"]["command_id"] == original_id


def test_detail_poll_alone_expires_command_and_late_success_cannot_overwrite(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        data = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                           json={"mode": "custom", "text": "请保持安静"}).json()["data"]
        command = db.get(ClassroomDeviceCommand, data["command"]["command_id"])
        command.expires_at = now() - timedelta(seconds=1)
        db.commit()
        assert classroom_devices.pending_commands(db, paired["device_id"]) == []
        detail = client.get(f"/api/v1/classes/{cls.id}/classroom/broadcasts/{data['broadcast_id']}" ).json()["data"]
        assert detail["speak_status"] == "expired"
        ack = report(db, paired["device_id"], command.id, "succeeded", display="shown", speak="spoken")
        assert ack["status"] == "expired" and ack["duplicate"]


def test_missing_channel_outcomes_do_not_poll_forever(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        base = f"/api/v1/classes/{cls.id}/classroom/broadcasts"
        data = client.post(base, json={"mode": "custom", "text": "请保持安静"}).json()["data"]
        report(db, paired["device_id"], data["command"]["command_id"], "succeeded")
        detail = client.get(f"{base}/{data['broadcast_id']}").json()["data"]
        assert detail["display_status"] == detail["speak_status"] == "unknown"


def test_empty_salutation_is_preserved_and_changed_preview_is_rejected(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    base = f"/api/v1/classes/{cls.id}/classroom/broadcasts"
    payload = {"student_ids": [students[0].id], "salutation": "", "predicate": "去扫地"}
    preview = client.post(f"{base}/preview", json=payload).json()["data"]
    assert preview["texts"] == ["请张三现在去扫地。"]
    students[0].name = "更正姓名"
    db.commit()
    with online_terminal(db, paired["device_id"], cls.id):
        sent = client.post(base, json={**payload, "expected_texts": preview["texts"]})
    assert sent.status_code == 409 and sent.json()["error"]["code"] == "BROADCAST_PREVIEW_STALE"
    assert db.scalar(select(ClassroomDeviceCommand)) is None


def test_empty_output_inventory_rejects_arbitrary_targets(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    device = db.get(ClassroomDevice, paired["device_id"])
    device.capabilities_json = {"displays": []}
    device.inventory_json = {"speakers": []}
    db.commit()
    with online_terminal(db, paired["device_id"], cls.id):
        result = client.post(f"/api/v1/classes/{cls.id}/classroom/device/output", json={"display_name": "任意设备"})
        assert result.status_code == 422
        result = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                             json={"mode": "custom", "text": "请保持安静", "target_screen": "任意设备"})
        assert result.status_code == 422


@pytest.mark.parametrize("location", [
    "http://127.2.3.4/", "http://127.1/", "http://2130706433/", "http://0x7f000001/",
    "http://[::ffff:127.0.0.1]/", "http://localhost./", "http://169.254.170.2/",
])
def test_camera_rejects_alternate_local_and_metadata_addresses(client, sample, location):
    cls, _, _ = sample
    result = _connect(client, cls.id, {**NETWORK_CAMERA, "protocol": "http", "location": location})
    assert result.status_code == 422 and result.json()["error"]["code"] == "CAMERA_LOCATION_FORBIDDEN"


def test_camera_ignores_old_revision_and_tracks_disconnect(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        first = _connect(client, cls.id, WINDOWS_CAMERA).json()["data"]
        _connect(client, cls.id, WINDOWS_CAMERA, expected_revision=1)
        device = db.get(ClassroomDevice, paired["device_id"])
        state = {"camera_id": first["camera_id"], "config_revision": 1, "identifier": "usb-cam-1", "state": "connected"}
        classroom_devices.heartbeat(db, device, DeviceHeartbeat(camera_state=state))
        camera = db.get(ClassroomCamera, first["camera_id"])
        assert camera.status == "registered"
        state["config_revision"] = 2
        classroom_devices.heartbeat(db, device, DeviceHeartbeat(camera_state=state))
        assert camera.status == "connected"
        classroom_devices.mark_disconnected(db, device)
        assert camera.status == "failed"


def test_revoke_persists_lease_release_timestamp(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    _connect(client, cls.id, WINDOWS_CAMERA)
    lease = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={}).json()["data"]
    client.post(f"/api/v1/classes/{cls.id}/classroom/device/revoke")
    db.expire_all()
    row = db.get(ClassroomMediaSession, lease["session_id"])
    assert row.status == "revoked" and row.released_at is not None


def test_read_side_expiry_uses_writer_session(client, db, sample, monkeypatch):
    cls, _, _ = sample
    pair_terminal(client, cls)
    used = []

    @contextmanager
    def writer():
        used.append(True)
        db.info.pop("read_only", None)
        try:
            yield db
        finally:
            db.info["read_only"] = True

    monkeypatch.setattr(classroom_devices, "writer_session", writer)
    monkeypatch.setattr(classroom_media, "writer_session", writer)
    db.info["read_only"] = True
    try:
        classroom_devices.expire_overdue(db)
        classroom_media.expire_overdue(db)
        assert len(used) == 2
    finally:
        db.info.pop("read_only", None)
