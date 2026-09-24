"""MediaMTX signaling adapter and bounded, process-local media connections.

Media packets go directly through MediaMTX. FastAPI only handles SDP/control.
Video and audio use separate paths: a video-only lease cannot negotiate audio.
All client paths are constructed by the server, never supplied as arbitrary URLs.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
import weakref
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.database import reader_session, writer_session
from app.models.entities import ClassRoom, ClassroomCamera, ClassroomDevice, ClassroomMediaSession, User
from app.services import classroom_channel, classroom_devices, classroom_media
from app.utils.time import now

_key = secrets.token_bytes(32)
_peers: dict[str, Peer] = {}
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_last_states: dict[str, str] = {}
_ready = False


@dataclass
class Peer:
    id: str
    location: str
    camera_id: str
    revision: int
    track: str
    device_id: str | None = None
    lease_id: str | None = None
    user_id: str | None = None


def configured() -> bool:
    return settings.classroom.media_provider == "mediamtx"


def path_for(camera_id: str, revision: int, track: str) -> str:
    if track not in {"video", "audio"}:
        raise AppError("MEDIA_TRACK_INVALID", "只允许 video 或 audio", 422)
    return f"classclaw/{camera_id}/{revision}/{track}"


def capability(action: str, path: str, ttl: int = 30) -> str:
    # FFmpeg's RTSP userinfo buffer is small. Sign the callback's action/path
    # instead of embedding them in the password, keeping this token under 80 bytes.
    expiry = str(int(time.time()) + ttl)
    raw = f"{action}\n{path}\n{expiry}".encode()
    return expiry + "." + hmac.new(_key, raw, hashlib.sha256).hexdigest()


def verify_capability(payload: dict) -> bool:
    token = payload.get("token") or payload.get("password") or ""
    try:
        expiry, signature = token.rsplit(".", 1)
        path = payload.get("path", "")
        raw = f"{payload.get('action')}\n{path}\n{expiry}".encode()
        expected = hmac.new(_key, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
        return int(expiry) > time.time() and path.startswith("classclaw/")
    except (ValueError, TypeError, AttributeError):
        return False


def validate_sdp(sdp: str, track: str, *, publish: bool) -> None:
    # One media section per request; reject mixed A/V and data channels before MediaMTX.
    sections = [line.split() for line in sdp.replace("\r", "").split("\n") if line.startswith("m=")]
    if len(sections) != 1 or sections[0][0] != f"m={track}":
        raise AppError("MEDIA_TRACK_DENIED", "SDP 必须且只能包含所授权的一路音轨或视频轨", 403)
    if publish and "a=recvonly" in sdp:
        raise AppError("MEDIA_SDP_INVALID", "发布会话必须发送媒体", 422)


def valid_lease(db: Session, lease_id: str, class_id: str | None = None, user_id: str | None = None,
                *, check_owner: bool = False) -> ClassroomMediaSession:
    lease = db.get(ClassroomMediaSession, lease_id)
    if not lease or lease.status != "active" or classroom_media._aware(lease.expires_at) <= now():
        raise AppError("MEDIA_SESSION_EXPIRED", "观看租约已结束，请重新开始观看", 401)
    if class_id and lease.class_id != class_id or check_owner and lease.user_id != user_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能使用本人本班观看租约", 403)
    cls = db.get(ClassRoom, lease.class_id)
    if not cls or cls.deleted_at:
        raise AppError("MEDIA_SESSION_INVALID", "班级已不可用", 403)
    if lease.user_id:
        user = db.get(User, lease.user_id)
        if not user or not user.is_active or (user.role != "admin" and cls.owner_user_id != user.id):
            raise AppError("MEDIA_SESSION_INVALID", "观看权限已撤销", 403)
    return lease


def demand(db: Session, camera: ClassroomCamera) -> dict:
    rows = list(db.scalars(select(ClassroomMediaSession).where(
        ClassroomMediaSession.camera_id == camera.id, ClassroomMediaSession.status == "active",
        ClassroomMediaSession.expires_at > now())))
    valid = []
    for lease in rows:
        try:
            valid_lease(db, lease.id)
            valid.append(lease)
        except AppError:
            pass
    audio = camera.audio_capable and any(row.audio_allowed for row in valid)
    # Audio stops immediately when its last authorized viewer leaves. Only video has grace.
    grace = classroom_media.streaming_state(db, camera.id)["keep_stream_up"] if not valid and not rows else False
    latest = db.scalar(select(ClassroomMediaSession).where(ClassroomMediaSession.camera_id == camera.id)
                       .order_by(ClassroomMediaSession.created_at.desc()))
    if latest and latest.status == "revoked":
        grace = False
    return {"video": bool(valid) or bool(grace), "audio": bool(audio), "viewers": len(valid)}


def device_state(db: Session, device: ClassroomDevice) -> dict:
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.device_id == device.id))
    state = {"type": "media_state", "enabled": False, "camera": None, "video": False, "audio": False,
             "valid_until": (now() + timedelta(seconds=settings.classroom.heartbeat_timeout_seconds)).isoformat(),
             "volume_ceiling": settings.classroom.volume_ceiling, "ice_servers": settings.classroom.media_ice_servers}
    if not camera:
        return state
    state["camera"] = {"camera_id": camera.id, "config_revision": camera.config_revision,
                       "identifier": camera.device_identifier, "microphone_identifier": camera.microphone_identifier,
                       "source_kind": camera.source_kind, "access_path": camera.access_path,
                       "video": camera.video_json, "audio_capable": camera.audio_capable}
    if not configured() or not _ready or camera.status == "disconnected":
        state["reason"] = "MEDIA_NOT_CONFIGURED" if not configured() else "MEDIA_UNAVAILABLE"
        return state
    state.update(demand(db, camera))
    state["enabled"] = state["video"] or state["audio"]
    prefix = f"{settings.api_prefix}/classroom/device/media/{camera.id}/{camera.config_revision}"
    state["publish"] = {track: f"{prefix}/{track}/offer" for track in ("video", "audio") if state[track]}
    if state["enabled"] and camera.source_kind == "network_stream":
        from app.services.classroom_sources import source_for_terminal
        try:
            state["camera"]["source"] = source_for_terminal(camera)
        except AppError as exc:
            # Bad camera configuration must not disconnect the broadcast/control channel.
            state.update(enabled=False, video=False, audio=False, publish={}, reason=exc.code, message=exc.message)
    return state


def publisher_camera(db: Session, device: ClassroomDevice, camera_id: str, revision: int, track: str) -> ClassroomCamera:
    camera = db.get(ClassroomCamera, camera_id)
    if (track not in {"video", "audio"} or not camera or camera.device_id != device.id or camera.config_revision != revision
            or camera.status == "disconnected"
            or not classroom_devices.is_online(db, device) or not demand(db, camera).get(track)):
        raise AppError("MEDIA_PUBLISH_DENIED", "当前设备、摄像头版本或音视频采集授权已失效", 403)
    return camera


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
            return await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise AppError("MEDIA_UNAVAILABLE", "媒体组件不可达，请检查服务器媒体服务", 503) from exc


async def offer(camera_id: str, revision: int, track: str, sdp: str, *, device_id: str | None = None,
                lease_id: str | None = None, user_id: str | None = None) -> dict:
    if not configured() or not _ready:
        raise AppError("MEDIA_NOT_CONFIGURED", "媒体服务尚未就绪", 503)
    publish = device_id is not None
    validate_sdp(sdp, track, publish=publish)
    path = path_for(camera_id, revision, track)
    lock_key = f"{device_id or lease_id}:{track}"
    lock = _locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        # Replacing an existing peer for the same owner/track cannot leak it.
        for peer in list(_peers.values()):
            if peer.track == track and peer.device_id == device_id and peer.lease_id == lease_id:
                await close(peer.id)
        url = settings.classroom.media_base_url.rstrip("/") + f"/{path}/{'whip' if publish else 'whep'}"
        token = capability("publish" if publish else "read", path)
        response = await request("POST", url, content=sdp,
                                 headers={"Content-Type": "application/sdp", "Authorization": f"Bearer {token}"})
        if response.status_code != 201:
            raise AppError("MEDIA_NEGOTIATION_FAILED", "媒体协商失败，采集端可能尚未就绪或编码不兼容", 502,
                           {"upstream_status": response.status_code})
        location = urljoin(url, response.headers.get("Location", ""))
        if not response.headers.get("Location") or urlparse(location).netloc != urlparse(url).netloc:
            raise AppError("MEDIA_NEGOTIATION_FAILED", "媒体组件返回了无效会话地址", 502)
        peer_id = secrets.token_hex(16)
        peer = Peer(peer_id, location, camera_id, revision, track, device_id, lease_id, user_id)
        _peers[peer_id] = peer
        return {"peer_id": peer_id, "sdp": response.text, "track": track}


async def close(peer_id: str) -> None:
    peer = _peers.get(peer_id)
    if not peer:
        return
    response = await request("DELETE", peer.location)
    if response.status_code not in {200, 204, 404, 410}:
        raise AppError("MEDIA_CLOSE_FAILED", "媒体会话尚未关闭，将继续重试", 503)
    _peers.pop(peer_id, None)


async def close_owned(peer_id: str, *, device_id: str | None = None, user_id: str | None = None) -> None:
    peer = _peers.get(peer_id)
    if peer and (peer.device_id != device_id or peer.user_id != user_id):
        raise AppError("CLASS_ACCESS_DENIED", "只能关闭本人媒体连接", 403)
    await close(peer_id)


def _invalid_peers() -> list[str]:
    invalid = []
    with reader_session() as db:
        for peer in list(_peers.values()):
            try:
                camera = db.get(ClassroomCamera, peer.camera_id)
                if not camera or camera.config_revision != peer.revision:
                    raise AppError("MEDIA_REVOKED", "摄像头已更换", 403)
                if peer.device_id:
                    device = classroom_devices.require_paired(db, peer.device_id)
                    publisher_camera(db, device, peer.camera_id, peer.revision, peer.track)
                else:
                    lease = valid_lease(db, peer.lease_id)
                    if peer.track == "audio" and not lease.audio_allowed:
                        raise AppError("MEDIA_TRACK_DENIED", "无声音权限", 403)
            except AppError:
                invalid.append(peer.id)
    return invalid


async def reconcile() -> None:
    for peer_id in await asyncio.to_thread(_invalid_peers):
        try:
            await close(peer_id)
        except AppError:
            pass  # Keep the entry and retry; never report revocation as physically complete.


def _maintenance() -> None:
    with writer_session() as db:
        classroom_devices.expire_overdue(db)
        classroom_media.expire_overdue(db)
        online_ids = classroom_channel.online_device_ids()
        for device_id in set(_last_states) - online_ids:
            _last_states.pop(device_id, None)
        for device_id in online_ids:
            device = db.get(ClassroomDevice, device_id)
            if not device:
                continue
            state = device_state(db, device)
            state.pop("valid_until", None)
            signature = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
            if _last_states.get(device_id) != signature:
                _last_states[device_id] = signature
                classroom_channel.notify(device_id)


async def recover() -> None:
    """A restarted backend must not leave MediaMTX sessions alive without an owner."""
    global _ready
    for protocol in ("webrtc", "rtsp"):
        prefix = settings.classroom.media_api_url.rstrip("/") + f"/v3/{protocol}sessions"
        response = await request("GET", prefix + "/list?itemsPerPage=10000")
        if response.status_code != 200:
            raise AppError("MEDIA_UNAVAILABLE", "媒体控制 API 不可用", 503)
        for item in response.json().get("items", []):
            if str(item.get("path", "")).startswith("classclaw/"):
                kicked = await request("POST", prefix + f"/kick/{item['id']}")
                if kicked.status_code not in {200, 404}:
                    raise AppError("MEDIA_UNAVAILABLE", "旧媒体连接尚未清理", 503)
    _ready = True


async def run() -> None:
    global _ready
    try:
        while True:
            try:
                if configured() and not _ready:
                    await recover()
                await asyncio.to_thread(_maintenance)
                await reconcile()
                if configured() and _ready:
                    from app.services.classroom_sources import reconcile_sources, update_status
                    await reconcile_sources()
                    response = await request("GET", settings.classroom.media_api_url.rstrip("/") + "/v3/paths/list?itemsPerPage=10000")
                    if response.status_code != 200:
                        raise AppError("MEDIA_UNAVAILABLE", "媒体状态接口不可用", 503)
                    await asyncio.to_thread(update_status, response.json().get("items", []))
            except Exception as exc:  # noqa: BLE001 -- keep the maintenance task alive, log only safe error types
                if isinstance(exc, AppError) and exc.code == "MEDIA_UNAVAILABLE":
                    _ready = False
                from app.core.logging import get_logger
                get_logger("classroom").warning("Media maintenance failed: %s", type(exc).__name__)
            await asyncio.sleep(2)
    finally:
        _ready = False
        for peer_id in list(_peers):
            try:
                await close(peer_id)
            except AppError:
                pass
        from app.services.classroom_sources import stop_sources
        await stop_sources()
