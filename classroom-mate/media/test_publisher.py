import importlib.util
import io
import pathlib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("publisher", pathlib.Path(__file__).with_name("publisher.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class ProtocolTests(unittest.TestCase):
    def test_preview_is_a_small_jpeg_of_the_existing_frame(self):
        frame = publisher.av.VideoFrame(640, 360, "yuv420p")
        frame.pts = 5
        jpeg = publisher.preview_jpeg(frame)
        self.assertEqual(frame.pts, 5)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        with publisher.av.open(io.BytesIO(jpeg), format="mjpeg") as image:
            decoded = next(image.decode(video=0))
            self.assertEqual((decoded.width, decoded.height), (320, 180))

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
