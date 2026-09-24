"""Authenticated SDP signaling, with all media I/O outside the SQLite writer lock."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db, suspend_writer
from app.models.entities import ClassroomCamera
from app.schemas.classroom import MediaOffer, MediaPeerClose
from app.services import classroom_devices
from app.services import classroom_streaming as media

public_router = APIRouter(prefix="/classroom", tags=["媒体终端接入"])
router = APIRouter(prefix="/classes/{class_id}/classroom", tags=["监控播放"])


@router.post("/observation")
async def observation(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    from app.services.classroom_observation import observe
    principal = principal_from_request(request)
    return ok(request, await observe(db, class_id, user_id=principal.user_id, actor=principal.username))


def _device(request: Request, db: Session):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return classroom_devices.authenticate(db, request.headers.get("X-ClassClaw-Device-ID", ""), token)


@public_router.post("/media/auth")
async def media_auth(request: Request):
    # MediaMTX auth callbacks use signed, action/path-scoped capabilities issued only by this process.
    if len(await request.body()) > 8192:
        return Response(status_code=401)
    try:
        accepted = media.verify_capability(await request.json())
    except (ValueError, TypeError, AttributeError):
        accepted = False
    return Response(status_code=204 if accepted else 401)


@public_router.post("/device/media/{camera_id}/{revision}/{track}/offer")
async def publish_offer(request: Request, camera_id: str, revision: int, track: str,
                        body: MediaOffer, db: Session = Depends(get_db)):
    device = _device(request, db)
    media.publisher_camera(db, device, camera_id, revision, track)
    device_id = device.id
    async with suspend_writer(db):
        result = await media.offer(camera_id, revision, track, body.sdp, device_id=device_id)
    # Revocation/replacement can happen while SDP is in flight.
    try:
        media.publisher_camera(db, _device(request, db), camera_id, revision, track)
    except AppError:
        async with suspend_writer(db):
            await media.close(result["peer_id"])
        raise
    return ok(request, result)


@public_router.post("/device/media/close")
async def publish_close(request: Request, body: MediaPeerClose, db: Session = Depends(get_db)):
    device_id = _device(request, db).id
    async with suspend_writer(db):
        await media.close_owned(body.peer_id, device_id=device_id)
    return ok(request, {"closed": True})


@router.post("/media-sessions/{session_id}/{track}/offer")
async def view_offer(request: Request, class_id: str, session_id: str, track: str,
                     body: MediaOffer, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    lease = media.valid_lease(db, session_id, class_id, principal.user_id, check_owner=True)
    if track not in {"video", "audio"} or track == "audio" and not lease.audio_allowed:
        raise AppError("MEDIA_TRACK_DENIED", "观看租约没有该音视频轨道权限", 403)
    camera = db.get(ClassroomCamera, lease.camera_id)
    if not camera:
        raise AppError("CAMERA_DISCONNECTED", "摄像头已断开", 409)
    camera_id, revision = camera.id, camera.config_revision
    async with suspend_writer(db):
        result = await media.offer(camera_id, revision, track, body.sdp, lease_id=session_id, user_id=principal.user_id)
    try:
        media.valid_lease(db, session_id, class_id, principal.user_id, check_owner=True)
        camera = db.get(ClassroomCamera, camera_id, populate_existing=True)
        if not camera or camera.config_revision != revision:
            raise AppError("CAMERA_REVISION_CONFLICT", "摄像头已更换", 409)
    except AppError:
        async with suspend_writer(db):
            await media.close(result["peer_id"])
        raise
    return ok(request, result)


@router.post("/media/close")
async def view_close(request: Request, class_id: str, body: MediaPeerClose, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    async with suspend_writer(db):
        await media.close_owned(body.peer_id, user_id=principal.user_id)
    return ok(request, {"closed": True})
