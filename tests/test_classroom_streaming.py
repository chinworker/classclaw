from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from app.config import settings
from app.models.entities import ClassroomDevice, ClassroomMediaSession
from app.services import classroom_devices, classroom_media
from app.services import classroom_streaming as media
from tests.helpers import online_terminal, pair_terminal
from tests.test_classroom_camera_media import WINDOWS_CAMERA, _connect

VIDEO_SDP = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"
AUDIO_SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n"


@pytest.fixture
def media_ready(monkeypatch):
    configured = replace(settings, classroom=settings.classroom.model_copy(update={
        "media_provider": "mediamtx", "media_base_url": "http://127.0.0.1:8889"}))
    for module in (media, classroom_devices, classroom_media):
        monkeypatch.setattr(module, "settings", configured)
    monkeypatch.setattr(media, "_ready", True)
    monkeypatch.setattr(media, "_peers", {})
    monkeypatch.setattr(media, "_locks", {})
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return httpx.Response(201, text="v=0\r\n", headers={"Location": url + "/resource"})
        return httpx.Response(204)

    monkeypatch.setattr(media, "request", request)
    return calls


def test_video_only_viewer_cannot_negotiate_audio_or_mixed_tracks(client, db, sample, media_ready):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        camera = _connect(client, cls.id, WINDOWS_CAMERA).json()["data"]
        base = f"/api/v1/classes/{cls.id}/classroom"
        lease = client.post(base + "/media-sessions", json={"audio": False}).json()["data"]
        assert lease["available"] and set(lease["offers"]) == {"video"}
        audio = client.post(f"{base}/media-sessions/{lease['session_id']}/audio/offer", json={"sdp": AUDIO_SDP})
        assert audio.status_code == 403
        mixed = client.post(lease["offers"]["video"], json={"sdp": VIDEO_SDP + AUDIO_SDP})
        assert mixed.status_code == 403 and media_ready == []
        video = client.post(lease["offers"]["video"], json={"sdp": VIDEO_SDP})
        assert video.status_code == 200, video.text
        token = media_ready[0][2]["headers"]["Authorization"].removeprefix("Bearer ")
        path = media.path_for(camera["camera_id"], 1, "video")
        assert media.verify_capability({"token": token, "path": path, "action": "read"})
        assert not media.verify_capability({"token": token, "path": path.replace("video", "audio"), "action": "read"})
        assert not media.verify_capability({"token": token, "path": path, "action": "publish"})


def test_device_publish_requires_live_demand_and_current_camera_revision(client, db, sample, media_ready):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        camera = _connect(client, cls.id, WINDOWS_CAMERA).json()["data"]
        headers = {"X-ClassClaw-Device-ID": paired["device_id"], "Authorization": f"Bearer {paired['credential']}"}
        path = f"/api/v1/classroom/device/media/{camera['camera_id']}/1/video/offer"
        body = {"sdp": VIDEO_SDP.replace("recvonly", "sendonly")}
        assert client.post(path, json=body, headers=headers).status_code == 403
        client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={"audio": False})
        state = media.device_state(db, db.get(ClassroomDevice, paired["device_id"]))
        assert state["enabled"] and state["video"] and not state["audio"]
        assert client.post(path, json=body, headers=headers).status_code == 200
        assert client.post(path.replace("/1/", "/2/"), json=body, headers=headers).status_code == 403


