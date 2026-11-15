import base64
import hashlib
import io
import time
import wave
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from recordings import live, views
from recordings.models import Device, Recording

RATE = 8000


def make_wav(seconds=2, rate=RATE, name="a.wav"):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * rate * seconds)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="audio/wav")


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(phone="09111111111")
        self.other = User.objects.create_user(phone="09222222222")
        self.c = APIClient()
        self.c.force_authenticate(self.user)

    def upload(self, client=None, **extra):
        data = {"audio": make_wav(), **extra}
        return (client or self.c).post("/api/recordings/", data, format="multipart")

    def tearDown(self):
        for r in Recording.objects.all():
            try:
                r.audio.delete(save=False)
            except PermissionError:   # ویندوز: فایل هنوز باز است؛ پوشهٔ موقت است و اهمیتی ندارد
                pass


class RecordingCreateTests(Base):
    def test_auth_required(self):
        r = APIClient().post("/api/recordings/", {"audio": make_wav()}, format="multipart")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(APIClient().get("/api/recordings/").status_code, 401)

    def test_upload_ok_duration_computed_by_server(self):
        r = self.upload(bpm=140, duration=9999)  # duration کلاینت نادیده گرفته می‌شود
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 2)
        self.assertEqual(r.data["bpm"], 140)
        self.assertNotIn("audio", r.data)
        self.assertIn("/api/recordings/", r.data["audio_url"])
        rec = Recording.objects.get()
        self.assertEqual(rec.user, self.user)
        self.assertIn(f"user_{self.user.id}", rec.audio.name)

    def test_waveform_saved(self):
        r = self.upload(waveform="[1,50,100]")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["waveform"], [1, 50, 100])

    def test_bad_extension(self):
        f = SimpleUploadedFile("a.txt", b"hello")
        r = self.c.post("/api/recordings/", {"audio": f}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_fake_audio_content_rejected(self):
        f = SimpleUploadedFile("a.mp3", b"this is not audio at all" * 100)
        r = self.c.post("/api/recordings/", {"audio": f}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_missing_audio(self):
        self.assertEqual(self.c.post("/api/recordings/", {"bpm": 100}, format="multipart").status_code, 400)

    def test_bpm_range(self):
        self.assertEqual(self.upload(bpm=29).status_code, 400)
        self.assertEqual(self.upload(bpm=301).status_code, 400)
        self.assertEqual(self.upload(bpm=30).status_code, 201)
        self.assertEqual(self.upload(bpm=300).status_code, 201)

    def test_waveform_validation(self):
        for bad in ["[101]", "[-1]", '["a"]', "[true]", "[1.5]", "{}", "[" + ",".join(["1"] * 129) + "]"]:
            self.assertEqual(self.upload(waveform=bad).status_code, 400, bad)

    def test_too_long_rejected(self):
        f = make_wav(seconds=601, rate=1000)
        r = self.c.post("/api/recordings/", {"audio": f}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_too_large_rejected(self):
        with mock.patch("recordings.serializers.MAX_SIZE", 100):
            r = self.upload()
        self.assertEqual(r.status_code, 400)

    def test_per_user_limit(self):
        with mock.patch.object(views, "MAX_RECORDINGS_PER_USER", 1):
            self.assertEqual(self.upload().status_code, 201)
            self.assertEqual(self.upload().status_code, 400)
            # کاربر دیگر محدود نشده
            oc = APIClient(); oc.force_authenticate(self.other)
            self.assertEqual(self.upload(client=oc).status_code, 201)


class RecordingReadDeleteTests(Base):
    def test_list_only_own_and_pagination(self):
        for _ in range(3):
            self.upload()
        oc = APIClient(); oc.force_authenticate(self.other)
        self.upload(client=oc)

        r = self.c.get("/api/recordings/")
        self.assertEqual(r.data["count"], 3)
        r = self.c.get("/api/recordings/?limit=2")
        self.assertEqual(len(r.data["results"]), 2)
        self.assertIsNotNone(r.data["next"])
        r = self.c.get("/api/recordings/?limit=1000")  # سقف ۱۰۰
        self.assertEqual(len(r.data["results"]), 3)

    def test_newest_first(self):
        a = self.upload().data["id"]
        b = self.upload().data["id"]
        ids = [x["id"] for x in self.c.get("/api/recordings/").data["results"]]
        self.assertEqual(ids, [b, a])

    def test_detail_isolated_between_users(self):
        rid = self.upload().data["id"]
        self.assertEqual(self.c.get(f"/api/recordings/{rid}/").status_code, 200)
        oc = APIClient(); oc.force_authenticate(self.other)
        self.assertEqual(oc.get(f"/api/recordings/{rid}/").status_code, 404)
        self.assertEqual(oc.delete(f"/api/recordings/{rid}/").status_code, 404)
        self.assertEqual(oc.get(f"/api/recordings/{rid}/audio/").status_code, 404)

    def test_put_patch_not_allowed(self):
        rid = self.upload().data["id"]
        self.assertEqual(self.c.patch(f"/api/recordings/{rid}/", {"bpm": 90}, format="json").status_code, 405)

    def test_delete_removes_row_and_file(self):
        rid = self.upload().data["id"]
        path = Path(Recording.objects.get(pk=rid).audio.path)
        self.assertTrue(path.exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.c.delete(f"/api/recordings/{rid}/").status_code, 204)
        self.assertFalse(Recording.objects.filter(pk=rid).exists())
        self.assertFalse(path.exists())


class AudioStreamTests(Base):
    def setUp(self):
        super().setUp()
        rid = self.upload().data["id"]
        self.url = f"/api/recordings/{rid}/audio/"
        self.size = Recording.objects.get(pk=rid).audio.size

    def body(self, r):
        return b"".join(r.streaming_content)

    def test_full(self):
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Accept-Ranges"], "bytes")
        self.assertEqual(len(self.body(r)), self.size)   # اول بخوان تا فایل بسته شود (ویندوز)
        self.assertIn(r["Content-Type"], ("audio/wav", "audio/x-wav", "audio/vnd.wave"))

    def test_range_partial(self):
        r = self.c.get(self.url, HTTP_RANGE="bytes=0-99")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r["Content-Range"], f"bytes 0-99/{self.size}")
        self.assertEqual(len(self.body(r)), 100)

    def test_range_open_ended(self):
        r = self.c.get(self.url, HTTP_RANGE="bytes=100-")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(self.body(r)), self.size - 100)

    def test_range_suffix(self):
        r = self.c.get(self.url, HTTP_RANGE="bytes=-50")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(self.body(r)), 50)

    def test_range_end_clamped(self):
        r = self.c.get(self.url, HTTP_RANGE=f"bytes=10-{self.size * 10}")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(self.body(r)), self.size - 10)

    def test_range_unsatisfiable(self):
        r = self.c.get(self.url, HTTP_RANGE=f"bytes={self.size + 10}-")
        self.assertEqual(r.status_code, 416)
        self.assertEqual(r["Content-Range"], f"bytes */{self.size}")

    def test_garbage_range_falls_back_to_full(self):
        r = self.c.get(self.url, HTTP_RANGE="bytes=abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.body(r)), self.size)

    def test_requires_auth(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_missing_file_is_404(self):
        Path(Recording.objects.get().audio.path).unlink()
        self.assertEqual(self.c.get(self.url).status_code, 404)


class DeviceIngestTests(Base):
    KEY = "secret-key"

    def setUp(self):
        super().setUp()
        self.dev = Device.objects.create(
            user=self.user, serial="SN1", sample_rate=RATE,
            key_hash=hashlib.sha256(self.KEY.encode()).hexdigest())
        self.hdr = {"HTTP_X_DEVICE_SERIAL": "SN1", "HTTP_X_DEVICE_KEY": self.KEY}

    def chunk(self, seq, data=b"\x01\x00" * 100, bpm=None, **h):
        hdr = {**self.hdr, "HTTP_X_SEQ": str(seq), **h}
        if bpm is not None:
            hdr["HTTP_X_BPM"] = str(bpm)
        return APIClient().post("/api/device/chunks/", data, content_type="application/octet-stream", **hdr)

    def test_bad_credentials(self):
        r = APIClient().post("/api/device/chunks/", b"xx", content_type="application/octet-stream",
                             HTTP_X_DEVICE_SERIAL="SN1", HTTP_X_DEVICE_KEY="wrong", HTTP_X_SEQ="1")
        self.assertEqual(r.status_code, 401)
        r = APIClient().post("/api/device/chunks/", b"xx", content_type="application/octet-stream",
                             HTTP_X_DEVICE_SERIAL="NOPE", HTTP_X_DEVICE_KEY=self.KEY, HTTP_X_SEQ="1")
        self.assertEqual(r.status_code, 401)

    def test_accepts_chunk(self):
        self.assertEqual(self.chunk(1).status_code, 204)

    def test_empty_and_oversize_rejected(self):
        self.assertEqual(self.chunk(1, data=b"").status_code, 400)
        self.assertEqual(self.chunk(1, data=b"x" * (live.MAX_CHUNK + 1)).status_code, 400)

    def test_missing_or_bad_seq(self):
        r = APIClient().post("/api/device/chunks/", b"xx", content_type="application/octet-stream", **self.hdr)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.chunk("abc").status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(APIClient().get("/api/device/chunks/", **self.hdr).status_code, 405)


class LiveFlowTests(DeviceIngestTests):
    def test_live_requires_auth_and_device(self):
        self.assertEqual(APIClient().get("/api/live/").status_code, 401)
        oc = APIClient(); oc.force_authenticate(self.other)
        self.assertEqual(oc.get("/api/live/").status_code, 400)

    def test_offline_when_no_data(self):
        r = self.c.get("/api/live/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["online"])
        self.assertFalse(r.data["can_record"])

    def test_online_returns_chunks_and_bpm(self):
        self.chunk(1, data=b"AAAA", bpm=140)
        self.chunk(2, data=b"BBBB")
        r = self.c.get("/api/live/?after=0")
        self.assertTrue(r.data["online"])
        self.assertEqual(r.data["last_seq"], 2)
        self.assertEqual([base64.b64decode(x) for x in r.data["chunks"]], [b"AAAA", b"BBBB"])
        self.assertEqual(r.data["bpm"], 140)
        # فقط تکه‌های جدید
        r = self.c.get("/api/live/?after=1")
        self.assertEqual([base64.b64decode(x) for x in r.data["chunks"]], [b"BBBB"])

    def test_invalid_bpm_header_ignored(self):
        self.chunk(1, bpm=999)
        self.assertIsNone(self.c.get("/api/live/").data["bpm"])

    def test_goes_offline_after_window(self):
        self.chunk(1)
        with mock.patch("recordings.live.time.time", return_value=time.time() + live.ONLINE_WINDOW + 5):
            self.assertFalse(self.c.get("/api/live/").data["online"])

    def test_record_start_needs_device_online_and_listening(self):
        r = self.c.post("/api/live/record/start/")
        self.assertEqual(r.status_code, 400)           # دستگاه آفلاین
        self.chunk(1)
        r = self.c.post("/api/live/record/start/")
        self.assertEqual(r.status_code, 400)           # هنوز پخش شروع نشده
        self.c.get("/api/live/")                        # شروع پخش
        r = self.c.post("/api/live/record/start/")
        self.assertEqual(r.status_code, 200)

    def test_stop_without_start(self):
        self.assertEqual(self.c.post("/api/live/record/stop/").status_code, 400)

    def test_full_record_flow_creates_recording(self):
        self.chunk(1)
        self.c.get("/api/live/")
        self.assertEqual(self.c.post("/api/live/record/start/").status_code, 200)

        one_sec = b"\x01\x00" * RATE
        self.chunk(2, data=one_sec, bpm=100)
        self.chunk(3, data=one_sec, bpm=120)
        self.assertTrue(self.c.get("/api/live/").data["recording"])

        r = self.c.post("/api/live/record/stop/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 2)
        self.assertEqual(r.data["bpm"], 110)

        rec = Recording.objects.get(pk=r.data["id"])
        self.assertEqual(rec.user, self.user)
        with wave.open(rec.audio.path) as w:
            self.assertEqual(w.getframerate(), RATE)
            self.assertEqual(w.getnframes(), RATE * 2)
        self.dev.refresh_from_db()
        self.assertIsNone(self.dev.recording_since)
        self.assertFalse(live.pcm_path(self.dev).exists())

    def test_too_short_recording_rejected_and_state_kept(self):
        self.chunk(1)
        self.c.get("/api/live/")
        self.c.post("/api/live/record/start/")
        self.chunk(2, data=b"\x01\x00" * 10)
        r = self.c.post("/api/live/record/stop/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Recording.objects.count(), 0)
        self.dev.refresh_from_db()
        self.assertIsNotNone(self.dev.recording_since)   # هنوز در حال ضبط است
