"""One authorized media track per child; credentials only arrive over inherited stdin.

The parent owns a kill-on-close Windows Job. EOF, expiry and disconnect also stop
capture here. Never print exceptions/SDP/URLs/credentials from native libraries.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from contextlib import contextmanager, nullcontext, suppress
from fractions import Fraction

import av
from aiortc import MediaStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCRtpSender, RTCSessionDescription, rtcrtpsender
from aiortc.codecs import CODECS
from aiortc.codecs.h264 import H264Encoder

logging.disable(logging.CRITICAL)
av.logging.set_level(av.logging.PANIC)


QUALITY = {360: (640, 360, 20, 650000), 480: (854, 480, 24, 1000000),
           720: (1280, 720, 30, 1800000), 1080: (1920, 1080, 30, 3000000)}
VIDEO_CLOCK = Fraction(1, 90000)
OUTPUT_LOCK = threading.Lock()


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
    proxy = urllib.request.ProxyHandler({}) if request.type == "http" else urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(proxy, NoRedirect())
    try:
        with opener.open(request, timeout=12) as response:
            value = json.loads(response.read(131072))
    except urllib.error.HTTPError as exc:
        raise CaptureError(f"MEDIA_SIGNAL_HTTP_{exc.code}") from exc
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
        options = {"rtbufsize": "16000000"} if track == "video" else {}
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


class Capture:
    """The same thread opens, reads and closes the native capture device."""

    def __init__(self, config, preview):
        self.config = config
        self.kind = config["track"]
        self.profile = QUALITY.get(config.get("quality"), QUALITY[720])
        self.preview = preview
        self.frames = deque(maxlen=1 if self.kind == "video" else 12)
        self.changed = threading.Condition()
        self.closed = False
        self.error = None
        self.last_frame = None
        self.started = time.monotonic()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            with open_capture(self.config, self.profile) as container:
                streams = container.streams.video if self.kind == "video" else container.streams.audio
                if not streams:
                    raise CaptureError("CAPTURE_TRACK_UNAVAILABLE")
                first_pts = None
                last_sent = 0.0
                width, height, fps, _ = self.profile
                for frame in container.decode(streams[0]):
                    if self.closed:
                        return
                    current = time.monotonic()
                    if self.kind == "video":
                        if current - last_sent < 0.9 / fps:
                            continue
                        last_sent = current
                        size = fit(frame, width, height)
                        frame = frame.reformat(width=size[0], height=size[1], format="yuv420p")
                        frame.time_base = VIDEO_CLOCK
                        frame.pts = round((current - self.started) / VIDEO_CLOCK)
                    elif frame.pts is not None:
                        if first_pts is None:
                            first_pts = frame.pts
                        frame.pts -= first_pts
                    with self.changed:
                        self.last_frame = current
                        self.frames.append(frame)
                        self.changed.notify_all()
                    if self.kind == "video":
                        self.preview(frame, current)
                raise CaptureError("CAPTURE_ENDED")
        except Exception as exc:  # noqa: BLE001 - native messages may contain camera locations and credentials.
            with self.changed:
                self.error = str(exc) if isinstance(exc, CaptureError) else "CAPTURE_READ_FAILED"
                self.changed.notify_all()

    def next_frame(self):
        with self.changed:
            self.changed.wait_for(lambda: self.frames or self.error or self.closed, timeout=8)
            if self.error or self.closed or not self.frames:
                raise CaptureError(self.error or "CAPTURE_STALLED")
            return self.frames.popleft()

    def stop(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()


def fit(frame, width, height):
    scale = min(1, width / frame.width, height / frame.height)
    return tuple(max(2, int(size * scale) // 2 * 2) for size in (frame.width, frame.height))


def open_capture(config, profile):
    location, format_name, options = source(config)
    if format_name != "dshow":
        return av.open(location, format=format_name, options=options, timeout=(5, 6))
    names = [location]
    friendly = config.get("friendly_device")
    if friendly and not any(char in friendly for char in (":", '"', "\n", "\r")):
        names.append(f"{config['track']}={friendly}")
    width, height, fps, _ = profile
    modes = [{**options, "video_size": f"{width}x{height}", "framerate": str(fps)},
             {**options, "video_size": f"{width}x{height}"}, options] if config["track"] == "video" else [options]
    if format_name not in av.formats_available:
        raise CaptureError("CAPTURE_DSHOW_UNAVAILABLE")
    for name in names:
        for mode in modes:
            try:
                return av.open(name, format=format_name, options=mode, timeout=(3, 6))
            except av.error.PermissionError as exc:
                raise CaptureError("CAPTURE_PERMISSION_DENIED") from exc
            except av.error.FFmpegError:
                continue
    raise CaptureError("CAPTURE_OPEN_FAILED")


class PublishingTrack(MediaStreamTrack):
    def __init__(self, capture, ready):
        super().__init__()
        self.kind = capture.kind
        self.capture = capture
        self.ready = ready

    async def recv(self):
        frame = await asyncio.to_thread(self.capture.next_frame)
        self.ready()
        return frame


class VideoEncoder(H264Encoder):
    """Use aiortc's RTP packetization, bitrate feedback and keyframe requests."""

    def __init__(self, profile):
        super().__init__()
        self.fps, self.limit = profile[2:]
        self.level = "4.0" if profile[1] > 720 else "3.1"
        self.target_bitrate = self.limit

    def _encode_frame(self, frame, force_keyframe):
        bitrate = min(self.target_bitrate, self.limit)
        if self.codec is None or (self.codec.width, self.codec.height) != (frame.width, frame.height) or (
            abs(bitrate - self.codec.bit_rate) > self.codec.bit_rate * 0.25
        ):
            codec = av.CodecContext.create("libx264", "w")
            codec.width, codec.height = frame.width, frame.height
            codec.bit_rate = bitrate
            codec.pix_fmt = "yuv420p"
            codec.framerate = Fraction(self.fps)
            codec.time_base = VIDEO_CLOCK
            codec.gop_size = self.fps * 2
            codec.max_b_frames = 0
            codec.profile = "Baseline"
            codec.options = {"preset": "veryfast", "tune": "zerolatency", "level": self.level,
                             "x264-params": f"repeat-headers=1:vbv-maxrate={bitrate // 1000}:vbv-bufsize={bitrate // 1000}"}
            self.codec = codec
        frame.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        data = b"".join(bytes(packet) for packet in self.codec.encode(frame))
        yield from self._split_bitstream(data)