def test_releasing_lease_terminates_existing_peer(client, db, sample, media_ready, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def reader():
        yield db

    monkeypatch.setattr(media, "reader_session", reader)
    cls, _, _ = sample
    pair_terminal(client, cls)
    _connect(client, cls.id, WINDOWS_CAMERA)
    base = f"/api/v1/classes/{cls.id}/classroom"
    lease = client.post(base + "/media-sessions", json={}).json()["data"]
    result = client.post(lease["offers"]["video"], json={"sdp": VIDEO_SDP}).json()["data"]
    assert result["peer_id"] in media._peers
    client.delete(f"{base}/media-sessions/{lease['session_id']}")
    asyncio.run(media.reconcile())
    assert result["peer_id"] not in media._peers
    assert any(call[0] == "DELETE" for call in media_ready)


def test_unauthorized_media_callback_never_grants_access(client):
    assert client.post("/api/v1/classroom/media/auth", json={"action": "read", "path": "classclaw/a/1/video"}).status_code == 401
    assert client.post("/api/v1/classroom/media/auth", json=[]).status_code == 401


def test_single_frame_observation_releases_temporary_lease(client, db, sample, media_ready, monkeypatch):
    from app.services import classroom_observation

    async def snapshot(*_args):
        return b"jpeg-frame"

    async def analyze(*_args, **kwargs):
        assert kwargs["user"].startswith("classroom-observation:")
        assert kwargs["extra_content"][0]["type"] == "input_image"
        return {"scene": "教室画面", "visible_activity": "几人站在桌旁", "limitations": ["仅单帧，没有声音"]}

    monkeypatch.setattr(classroom_observation.classroom_sources, "snapshot", snapshot)
    monkeypatch.setattr(classroom_observation.openclaw_bridge, "_responses_json", analyze)
    cls, _, _ = sample
    pair_terminal(client, cls)
    _connect(client, cls.id, WINDOWS_CAMERA)
    result = client.post(f"/api/v1/classes/{cls.id}/classroom/observation")
    assert result.status_code == 200, result.text
    assert result.json()["data"]["audio_used"] is False
    from sqlalchemy import select
    assert all(row.status == "released" for row in db.scalars(select(ClassroomMediaSession)))


def test_compact_rtsp_capability_expires_and_is_action_scoped(monkeypatch):
    monkeypatch.setattr(media.time, "time", lambda: 1000)
    path = media.path_for("camera", 1, "video")
    token = media.capability("read", path, 30)
    assert len(token) < 100
    payload = {"password": token, "path": path, "action": "read"}
    assert media.verify_capability(payload)
    assert not media.verify_capability({**payload, "action": "publish"})
    monkeypatch.setattr(media.time, "time", lambda: 1030)
    assert not media.verify_capability(payload)


def test_audio_stops_at_last_audio_viewer_while_video_viewer_remains(client, db, sample, media_ready):
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    with online_terminal(db, paired["device_id"], cls.id):
        _connect(client, cls.id, WINDOWS_CAMERA)
        base = f"/api/v1/classes/{cls.id}/classroom"
        audio = client.post(base + "/media-sessions", json={"audio": True}).json()["data"]
        client.post(base + "/media-sessions", json={"audio": False})
        device = db.get(ClassroomDevice, paired["device_id"])
        assert media.device_state(db, device)["audio"]
        client.delete(f"{base}/media-sessions/{audio['session_id']}")
        state = media.device_state(db, device)
        assert state["video"] and not state["audio"]
        assert set(state["publish"]) == {"video"}


def test_observation_failure_still_releases_lease(client, db, sample, media_ready, monkeypatch):
    from sqlalchemy import select

    from app.core.errors import AppError
    from app.services import classroom_observation

    async def snapshot(*_args):
        return b"jpeg"

    async def fail(*_args, **_kwargs):
        raise AppError("OPENCLAW_UNAVAILABLE", "模型离线", 503)

    monkeypatch.setattr(classroom_observation.classroom_sources, "snapshot", snapshot)
    monkeypatch.setattr(classroom_observation.openclaw_bridge, "_responses_json", fail)
    cls, _, _ = sample
    pair_terminal(client, cls)
    _connect(client, cls.id, WINDOWS_CAMERA)
    result = client.post(f"/api/v1/classes/{cls.id}/classroom/observation")
    assert result.status_code == 503
    assert cls.id not in classroom_observation._busy
    assert all(row.status == "released" for row in db.scalars(select(ClassroomMediaSession)))


def test_network_camera_rejects_dns_resolution_to_loopback(monkeypatch):
    from app.core.errors import AppError
    from app.models.entities import ClassroomCamera
    from app.services import classroom_sources
    camera = ClassroomCamera(protocol="rtsp", location="rtsp://camera.example/live")
    monkeypatch.setattr(classroom_sources.socket, "getaddrinfo", lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 554))])
    with pytest.raises(AppError, match="禁止访问"):
        classroom_sources.pinned_source(camera)


def test_restart_cleans_both_webrtc_and_rtsp_sessions(monkeypatch):
    calls = []

    async def request(method, url, **_kwargs):
        calls.append((method, url))
        if method == "GET":
            return httpx.Response(200, json={"items": [{"id": "old", "path": "classclaw/camera/1/video"},
                                                      {"id": "unrelated", "path": "other/camera"}]})
        return httpx.Response(200)

    monkeypatch.setattr(media, "request", request)
    monkeypatch.setattr(media, "_ready", False)
    asyncio.run(media.recover())
    assert media._ready
    assert len([row for row in calls if row[0] == "POST"]) == 2
    assert any("/rtspsessions/kick/old" in url for _, url in calls)
    assert all("unrelated" not in url for _, url in calls)


def test_bad_relay_credentials_disable_media_without_breaking_control(client, db, sample, media_ready, monkeypatch):
    from app.models.entities import ClassroomCamera
    from tests.test_classroom_camera_media import NETWORK_CAMERA
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    _connect(client, cls.id, {**NETWORK_CAMERA, "access_path": "windows_relay"})
    client.post(f"/api/v1/classes/{cls.id}/classroom/media-sessions", json={})
    from sqlalchemy import select
    camera = db.scalar(select(ClassroomCamera))
    camera.credential_ref = "MISSING_CAMERA"
    db.commit()
    monkeypatch.delenv("CLASSCLAW_CLASSROOM_CAMERA_MISSING_CAMERA", raising=False)
    state = media.device_state(db, db.get(ClassroomDevice, paired["device_id"]))
    assert not state["enabled"] and state["publish"] == {}
    assert state["reason"] == "CAMERA_CREDENTIAL_MISSING"


def test_idle_capture_report_clears_connected_status(client, db, sample, media_ready):
    from app.models.entities import ClassroomCamera
    from app.schemas.classroom import DeviceHeartbeat
    cls, _, _ = sample
    paired = pair_terminal(client, cls)
    camera_id = _connect(client, cls.id, WINDOWS_CAMERA).json()["data"]["camera_id"]
    camera = db.get(ClassroomCamera, camera_id)
    camera.status = "connected"
    db.commit()
    device = db.get(ClassroomDevice, paired["device_id"])
    classroom_devices.heartbeat(db, device, DeviceHeartbeat(type="heartbeat", camera_state={
        "camera_id": camera_id, "config_revision": 1, "identifier": "usb-cam-1", "state": "idle"}))
    assert camera.status == "registered"
