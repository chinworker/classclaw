"""实时监控观看租约：短期、绑定用户与班级、音轨单独授权。

播放器静音不等于没有采集声音，所以音轨授权在媒体层执行。租约到期、离页、登出、
撤权、更换或断开摄像头都会终止会话；多媒体播放链接不做永久公开分享。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.database import writer_session
from app.models.entities import ClassroomCamera, ClassroomMediaSession
from app.services.class_student import get_class
from app.utils.time import now

TOKEN_BYTES = 32


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=now().tzinfo)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def expire_overdue(db: Session, *, commit: bool = True) -> int:
    if db.info.get("read_only"):
        with writer_session() as writer:
            changed = expire_overdue(writer)
        db.expire_all()
        return changed
    current = now()
    rows = list(db.scalars(select(ClassroomMediaSession).where(ClassroomMediaSession.status == "active")))
    changed = 0
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is not None and expires <= current:
            row.status = "expired"
            row.released_at = current
            changed += 1
    if changed and commit:
        db.commit()
    return changed


def viewer_count(db: Session, camera_id: str) -> int:
    return db.scalar(select(func.count()).select_from(ClassroomMediaSession).where(
        ClassroomMediaSession.camera_id == camera_id, ClassroomMediaSession.status == "active",
        ClassroomMediaSession.expires_at > now())) or 0


def issue(db: Session, class_id: str, camera: ClassroomCamera, *, user_id: str | None, audio: bool,
          surface: str = "web", created_by: str | None = None) -> dict:
    """Grant a short viewing lease. Audio is granted only when the source really has it."""
    expire_overdue(db, commit=False)
    audio_reason = None
    audio_allowed = bool(audio)
    if audio_allowed and not camera.audio_capable:
        audio_allowed = False
        audio_reason = "该摄像头没有可用音轨；现场声音需另接麦克风，不能凭空获取"
    if camera.status == "disconnected":
        raise AppError("CAMERA_DISCONNECTED", "摄像头已断开，无法开始观看", 409, {"camera_id": camera.id})
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session = ClassroomMediaSession(
        class_id=class_id, camera_id=camera.id, user_id=user_id, token_hash=_hash(token),
        video_allowed=True, audio_allowed=audio_allowed, status="active", surface=surface,
        expires_at=now() + timedelta(seconds=settings.classroom.media_lease_seconds),
        last_seen_at=now(), created_by=created_by,
    )
    db.add(session)
    db.commit()
    # The token is returned exactly once; only its hash is stored.
    return {"session_id": session.id, "camera_id": camera.id, "class_id": class_id, "token": token,
            "video_allowed": True, "audio_allowed": audio_allowed, "audio_unavailable_reason": audio_reason,
            "expires_at": session.expires_at, "lease_seconds": settings.classroom.media_lease_seconds,
            "viewers": viewer_count(db, camera.id), **playback(camera, session)}


def playback(camera: ClassroomCamera, session: ClassroomMediaSession) -> dict:
    """The media-forwarding boundary. Never fabricate a stream that is not wired up."""
    provider = settings.classroom.media_provider
    if provider == "none":
        return {"available": False, "provider": provider, "reason": "MEDIA_NOT_CONFIGURED",
                "message": "尚未接入媒体转发组件，当前只能查看终端与摄像头状态，不能显示画面"}
    from app.services import classroom_streaming
    if not classroom_streaming._ready:
        return {"available": False, "provider": provider, "reason": "MEDIA_UNAVAILABLE", "message": "媒体服务尚未就绪，请检查部署"}
    prefix = f"{settings.api_prefix}/classes/{camera.class_id}/classroom/media-sessions/{session.id}"
    return {"available": True, "provider": provider, "protocol": "webrtc",
            "offers": {track: f"{prefix}/{track}/offer" for track in ("video", "audio") if track == "video" or session.audio_allowed},
            "ice_servers": settings.classroom.media_ice_servers,
            "message": "媒体服务已接入，等待教室采集端发布"}


def authenticate(db: Session, token: str, *, camera_id: str | None = None) -> ClassroomMediaSession:
    """Media-layer authorization: the path alone must not bypass this check."""
    session = db.scalar(select(ClassroomMediaSession).where(ClassroomMediaSession.token_hash == _hash(token)))
    if not session:
        raise AppError("MEDIA_SESSION_INVALID", "观看会话不存在或已失效", 401)
    if session.status != "active":
        raise AppError("MEDIA_SESSION_INVALID", f"观看会话已{session.status}", 401, {"status": session.status})
    expires = _aware(session.expires_at)
    if expires is None or expires <= now():
        session.status = "expired"
        session.released_at = now()
        db.commit()
        raise AppError("MEDIA_SESSION_EXPIRED", "观看会话已过期，请重新发起观看", 401)
    if camera_id and session.camera_id != camera_id:
        raise AppError("CLASS_ACCESS_DENIED", "观看会话与请求的摄像头不一致", 403)
    session.last_seen_at = now()
    db.commit()
    return session


def renew(db: Session, session_id: str, *, user_id: str | None, is_admin: bool) -> dict:
    session = _require_own(db, session_id, user_id=user_id, is_admin=is_admin)
    expire_overdue(db, commit=False)
    if session.status != "active":
        raise AppError("MEDIA_SESSION_INVALID", f"观看会话已{session.status}，请重新发起观看", 409,
                       {"status": session.status})
    session.expires_at = now() + timedelta(seconds=settings.classroom.media_lease_seconds)
    session.last_seen_at = now()
    db.commit()
    return public_session(session)


def release(db: Session, session_id: str, *, user_id: str | None, is_admin: bool) -> dict:
    """离页释放本人会话。最后一名观看者离开后进入宽限，不立刻停流。"""
    session = _require_own(db, session_id, user_id=user_id, is_admin=is_admin)
    if session.status == "active":
        session.status = "released"
        session.released_at = now()
        db.commit()
    return {**public_session(session), **streaming_state(db, session.camera_id)}


def _require_own(db: Session, session_id: str, *, user_id: str | None, is_admin: bool) -> ClassroomMediaSession:
    session = db.get(ClassroomMediaSession, session_id)
    if not session:
        raise not_found("观看会话", session_id)
    if not is_admin and session.user_id != user_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能管理本人发起的观看会话", 403, {"session_id": session_id})
    return session


def revoke_camera(db: Session, camera_id: str, reason: str, *, commit: bool = True) -> int:
    rows = list(db.scalars(select(ClassroomMediaSession).where(
        ClassroomMediaSession.camera_id == camera_id, ClassroomMediaSession.status == "active")))
    for row in rows:
        row.status = "revoked"
        row.released_at = now()
        row.revoke_reason = reason
    if rows and commit:
        db.commit()
    return len(rows)


def revoke_user(db: Session, user_id: str, reason: str = "logout", *, commit: bool = True) -> int:
    """登出即终止本人已建立的观看会话。"""
    rows = list(db.scalars(select(ClassroomMediaSession).where(
        ClassroomMediaSession.user_id == user_id, ClassroomMediaSession.status == "active")))
    for row in rows:
        row.status = "revoked"
        row.released_at = now()
        row.revoke_reason = reason
    if rows and commit:
        db.commit()
    return len(rows)


def streaming_state(db: Session, camera_id: str) -> dict:
    """One uplink is shared by all viewers; the last one leaving ends it after a grace period."""
    viewers = viewer_count(db, camera_id)
    grace = settings.classroom.media_stop_grace_seconds
    if viewers:
        return {"viewers": viewers, "keep_stream_up": True, "grace_remaining_seconds": None,
                "provider": settings.classroom.media_provider}
    last = db.scalar(select(func.max(ClassroomMediaSession.released_at)).where(
        ClassroomMediaSession.camera_id == camera_id, ClassroomMediaSession.released_at.is_not(None)))
    last = _aware(last)
    elapsed = (now() - last).total_seconds() if last else None
    remaining = None if elapsed is None else max(0, int(grace - elapsed))
    return {"viewers": 0, "keep_stream_up": bool(remaining), "grace_remaining_seconds": remaining,
            "provider": settings.classroom.media_provider}


def public_session(session: ClassroomMediaSession) -> dict:
    return {"session_id": session.id, "camera_id": session.camera_id, "class_id": session.class_id,
            "video_allowed": session.video_allowed, "audio_allowed": session.audio_allowed,
            "status": session.status, "surface": session.surface, "expires_at": session.expires_at,
            "last_seen_at": session.last_seen_at, "released_at": session.released_at,
            "revoke_reason": session.revoke_reason, "created_at": session.created_at}


def list_sessions(db: Session, class_id: str) -> dict:
    get_class(db, class_id)
    expire_overdue(db)
    rows = list(db.scalars(
        select(ClassroomMediaSession)
        .where(ClassroomMediaSession.class_id == class_id, ClassroomMediaSession.status == "active")
        .order_by(ClassroomMediaSession.created_at.desc())
    ))
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == class_id))
    return {"items": [public_session(row) for row in rows], "total": len(rows),
            "streaming": streaming_state(db, camera.id) if camera else None}