@contextmanager
def video_encoding(profile):
    """A worker process has one publishing track. The pinned aiortc factory also serves its retransmissions."""
    default = rtcrtpsender.get_encoder
    codecs = [codec for codec in CODECS["video"] if codec.mimeType == "video/H264"]
    previous = [codec.parameters["profile-level-id"] for codec in codecs]
    level = "28" if profile[1] > 720 else "1f"
    try:
        for codec, value in zip(codecs, previous):
            codec.parameters["profile-level-id"] = value[:-2] + level
        rtcrtpsender.get_encoder = lambda codec: VideoEncoder(profile) if codec.mimeType == "video/H264" else default(codec)
        yield
    finally:
        rtcrtpsender.get_encoder = default
        for codec, value in zip(codecs, previous):
            codec.parameters["profile-level-id"] = value


def output(line):
    with OUTPUT_LOCK:
        print(line, flush=True)


def emit(state, code=""):
    output(json.dumps({"state": state, "code": code}))


class Preview:
    """Small JPEGs for the Windows dashboard, generated from the local capture thread."""

    def __init__(self):
        self.codec = None
        self.last = 0.0
        self.until = 0.0
        self.pts = 0

    def __call__(self, frame, current):
        if current >= self.until or current - self.last < 1 / 12:
            return
        self.last = current
        with suppress(av.error.FFmpegError, OSError, ValueError):
            jpeg = self.encode(frame)
            if 0 < len(jpeg) <= 48000 and time.monotonic() < self.until:
                output("preview:" + base64.b64encode(jpeg).decode("ascii"))

    def encode(self, frame):
        width, height = fit(frame, 320, 240)
        if self.codec is None or (self.codec.width, self.codec.height) != (width, height):
            codec = av.CodecContext.create("mjpeg", "w")
            codec.width, codec.height = width, height
            codec.pix_fmt, codec.time_base = "yuvj420p", Fraction(1, 12)
            self.codec = codec
        image = frame.reformat(width=width, height=height, format=self.codec.pix_fmt)
        if image is frame:
            image = frame.reformat(format="yuv420p").reformat(format=self.codec.pix_fmt)
        self.pts += 1
        image.pts, image.time_base = self.pts, self.codec.time_base
        return b"".join(bytes(packet) for packet in self.codec.encode(image))


