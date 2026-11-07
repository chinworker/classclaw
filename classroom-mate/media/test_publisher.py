import asyncio
import importlib.util
import io
import pathlib
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("publisher", pathlib.Path(__file__).with_name("publisher.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ProtocolTests(unittest.TestCase):
    def test_local_preview_stays_open_across_viewer_join_and_leave(self):
        async def exercise():
            inputs = asyncio.Queue()
            started = asyncio.Event()
            ended = asyncio.Event()

            class SyntheticCapture:
                started = last_frame = time.monotonic()
                error = None
                def stop(self):
                    pass

            async def publish(grant, capture):
                self.assertEqual(grant["key"], "camera:1:video")
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    ended.set()

            with patch.object(publisher, "Capture", return_value=SyntheticCapture()) as capture, patch.object(publisher, "publish", publish):
                output = io.StringIO()
                with redirect_stdout(output):
                    worker = asyncio.create_task(publisher.run({"preview": True, "track": "video",
                        "camera": {"source_kind": "windows_device"}}, inputs.get))
                    await asyncio.sleep(0.15)
                    self.assertEqual(capture.call_count, 1)
                    await inputs.put(b'{"preview":true,"valid_seconds":10,"publish":{"key":"camera:1:video"}}')
                    await asyncio.wait_for(started.wait(), timeout=2)
                    await inputs.put(b'{"preview":true}')
                    await asyncio.wait_for(ended.wait(), timeout=2)
                    self.assertFalse(worker.done())
                    await inputs.put(b"")
                    await asyncio.wait_for(worker, timeout=2)
                self.assertEqual(capture.call_count, 1)
                self.assertNotIn('"state": "failed"', output.getvalue())

        asyncio.run(exercise())

    def test_preview_is_a_small_jpeg_of_the_existing_frame(self):
        frame = publisher.av.VideoFrame(640, 360, "yuv420p")
        frame.pts = 5
        encoder = publisher.Preview()
        jpeg = encoder.encode(frame)
        self.assertTrue(encoder.encode(frame).startswith(b"\xff\xd8"))
        self.assertEqual(frame.pts, 5)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        with publisher.av.open(io.BytesIO(jpeg), format="mjpeg") as image:
            decoded = next(image.decode(video=0))
            self.assertEqual((decoded.width, decoded.height), (320, 180))

    def test_directshow_retries_native_format_and_unique_friendly_name(self):
        config = {"camera": {"source_kind": "windows_device"}, "track": "video", "native_device": "@device_pnp_cam",
                  "friendly_device": "USB camera"}
        with patch.object(publisher.av, "formats_available", {"dshow"}), patch.object(publisher.av, "open") as open_av:
            fallback = object()
            error = publisher.av.error.FFmpegError(5, "test")
            open_av.side_effect = [error, error, error, fallback]
            self.assertIs(publisher.open_capture(config, publisher.QUALITY[1080]), fallback)
            calls = open_av.call_args_list
            self.assertEqual(calls[0].kwargs["options"]["video_size"], "1920x1080")
            self.assertNotIn("video_size", calls[2].kwargs["options"])
            self.assertEqual(calls[3].args[0], "video=USB camera")

    def test_video_quality_bounds_preserve_aspect_ratio_without_upscaling(self):
        self.assertEqual(publisher.fit(publisher.av.VideoFrame(640, 480), 1920, 1080), (640, 480))
        self.assertEqual(publisher.fit(publisher.av.VideoFrame(1920, 1080), 1280, 720), (1280, 720))
        self.assertEqual(publisher.fit(publisher.av.VideoFrame(1920, 1080), 854, 480), (852, 480))

    def test_endpoint_never_exfiltrates_device_credential(self):
        cfg = {"server": "https://class.example.com"}
        self.assertEqual(
            publisher.endpoint(cfg, "/api/v1/classroom/device/media/close"), "https://class.example.com/api/v1/classroom/device/media/close"
        )
        for path in [
            "//attacker.example/offer",
            "/api/v1/classroom/device/media/../../admin",
            "/api/v1/classroom/device/media/close?key=x",
        ]:
            with self.assertRaises(publisher.CaptureError):
                publisher.endpoint(cfg, path)
        with self.assertRaises(publisher.CaptureError):
            publisher.endpoint({"server": "http://class.example.com"}, "/api/v1/classroom/device/media/close")

    def test_dshow_opens_only_authorized_track(self):
        camera = {"source_kind": "windows_device"}
        video = publisher.source({"camera": camera, "track": "video", "native_device": "@device_pnp_cam"})
        audio = publisher.source({"camera": camera, "track": "audio", "native_device": "@device_cm_mic"})
        self.assertEqual(video[0], "video=@device_pnp_cam")
        self.assertEqual(audio[0], "audio=@device_cm_mic")
        self.assertNotIn("audio", video[0])
        self.assertNotIn("video_size", audio[2])

    def test_network_rtsp_filters_audio_at_source(self):
        camera = {"source_kind": "network_stream", "source": {"protocol": "rtsp", "location": "rtsp://10.1.2.3/live"}}
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.1.2.3", 554))]):
            _, _, options = publisher.source({"camera": camera, "track": "video"})
        self.assertEqual(options["allowed_media_types"], "video")

    def test_network_forbids_local_and_metadata_targets(self):
        for host in ("127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1"):
            camera = {"source_kind": "network_stream", "source": {"protocol": "rtsp", "location": "rtsp://camera.example/live"}}
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (host, 554))]), self.assertRaises(publisher.CaptureError):
                publisher.source({"camera": camera, "track": "video"})

    def test_credentials_are_encoded_only_inside_memory(self):
        camera = {
            "source_kind": "network_stream",
            "source": {"protocol": "rtsp", "location": "rtsp://10.1.2.3/live", "username": "cam", "password": "a@b:c"},
        }
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.1.2.3", 554))]):
            url, _, _ = publisher.source({"camera": camera, "track": "video"})
        self.assertIn("cam:a%40b%3Ac@", url)


if __name__ == "__main__":
    unittest.main()
