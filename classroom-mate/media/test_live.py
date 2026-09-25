"""Optional real helper -> ClassClaw-style JSON signaling -> MediaMTX -> decode.

Only acquisition is synthetic; no classroom hardware or business database access.
Run with the repository and aiortc/httpx installed, plus --mediamtx /path/to/binary.
"""

import argparse
import asyncio
import importlib.util
import json
import socket
import sys
import tempfile
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack

from app.config import settings
from app.core.errors import AppError
from app.services import classroom_streaming as media

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).with_name("publisher.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def check(binary):
    loop = asyncio.get_running_loop()

    async def dispatch(path, payload):
        if path.endswith("/close"):
            await media.close(payload["peer_id"])
            return {"closed": True}
        track = path.split("/")[-2]
        return await media.offer("test-camera", 1, track, payload["sdp"], device_id="test-device")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/auth":
                self.send_response(204 if media.verify_capability(payload) else 401)
                self.end_headers()
                return
            if (
                self.headers.get("Authorization") != "Bearer synthetic-test-credential"
                or self.headers.get("X-ClassClaw-Device-ID") != "test-device"
            ):
                self.send_response(401)
                self.end_headers()
                return
            result = asyncio.run_coroutine_threadsafe(dispatch(self.path, payload), loop).result(timeout=20)
            encoded = json.dumps({"success": True, "data": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api, signal, rtsp, ice = port(), port(), port(), port()
    media.settings = replace(
        settings,
        classroom=settings.classroom.model_copy(
            update={
                "media_provider": "mediamtx",
                "media_base_url": f"http://127.0.0.1:{signal}",
                "media_api_url": f"http://127.0.0.1:{api}",
                "media_rtsp_url": f"rtsp://127.0.0.1:{rtsp}",
            }
        ),
    )
    with tempfile.TemporaryDirectory(prefix="classroom-mate-live-") as directory:
        config = Path(directory) / "mediamtx.yml"
        config.write_text(f"""logLevel: warn
api: true
apiAddress: 127.0.0.1:{api}
authMethod: http
authHTTPAddress: http://127.0.0.1:{server.server_port}/auth
authHTTPExclude:
  - action: api
rtspAddress: 127.0.0.1:{rtsp}
rtspTransports: [tcp]
rtmp: false
hls: false
srt: false
webrtcAddress: 127.0.0.1:{signal}
webrtcLocalUDPAddress: 127.0.0.1:{ice}
webrtcAdditionalHosts: [127.0.0.1]
webrtcICEServers2: []
paths:
  all_others:
""")
        process = await asyncio.create_subprocess_exec(
            binary, str(config), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            for _ in range(100):
                try:
                    await media.recover()
                    break
                except AppError:
                    await asyncio.sleep(0.1)
            for track in ("video", "audio"):
                events = asyncio.Queue()
                inputs = asyncio.Queue()
                publisher.emit = lambda state, code="", target=events: target.put_nowait((state, code))
                publisher.Capture = lambda _config, kind=track: VideoStreamTrack() if kind == "video" else AudioStreamTrack()
                worker = asyncio.create_task(
                    publisher.run(
                        {
                            "server": f"http://127.0.0.1:{server.server_port}",
                            "development": True,
                            "device_id": "test-device",
                            "credential": "synthetic-test-credential",
                            "track": track,
                            "ice_servers": [],
                            "offer_path": f"/api/v1/classroom/device/media/test-camera/1/{track}/offer",
                            "valid_seconds": 30,
                        },
                        inputs.get,
                    )
                )
                receiver = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                try:
                    event = await asyncio.wait_for(events.get(), 20)
                    assert event[0] == "connected", event
                    for _ in range(50):
                        paths = await media.request("GET", f"http://127.0.0.1:{api}/v3/paths/list")
                        if any(
                            row.get("name") == f"classclaw/test-camera/1/{track}" and row.get("ready")
                            for row in paths.json().get("items", [])
                        ):
                            break
                        await asyncio.sleep(0.1)
                    receiver.addTransceiver(track, direction="recvonly")
                    future = asyncio.get_running_loop().create_future()

                    @receiver.on("track")
                    def on_track(remote, pending=future):
                        pending.set_result(remote)

                    await receiver.setLocalDescription(await receiver.createOffer())
                    result = await media.offer("test-camera", 1, track, receiver.localDescription.sdp, lease_id="test-viewer")
                    await receiver.setRemoteDescription(RTCSessionDescription(result["sdp"], "answer"))
                    remote = await asyncio.wait_for(future, 10)
                    assert await asyncio.wait_for(remote.recv(), 10) is not None
                    await inputs.put(b"")
                    await asyncio.wait_for(worker, 10)
                    assert not any(p.device_id for p in media._peers.values())
                    print(f"PASS helper {track}: JSON SDP, authenticated media, decode, EOF close", flush=True)
                finally:
                    await inputs.put(b"")
                    await asyncio.wait_for(worker, 10)
                    await receiver.close()
                    for peer_id in list(media._peers):
                        await media.close(peer_id)
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
            await asyncio.to_thread(server.shutdown)
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mediamtx", required=True)
    asyncio.run(check(parser.parse_args().mediamtx))
