"""Opt-in real MediaMTX/WebRTC smoke test with synthetic video and silence.

Uses only temporary listeners/configuration; never opens business databases.
Requires aiortc in the test Python environment, MediaMTX v1.17.0 and FFmpeg.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services import classroom_sources
from app.services import classroom_streaming as media


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Auth(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        accepted = media.verify_capability(payload)
        self.send_response(204 if accepted else 401)
        self.end_headers()

    def log_message(self, *_args):
        pass


async def check():
    from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription, VideoStreamTrack

    clients = []
    await media.recover()
    try:
        for track in ("video", "audio"):
            publisher = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            clients.append(publisher)
            publisher.addTrack(VideoStreamTrack() if track == "video" else AudioStreamTrack())
            codecs = RTCRtpSender.getCapabilities(track).codecs
            preferred = "video/H264" if track == "video" else "audio/opus"
            publisher.getTransceivers()[0].setCodecPreferences([codec for codec in codecs if codec.mimeType == preferred])
            await publisher.setLocalDescription(await publisher.createOffer())
            answer = await media.offer("smoke-test", 1, track, publisher.localDescription.sdp, device_id="test-device")
            await publisher.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))
            for _ in range(50):
                if publisher.connectionState == "connected":
                    break
                await asyncio.sleep(0.1)
            assert publisher.connectionState == "connected", publisher.connectionState

            receiver = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            clients.append(receiver)
            receiver.addTransceiver(track, direction="recvonly")
            received = asyncio.get_running_loop().create_future()

            @receiver.on("track")
            def on_track(remote_track, pending=received):
                if not pending.done():
                    pending.set_result(remote_track)

            await receiver.setLocalDescription(await receiver.createOffer())
            reply = await media.offer("smoke-test", 1, track, receiver.localDescription.sdp, lease_id=f"viewer-{track}")
            await receiver.setRemoteDescription(RTCSessionDescription(reply["sdp"], "answer"))
            remote = await asyncio.wait_for(received, 10)
            frame = await asyncio.wait_for(remote.recv(), 10)
            assert frame is not None
            print(f"PASS: WHIP publish -> authenticated {track} path -> WHEP receive -> decoded frame", flush=True)
            await media.close(reply["peer_id"])
            assert reply["peer_id"] not in media._peers
            print(f"PASS: {track} remote session DELETE", flush=True)
        try:
            frame = await classroom_sources.snapshot("smoke-test", 1)
        except Exception:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "3000000",
                "-i", classroom_sources.local_url("smoke-test", 1, "video", "read", 30),
                "-frames:v", "1", "-f", "null", "-", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            print(re.sub(r"rtsp://[^\s]+", "rtsp://[redacted]", err.decode(errors="replace"))[:2000], flush=True)
            raise
        assert frame.startswith(b"\xff\xd8")
        print(f"PASS: FFmpeg single-frame in-memory snapshot ({len(frame)} bytes)", flush=True)
        # Exercise the real server-direct encoder with a synthetic RTSP source.
        # Only source discovery is substituted, so no business database is opened.
        original_wanted = classroom_sources._wanted
        source_url = classroom_sources.local_url("smoke-test", 1, "video", "read", 60)
        classroom_sources._wanted = lambda: [("smoke-relay", 1, False, source_url)]
        try:
            await classroom_sources.reconcile_sources()
            deadline = asyncio.get_running_loop().time() + 25
            while True:
                try:
                    relayed = await classroom_sources.snapshot("smoke-relay", 1)
                    break
                except Exception:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise
                    await asyncio.sleep(0.5)
            assert relayed.startswith(b"\xff\xd8")
            print("PASS: server-direct RTSP -> FFmpeg H.264 -> authenticated RTSP publish -> snapshot", flush=True)
        finally:
            classroom_sources._wanted = original_wanted
            await classroom_sources.stop_sources()
        # A changed signed path/action cannot request an unauthorized track.
        token = media.capability("read", "classclaw/smoke-test/1/video")
        assert not media.verify_capability({"token": token, "action": "read", "path": "classclaw/smoke-test/1/audio"})
        print("PASS: capability cannot cross video/audio paths", flush=True)
    finally:
        for client in clients:
            await client.close()
        for peer_id in list(media._peers):
            await media.close(peer_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mediamtx", required=True)
    args = parser.parse_args()
    auth = ThreadingHTTPServer(("127.0.0.1", 0), Auth)
    thread = threading.Thread(target=auth.serve_forever, daemon=True)
    thread.start()
    api_port, signal_port, rtsp_port, ice_port = port(), port(), port(), port()
    configured = replace(settings, classroom=settings.classroom.model_copy(update={
        "media_provider": "mediamtx", "media_base_url": f"http://127.0.0.1:{signal_port}",
        "media_api_url": f"http://127.0.0.1:{api_port}", "media_rtsp_url": f"rtsp://127.0.0.1:{rtsp_port}"}))
    media.settings = classroom_sources.settings = configured
    with tempfile.TemporaryDirectory(prefix="classclaw-media-smoke-") as directory:
        config = Path(directory) / "mediamtx.yml"
        config.write_text(f"""logLevel: warn
api: true
apiAddress: 127.0.0.1:{api_port}
authMethod: http
authHTTPAddress: http://127.0.0.1:{auth.server_port}/auth
authHTTPExclude:
  - action: api
rtsp: true
rtspAddress: 127.0.0.1:{rtsp_port}
rtspTransports: [tcp]
rtmp: false
hls: false
srt: false
webrtc: true
webrtcAddress: 127.0.0.1:{signal_port}
webrtcLocalUDPAddress: 127.0.0.1:{ice_port}
webrtcAdditionalHosts: [127.0.0.1]
webrtcICEServers2: []
paths:
  all_others:
""")
        with (Path(directory) / "media.log").open("w") as log:
            proc = subprocess.Popen([args.mediamtx, str(config)], stdout=log, stderr=log)
            try:
                for _ in range(50):
                    try:
                        with socket.create_connection(("127.0.0.1", api_port), timeout=0.1):
                            break
                    except OSError:
                        if proc.poll() is not None:
                            raise RuntimeError("MediaMTX failed to start") from None
                        time.sleep(0.1)
                asyncio.run(check())
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                auth.shutdown()
                auth.server_close()


if __name__ == "__main__":
    main()
