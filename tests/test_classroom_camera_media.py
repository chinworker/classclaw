"""摄像头登记与更换、观看租约与音轨授权。"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from app.models.entities import ClassroomCamera, ClassroomDeviceCommand, ClassroomMediaSession
from app.services import classroom_media
from app.utils.time import now
from tests.helpers import online_terminal, pair_terminal, teacher_for_class

WINDOWS_CAMERA = {"name": "教室摄像头", "access_path": "windows_capture", "source_kind": "windows_device",
                  "device_identifier": "usb-cam-1", "device_label": "USB 摄像头", "audio_capable": True,
                  "microphone_identifier": "mic-1"}
NETWORK_CAMERA = {"name": "网络摄像头", "access_path": "server_direct", "source_kind": "network_stream",
                  "protocol": "rtsp", "location": "rtsp://10.20.30.40:554/stream1", "audio_capable": True}


def _connect(client, class_id, body, expected_revision: int | None = None):
    """POST 从请求体读 expected_revision；DELETE 才用查询参数。"""
    payload = body if expected_revision is None else {**body, "expected_revision": expected_revision}
    return client.post(f"/api/v1/classes/{class_id}/classroom/camera", json=payload)


def test_windows_camera_requires_paired_terminal(client, db, sample):
    cls, _, _ = sample
    denied = _connect(client, cls.id, WINDOWS_CAMERA)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "DEVICE_NOT_PAIRED"

    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        created = _connect(client, cls.id, WINDOWS_CAMERA)
    assert created.status_code == 201
    data = created.json()["data"]
    assert data["status"] == "registered" and data["config_revision"] == 1
    assert data["replaced"] is False and data["capture_requested"] is True and data["capture_started"] is False
    # 采集指令已经登记，等待终端回报连通状态。
    kinds = [row.kind for row in db.scalars(select(ClassroomDeviceCommand))]
    assert kinds == ["camera.start"]


def test_one_camera_per_class_and_explicit_replacement(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        assert _connect(client, cls.id, WINDOWS_CAMERA).status_code == 201
        second = _connect(client, cls.id, NETWORK_CAMERA)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CAMERA_ALREADY_CONNECTED"
        # 更换必须显示替换对象。
        assert second.json()["error"]["details"]["current"]["device_label"] == "USB 摄像头"

        stale = _connect(client, cls.id, NETWORK_CAMERA, expected_revision=99)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "CAMERA_REVISION_CONFLICT"

        replaced = _connect(client, cls.id, NETWORK_CAMERA, expected_revision=1)
        assert replaced.status_code == 201
        assert replaced.json()["data"]["config_revision"] == 2
        assert replaced.json()["data"]["replaced"] is True
        assert db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == cls.id)).config_revision == 2
        assert len(list(db.scalars(select(ClassroomCamera)))) == 1


def test_replacement_stops_old_connection_before_starting_new(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        replaced = _connect(client, cls.id, {**WINDOWS_CAMERA, "device_identifier": "usb-cam-2",
                                             "device_label": "外接摄像头"}, expected_revision=1)
    assert replaced.json()["data"]["stop_requested"] is True
    assert replaced.json()["data"]["old_connection_stopped"] is False
    kinds = [row.kind for row in db.scalars(select(ClassroomDeviceCommand))]
    # 先停旧连接再启新连接；顺序颠倒会导致两路并行或没有画面。
    assert kinds == ["camera.start", "camera.stop", "camera.start"]


def test_replacement_revokes_existing_viewers(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        issued = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={"audio": True})
        assert issued.status_code == 201
        session_id = issued.json()["data"]["session_id"]
        replaced = _connect(client, cls.id, NETWORK_CAMERA, expected_revision=1)
    assert replaced.json()["data"]["revoked_media_sessions"] == 1
    db.expire_all()
    row = db.get(ClassroomMediaSession, session_id)
    assert row.status == "revoked" and row.revoke_reason == "camera_replaced"


def test_network_camera_rejects_embedded_credentials_and_loopback(client, db, sample):
    cls, _, _ = sample
    pair_terminal(client, cls)
    with_secret = _connect(client, cls.id, {**NETWORK_CAMERA, "location": "rtsp://admin:pass@10.20.30.40:554/s1"})
    assert with_secret.status_code == 422
    assert with_secret.json()["error"]["code"] == "CAMERA_CREDENTIAL_IN_URL"

    loopback = _connect(client, cls.id, {**NETWORK_CAMERA, "access_path": "server_direct",
                                         "protocol": "http", "location": "http://127.0.0.1:8080/stream"})
    assert loopback.status_code == 422
    assert loopback.json()["error"]["code"] == "CAMERA_LOCATION_FORBIDDEN"

    mismatch = _connect(client, cls.id, {**NETWORK_CAMERA, "location": "http://10.20.30.40/stream"})
    assert mismatch.status_code == 422

    windows_path = _connect(client, cls.id, {**WINDOWS_CAMERA, "access_path": "server_direct"})
    assert windows_path.status_code == 422
    assert _connect(client, cls.id, {**NETWORK_CAMERA, "access_path": "windows_capture"}).status_code == 422


def test_camera_secret_stays_server_side(client, db, sample, monkeypatch):
    cls, _, _ = sample
    pair_terminal(client, cls)
    missing = _connect(client, cls.id, {**NETWORK_CAMERA, "credential_ref": "lab_one"})
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "CAMERA_CREDENTIAL_MISSING"

    monkeypatch.setenv("CLASSCLAW_CLASSROOM_CAMERA_LAB_ONE", "secret-camera-password")
    created = _connect(client, cls.id, {**NETWORK_CAMERA, "credential_ref": "lab_one"})
    assert created.status_code == 201
    body = created.text
    assert "secret-camera-password" not in body
    assert "lab_one" not in body
    assert created.json()["data"]["credential_configured"] is True
    assert db.scalar(select(ClassroomCamera)).credential_ref == "lab_one"
    assert "secret-camera-password" not in json.dumps(client.get(f"/api/v1/classes/{cls.id}/classroom/status").json())


def test_disconnect_keeps_broadcast_and_volume_usable(client, db, sample):
    cls, _, students = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        disconnected = client.delete(f"/api/v1/classes/{cls.id}/classroom/camera?expected_revision=1")
        assert disconnected.status_code == 200
        assert disconnected.json()["data"]["stop_requested"] is True
        assert disconnected.json()["data"]["old_connection_stopped"] is False
        assert db.scalar(select(ClassroomCamera)) is None

        conflict = client.delete(f"/api/v1/classes/{cls.id}/classroom/camera?expected_revision=7")
        assert conflict.status_code == 404
        volume = client.post(f"/api/v1/classes/{cls.id}/classroom/volume", json={"volume": 30})
        assert volume.status_code == 200
        broadcast = client.post(f"/api/v1/classes/{cls.id}/classroom/broadcasts",
                                json={"mode": "three_part", "student_ids": [students[0].id], "predicate": "去扫地"})
        assert broadcast.status_code == 201


def test_media_lease_is_short_lived_and_token_is_shown_once(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        issued = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={"audio": False})
    assert issued.status_code == 201
    data = issued.json()["data"]
    token = data["token"]
    assert data["video_allowed"] is True and data["audio_allowed"] is False
    assert data["available"] is False and data["reason"] == "MEDIA_NOT_CONFIGURED"
    assert "尚未接入媒体转发" in data["message"]

    row = db.get(ClassroomMediaSession, data["session_id"])
    assert row.token_hash != token and token not in json.dumps(data | {"token": ""})
    assert classroom_media.authenticate(db, token).id == row.id
    try:
        classroom_media.authenticate(db, "wrong-token")
    except Exception as exc:  # noqa: BLE001 - 断言错误码
        assert exc.code == "MEDIA_SESSION_INVALID"
    else:
        raise AssertionError("伪造令牌通过了鉴权")

    renewed = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions/{row.id}/renew")
    assert renewed.status_code == 200
    listed = client.get(f"/api/v1/classes/{cls.id}/classroom/media-sessions").json()["data"]
    assert listed["total"] == 1 and listed["streaming"]["viewers"] == 1
    assert listed["streaming"]["keep_stream_up"] is True

    released = client.delete(f"/api/v1/classes/{cls.id}/classroom/media-sessions/{row.id}")
    assert released.status_code == 200
    assert released.json()["data"]["status"] == "released"
    assert released.json()["data"]["keep_stream_up"] is True
    assert released.json()["data"]["grace_remaining_seconds"] is not None
    assert client.get(f"/api/v1/classes/{cls.id}/classroom/media-sessions").json()["data"]["total"] == 0


def test_audio_track_requires_a_real_audio_source(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, {**WINDOWS_CAMERA, "audio_capable": False})
        issued = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={"audio": True}).json()["data"]
    # 摄像头没有音轨时不能凭空获取现场声音，也不能只靠浏览器静音。
    assert issued["audio_allowed"] is False
    assert "没有可用音轨" in issued["audio_unavailable_reason"]
    assert classroom_media.authenticate(db, issued["token"]).audio_allowed is False


def test_expired_lease_is_refused_and_grace_ends_forwarding(client, db, sample):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        issued = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={"audio": False}).json()["data"]
    session = db.get(ClassroomMediaSession, issued["session_id"])
    session.expires_at = now() - timedelta(seconds=1)
    db.commit()
    try:
        classroom_media.authenticate(db, issued["token"])
    except Exception as exc:  # noqa: BLE001 - 断言错误码
        assert exc.code == "MEDIA_SESSION_EXPIRED"
    else:
        raise AssertionError("过期租约仍可观看")
    db.refresh(session)
    assert session.status == "expired"

    camera = db.scalar(select(ClassroomCamera))
    session.released_at = now() - timedelta(seconds=9999)
    db.commit()
    state = classroom_media.streaming_state(db, camera.id)
    assert state["viewers"] == 0 and state["keep_stream_up"] is False


def test_logout_revokes_own_viewing_sessions(client, db, sample):
    cls, _, _ = sample
    headers = teacher_for_class(client, cls, db, "teacher-media")
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        issued = client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={}, headers=headers)
    assert issued.status_code == 201
    session_id = issued.json()["data"]["session_id"]
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    db.expire_all()
    row = db.get(ClassroomMediaSession, session_id)
    assert row.status == "revoked" and row.revoke_reason == "logout"


def test_teacher_cannot_watch_another_class_camera(client, db, sample):
    cls, other, _ = sample
    pair_terminal(client, cls)
    headers = teacher_for_class(client, other, db, "teacher-other")
    assert client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={}, headers=headers).status_code == 403
    assert client.get(f"/api/v1/classes/{cls.id}/classroom/camera", headers=headers).status_code == 403
    assert client.post(f"/api/v1/classes/{cls.id}/classroom/camera", json=WINDOWS_CAMERA,
                       headers=headers).status_code == 403