async def publish(config, capture):
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(**v) for v in config.get("ice_servers") or []]))
    peer_id = None
    connected = asyncio.Event()
    ended = asyncio.Event()
    try:
        def frame_sent():
            if peer.connectionState == "connected":
                connected.set()

        @peer.on("connectionstatechange")
        async def changed():
            if peer.connectionState in {"failed", "closed"}:
                ended.set()

        track = capture.kind
        publisher = PublishingTrack(capture, frame_sent)
        publisher.on("ended", ended.set)
        peer.addTransceiver(publisher, direction="sendonly")
        codec = "video/H264" if track == "video" else "audio/opus"
        peer.getTransceivers()[0].setCodecPreferences([c for c in RTCRtpSender.getCapabilities(track).codecs if c.mimeType == codec])
        try:
            async with asyncio.timeout(20):
                await peer.setLocalDescription(await peer.createOffer())
                answer = await asyncio.to_thread(post, config, config["offer_path"], {"sdp": peer.localDescription.sdp})
                peer_id = answer["peer_id"]
                await peer.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))
                while not connected.is_set():
                    if ended.is_set():
                        raise CaptureError("MEDIA_SENDER_FAILED" if publisher.readyState == "ended" else "MEDIA_ICE_FAILED")
                    await asyncio.sleep(0.1)
        except TimeoutError as exc:
            raise CaptureError("MEDIA_CONNECT_TIMEOUT") from exc
        emit("connected")
        await ended.wait()
        raise CaptureError("MEDIA_SENDER_FAILED" if publisher.readyState == "ended" else "MEDIA_ICE_FAILED")
    finally:
        await peer.close()
        if peer_id:
            with suppress(OSError, ValueError, KeyError, CaptureError, TimeoutError):
                await asyncio.wait_for(asyncio.to_thread(
                    post, config, "/api/v1/classroom/device/media/close", {"peer_id": peer_id}
                ), 2)


async def run(config, lines=None):
    preview = Preview()
    capture = None
    stream = None
    state = config
    received_at = time.monotonic()
    eof = False
    video = config["track"] == "video"
    if config.get("preview") and (not video or config["camera"]["source_kind"] != "windows_device"):
        raise CaptureError("LOCAL_PREVIEW_VIDEO_ONLY")

    async def renew():
        nonlocal state, received_at, eof
        try:
            while line := (await asyncio.to_thread(sys.stdin.buffer.readline, 262145) if lines is None else await lines()):
                state = json.loads(line)
                received_at = time.monotonic()
        finally:
            eof = True
            preview.until = 0

    async def stop_stream(task):
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    reader = asyncio.create_task(renew())
    profile = QUALITY.get(config.get("quality"), QUALITY[720])
    active_key, retry_at, retry_delay = None, 0, 1
    try:
        with video_encoding(profile) if video else nullcontext():
            while not eof:
                current = time.monotonic()
                preview.until = received_at + 3 if state.get("preview") else 0
                grant = state.get("publish") if current < received_at + min(900, max(0, state.get("valid_seconds", 0))) else None
                if not grant and current >= preview.until:
                    break
                if capture is None:
                    capture = Capture(config, preview)
                if capture.error:
                    raise CaptureError(capture.error)
                if current - (capture.last_frame or capture.started) > (8 if capture.last_frame else 25):
                    raise CaptureError("CAPTURE_STALLED")
                key = grant.get("key") if grant else None
                if key != active_key:
                    await stop_stream(stream)
                    stream, active_key, retry_at, retry_delay = None, key, 0, 1
                    emit("starting" if key else "idle")
                if stream and stream.done():
                    error = stream.exception()
                    emit("retrying", str(error) if isinstance(error, CaptureError) else "MEDIA_SIGNAL_FAILED")
                    stream = None
                    retry_at, retry_delay = current + retry_delay, min(12, retry_delay * 2)
                if key and stream is None and capture.last_frame and current >= retry_at:
                    stream = asyncio.create_task(publish(grant, capture))
                await asyncio.sleep(0.1)
    except Exception as exc:  # noqa: BLE001 - never expose native errors, credentials or device names.
        emit("failed", str(exc) if isinstance(exc, CaptureError) else "MEDIA_CAPTURE_FAILED")
    finally:
        preview.until = 0
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        await stop_stream(stream)
        if capture:
            capture.stop()


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
