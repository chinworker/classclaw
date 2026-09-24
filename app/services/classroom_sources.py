"""On-demand server-side RTSP capture and short-lived snapshots.

No shell strings, arbitrary file outputs or user-selected executables. Camera
passwords are fetched only at execution time; subprocess output is not logged.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse, urlunparse

from sqlalchemy import select

from app.config import settings
from app.core.errors import AppError
from app.database import reader_session, writer_session
from app.models.entities import ClassroomCamera
from app.utils.time import now


@dataclass
class Source:
    revision: int
    audio: bool
    process: asyncio.subprocess.Process


_sources: dict[str, Source] = {}
_errors: dict[str, tuple[int, str]] = {}
_retry_after: dict[str, float] = {}


def source_for_terminal(camera: ClassroomCamera) -> dict:
    from app.services.classroom_camera import resolve_credential
    # Sent exclusively through an authenticated device socket, never through user APIs or command ledger.
    result = {"protocol": camera.protocol, "location": camera.location}
    if camera.credential_ref:
        try:
            secret = json.loads(resolve_credential(camera.credential_ref))
            result["username"] = str(secret["username"])
            result["password"] = str(secret["password"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError("CAMERA_CREDENTIAL_INVALID", "摄像头凭据必须是含 username/password 的 JSON", 503) from exc
    return result


def pinned_source(camera: ClassroomCamera) -> str:
    from app.services.classroom_camera import validate_location
    source = source_for_terminal(camera)
    if source["protocol"] != "rtsp":
        raise AppError("CAMERA_PROTOCOL_UNSUPPORTED", "服务器直读目前支持 RTSP；其他来源请由 Windows 采集后发布", 422)
    validate_location(source["location"], "rtsp")
    parsed = urlparse(source["location"])
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 554, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise AppError("CAMERA_HOST_UNREACHABLE", "摄像头主机无法解析", 502) from exc
    ips = [ipaddress.ip_address(row[4][0]) for row in addresses]
    if not ips or any(ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
                      or getattr(ip, "ipv4_mapped", None) and (ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_link_local)
                      for ip in ips):
        raise AppError("CAMERA_LOCATION_FORBIDDEN", "摄像头域名解析到禁止访问的地址", 403)
    host = f"[{ips[0]}]" if ips[0].version == 6 else str(ips[0])
    auth = ""
    if "username" in source:
        auth = f"{quote(source['username'], safe='')}:{quote(source['password'], safe='')}@"
    return urlunparse(parsed._replace(netloc=f"{auth}{host}:{parsed.port or 554}"))


def _wanted() -> list[tuple[str, int, bool, str]]:
    from app.services.classroom_streaming import demand
    result = []
    with reader_session() as db:
        for camera in db.scalars(select(ClassroomCamera).where(ClassroomCamera.access_path == "server_direct")):
            needed = demand(db, camera)
            if needed["video"]:
                try:
                    source = pinned_source(camera)
                except AppError as exc:
                    _errors[camera.id] = (camera.config_revision, exc.message)
                    continue
                result.append((camera.id, camera.config_revision, needed["audio"], source))
    return result


def local_url(camera_id: str, revision: int, track: str, action: str, ttl: int = 86400) -> str:
    from app.services.classroom_streaming import capability, path_for
    path = path_for(camera_id, revision, track)
    parsed = urlparse(settings.classroom.media_rtsp_url)
    token = capability(action, path, ttl)
    return urlunparse(parsed._replace(netloc=f"classclaw:{token}@{parsed.netloc}", path=f"/{path}"))


async def _stop(source: Source) -> None:
    if source.process.returncode is None:
        source.process.terminate()
        try:
            await asyncio.wait_for(source.process.wait(), 3)
        except TimeoutError:
            source.process.kill()
            await source.process.wait()


async def reconcile_sources() -> None:
    wanted = {row[0]: row for row in await asyncio.to_thread(_wanted)}
    for camera_id, source in list(_sources.items()):
        row = wanted.get(camera_id)
        if not row or (source.revision, source.audio) != (row[1], row[2]) or source.process.returncode is not None:
            if row and source.process.returncode is not None:
                _errors[camera_id] = (source.revision, "摄像头采集进程已退出，请检查地址、凭据和音视频轨道")
                _retry_after[camera_id] = time.monotonic() + 10
            await _stop(source)
            _sources.pop(camera_id, None)
    for camera_id in set(_retry_after) - wanted.keys():
        _retry_after.pop(camera_id, None)
    for camera_id, revision, audio, source_url in wanted.values():
        if camera_id in _sources or _retry_after.get(camera_id, 0) > time.monotonic():
            continue
        args = [settings.classroom.media_ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", source_url,
                "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-vf", "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-r", "10", "-pix_fmt", "yuv420p", "-bf", "0", "-g", "20",
                "-b:v", "700k", "-f", "rtsp", "-rtsp_transport", "tcp", local_url(camera_id, revision, "video", "publish")]
        if audio:
            args += ["-map", "0:a:0", "-vn", "-c:a", "libopus", "-ac", "1", "-ar", "48000", "-b:a", "32k",
                     "-f", "rtsp", "-rtsp_transport", "tcp", local_url(camera_id, revision, "audio", "publish")]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            _errors[camera_id] = (revision, "服务器未安装可用 FFmpeg")
            _retry_after[camera_id] = time.monotonic() + 10
            continue
        _sources[camera_id] = Source(revision, audio, proc)


def update_status(paths: list[dict]) -> None:
    """Only claim a direct camera is connected once the relay reports a ready video path."""
    from app.services.classroom_streaming import demand, path_for
    ready = {path.get("name") for path in paths if path.get("ready")}
    with writer_session() as db:
        cameras = list(db.scalars(select(ClassroomCamera).where(ClassroomCamera.access_path == "server_direct")))
        for camera in cameras:
            if path_for(camera.id, camera.config_revision, "video") in ready:
                if camera.status != "connected":
                    camera.connected_at = now()
                camera.status, camera.last_error = "connected", None
                _errors.pop(camera.id, None)
            elif not demand(db, camera)["video"]:
                camera.status, camera.last_error = "registered", None
                _errors.pop(camera.id, None)
            else:
                failure = _errors.get(camera.id)
                if failure and failure[0] == camera.config_revision:
                    camera.status, camera.last_error = "failed", failure[1]
                else:
                    camera.status, camera.last_error = "registered", None
        for camera_id in set(_errors) - {camera.id for camera in cameras}:
            _errors.pop(camera_id, None)
        db.commit()


async def stop_sources() -> None:
    for source in list(_sources.values()):
        await _stop(source)
    _sources.clear()
    _errors.clear()
    _retry_after.clear()


async def snapshot(camera_id: str, revision: int) -> bytes:
    args = [settings.classroom.media_ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", local_url(camera_id, revision, "video", "read", 30),
            "-frames:v", "1", "-an", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
            "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError as exc:
        raise AppError("MEDIA_ENCODER_UNAVAILABLE", "服务器未安装 FFmpeg", 503) from exc
    try:
        data, _ = await asyncio.wait_for(proc.communicate(), 12)
        if proc.returncode or not data or len(data) > 2 * 1024 * 1024:
            raise AppError("CAMERA_SAMPLE_UNAVAILABLE", "未获得有效画面，请确认终端正在采集", 503)
        return data
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
