"""One authorized media track per child; credentials only arrive over inherited stdin.

The parent owns a kill-on-close Windows Job. EOF, expiry and disconnect also stop
capture here. Never print exceptions/SDP/URLs/credentials from native libraries.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from contextlib import suppress

import av
from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCRtpSender, RTCSessionDescription

logging.disable(logging.CRITICAL)
av.logging.set_level(av.logging.PANIC)


class CaptureError(Exception):
    pass


def endpoint(config: dict, path: str) -> str:
    base = urllib.parse.urlsplit(config["server"])
    if base.scheme != "https" and not (
        config.get("development") and base.scheme == "http" and base.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise CaptureError("SERVER_URL_INVALID")
    if base.username or base.password or base.query or base.fragment or base.path not in {"", "/"}:
        raise CaptureError("SERVER_URL_INVALID")
    if not path.startswith("/api/v1/classroom/device/media/") or ".." in path or "\\" in path or "?" in path or "#" in path:
        raise CaptureError("MEDIA_PATH_INVALID")
    return urllib.parse.urlunsplit((base.scheme, base.netloc, path, "", ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def post(config, path, payload):
    request = urllib.request.Request(
        endpoint(config, path),
        json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + config["credential"],
            "X-ClassClaw-Device-ID": config["device_id"],
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=15) as response:
        value = json.loads(response.read(131072))
    if not value.get("success"):
        raise CaptureError("MEDIA_NEGOTIATION_FAILED")
    return value["data"]


def source(config):
    camera, track = config["camera"], config["track"]
    if track not in {"video", "audio"}:
        raise CaptureError("MEDIA_TRACK_INVALID")
    if camera["source_kind"] == "windows_device":
        native = config.get("native_device")
        if not native or any(char in native for char in ('"', "\n", "\r")):
            raise CaptureError("CAPTURE_DEVICE_MISSING")
        options = {"video_size": "640x480", "framerate": "10"} if track == "video" else {}
        return f"{track}={native}", "dshow", options
    stream = camera.get("source", {})
    url = urllib.parse.urlsplit(stream.get("location", ""))
    # Initial release supports camera RTSP/HTTP media, never WHIP as a capture input.
    if url.scheme not in {"rtsp", "rtmp", "rtmps", "http", "https"} or url.scheme != stream.get("protocol") or not url.hostname:
        raise CaptureError("CAMERA_PROTOCOL_UNSUPPORTED")
    if url.username or url.password or url.hostname.lower().rstrip(".") == "localhost":
        raise CaptureError("CAMERA_LOCATION_FORBIDDEN")
    import socket

    ips = [
        ipaddress.ip_address(row[4][0])
        for row in socket.getaddrinfo(
            url.hostname,
            url.port or (554 if url.scheme == "rtsp" else 443 if url.scheme in {"https", "rtmps"} else 80),
            type=socket.SOCK_STREAM,
        )
    ]
    for ip in ips:
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise CaptureError("CAMERA_LOCATION_FORBIDDEN")
    if stream.get("username") is not None:
        auth = urllib.parse.quote(stream["username"], safe="") + ":" + urllib.parse.quote(stream.get("password", ""), safe="") + "@"
        url = url._replace(netloc=auth + url.netloc)
    options = {"rw_timeout": "5000000"}
    if url.scheme == "rtsp":
        options = {"rtsp_transport": "tcp", "timeout": "5000000", "allowed_media_types": track}
    return urllib.parse.urlunsplit(url), None, options


class Capture(MediaStreamTrack):
    def __init__(self, config):
        super().__init__()
        self.kind = config["track"]
        location, format_name, options = source(config)
        self.container = av.open(location, format=format_name, options=options, timeout=5)
        streams = [stream for stream in self.container.streams if stream.type == self.kind]
        if not streams:
            self.container.close()
            raise CaptureError("CAPTURE_TRACK_UNAVAILABLE")
        self.frames = iter(self.container.decode(streams[0]))
        self.first_pts = None
        self.started = time.monotonic()
        self.last_video = -1.0
        self.frame_received = False
        self.on_frame = lambda: None

    def read(self):
        try:
            frame = next(self.frames)
            if self.kind == "video":
                while frame.time is not None and frame.time < self.last_video + 0.095:
                    frame = next(self.frames)
                self.last_video = frame.time if frame.time is not None else self.last_video + 0.1
                width = min(640, frame.width)
                height = max(2, int(frame.height * width / frame.width) // 2 * 2)
                if height > 480:
                    width = max(2, int(width * 480 / height) // 2 * 2)
                    height = 480
                frame = frame.reformat(width=width // 2 * 2, height=height, format="yuv420p")
            if frame.pts is not None:
                if self.first_pts is None:
                    self.first_pts = frame.pts
                frame.pts -= self.first_pts
            return frame
        except (StopIteration, av.error.FFmpegError) as exc:
            raise CaptureError("CAPTURE_READ_FAILED") from exc

    async def recv(self):
        try:
            frame = await asyncio.to_thread(self.read)
        except Exception:
            self.stop()
            raise
        if not self.frame_received:
            self.frame_received = True
            self.on_frame()
        if frame.pts is not None and frame.time_base is not None:
            delay = self.started + float(frame.pts * frame.time_base) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(min(delay, 1))
        return frame


def emit(state, code=""):
    print(json.dumps({"state": state, "code": code}), flush=True)


async def run(config, lines=None):
    expiry = time.monotonic() + min(900, max(0, config["valid_seconds"]))
    stopped = asyncio.Event()
    peer = None
    capture = None
    peer_id = None

    async def renew():
        nonlocal expiry
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline) if lines is None else await lines()
            if not line:
                stopped.set()
                return
            try:
                state = json.loads(line)
                expiry = time.monotonic() + min(900, max(0, float(state["valid_seconds"])))
            except (ValueError, KeyError, TypeError):
                stopped.set()
                return

    async def watchdog():
        while not stopped.is_set():
            if time.monotonic() >= expiry:
                stopped.set()
                return
            await asyncio.sleep(0.2)

    reader, guard = asyncio.create_task(renew()), asyncio.create_task(watchdog())
    try:

        async def connect():
            nonlocal peer, capture, peer_id
            capture = await asyncio.to_thread(Capture, config)
            peer = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**value) for value in config.get("ice_servers") or []]))

            def ready():
                if peer.connectionState == "connected" and getattr(capture, "frame_received", True):
                    emit("connected")

            capture.on_frame = ready

            @capture.on("ended")
            def ended():
                if not stopped.is_set():
                    emit("failed", "CAPTURE_ENDED")
                stopped.set()

            @peer.on("connectionstatechange")
            async def changed():
                if peer.connectionState == "connected":
                    ready()
                elif peer.connectionState in {"failed", "closed"}:
                    stopped.set()

            peer.addTransceiver(capture, direction="sendonly")
            codec = "video/H264" if config["track"] == "video" else "audio/opus"
            peer.getTransceivers()[0].setCodecPreferences(
                [c for c in RTCRtpSender.getCapabilities(config["track"]).codecs if c.mimeType == codec]
            )
            await peer.setLocalDescription(await peer.createOffer())
            answer = await asyncio.to_thread(post, config, config["offer_path"], {"sdp": peer.localDescription.sdp})
            peer_id = answer["peer_id"]
            if stopped.is_set():
                return
            await peer.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))

        attempt = asyncio.create_task(connect())
        stop_task = asyncio.create_task(stopped.wait())
        done, _ = await asyncio.wait([attempt, stop_task], timeout=25, return_when=asyncio.FIRST_COMPLETED)
        if attempt in done:
            await attempt
            await stopped.wait()
        else:
            attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
        stop_task.cancel()
    except Exception as exc:  # noqa: BLE001 - process boundary must redact native URLs and credentials.
        emit("failed", str(exc) if isinstance(exc, CaptureError) else "MEDIA_CAPTURE_OR_NETWORK_FAILED")
    finally:
        reader.cancel()
        guard.cancel()
        if peer:
            await peer.close()
        if capture:
            capture.stop()
            # Avoid concurrent close while a native read is blocked. Parent Job ends
            # the short-lived process; no worker survives the authorization scope.
        if peer_id:
            # Local resources are already closed; the server also expires this peer.
            with suppress(OSError, ValueError, KeyError, CaptureError):
                await asyncio.to_thread(post, config, "/api/v1/classroom/device/media/close", {"peer_id": peer_id})


def main():
    try:
        config = json.loads(sys.stdin.buffer.readline(262145))
        asyncio.run(run(config))
    except Exception:  # noqa: BLE001 - never expose a native exception at the process boundary.
        emit("failed", "MEDIA_START_FAILED")
    finally:
        # Python's executor may be blocked in a native camera read after EOF.
        # The dedicated process owns all capture handles; exit releases them.
        import os

        os._exit(0)


if __name__ == "__main__":
    main()
