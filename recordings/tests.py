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
        r = self.upload(duration=9999)  # duration کلاینت نادیده گرفته می‌شود
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 2)
        self.assertNotIn("bpm", r.data)
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
        self.assertEqual(self.c.post("/api/recordings/", {"waveform": "[]"}, format="multipart").status_code, 400)

    def test_waveform_validation(self):
        for bad in ["[101]", "[-1]", '["a"]', "[true]", "[1.5]", "{}", "[" + ",".join(["1"] * 129) + "]"]:
            self.assertEqual(self.upload(waveform=bad).status_code, 400, bad)

    def test_too_long_rejected(self):
        f = make_wav(seconds=121, rate=1000)          # سقف ۲ دقیقه
        r = self.c.post("/api/recordings/", {"audio": f}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_two_minutes_accepted(self):
        f = make_wav(seconds=120, rate=1000)
        r = self.c.post("/api/recordings/", {"audio": f}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 120)

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

    def test_put_not_allowed_and_patch_only_favorite(self):
        rid = self.upload().data["id"]
        self.assertEqual(self.c.put(f"/api/recordings/{rid}/", {"bpm": 90}, format="json").status_code, 405)
        # PATCH فقط is_favorite را عوض می‌کند (برای علاقه‌مندی)؛ duration ثابت می‌ماند
        self.assertEqual(self.c.patch(f"/api/recordings/{rid}/", {"duration": 90}, format="json").status_code, 200)
        self.assertEqual(self.c.get(f"/api/recordings/{rid}/").data["duration"], 2)

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

    def chunk(self, seq, data=b"\x01\x00" * 100, **h):
        hdr = {**self.hdr, "HTTP_X_SEQ": str(seq), **h}
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

    def test_online_returns_chunks(self):
        self.chunk(1, data=b"AAAA")
        self.chunk(2, data=b"BBBB")
        r = self.c.get("/api/live/?after=0")
        self.assertTrue(r.data["online"])
        self.assertEqual(r.data["last_seq"], 2)
        self.assertEqual([base64.b64decode(x) for x in r.data["chunks"]], [b"AAAA", b"BBBB"])
        self.assertNotIn("bpm", r.data)
        self.assertEqual(r.data["max_record_seconds"], 120)
        # فقط تکه‌های جدید
        r = self.c.get("/api/live/?after=1")
        self.assertEqual([base64.b64decode(x) for x in r.data["chunks"]], [b"BBBB"])

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
        self.chunk(2, data=one_sec)
        self.chunk(3, data=one_sec)
        self.assertTrue(self.c.get("/api/live/").data["recording"])

        r = self.c.post("/api/live/record/stop/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 2)
        self.assertNotIn("bpm", r.data)

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


class PhoneIngestTests(DeviceIngestTests):
    """رلهٔ گوشی با JWT کاربر (بدون کلید دستگاه)"""

    def phone_chunk(self, seq, client=None, data=b"\x01\x00" * 100):
        hdr = {"HTTP_X_SEQ": str(seq)}
        return (client or self.c).post(
            "/api/live/ingest/", data, content_type="application/octet-stream", **hdr)

    def test_requires_login(self):
        self.assertEqual(self.phone_chunk(1, client=APIClient()).status_code, 401)

    def test_device_key_headers_not_enough(self):
        r = APIClient().post("/api/live/ingest/", b"xx", content_type="application/octet-stream",
                             HTTP_X_SEQ="1", **self.hdr)
        self.assertEqual(r.status_code, 401)

    def test_user_without_device_rejected(self):
        c = APIClient()
        c.force_authenticate(self.other)
        self.assertEqual(self.phone_chunk(1, client=c).status_code, 400)

    def test_bad_input(self):
        self.assertEqual(self.phone_chunk(1, data=b"").status_code, 400)
        self.assertEqual(self.phone_chunk("abc").status_code, 400)

    def test_phone_chunks_reach_live_and_record(self):
        self.assertEqual(self.phone_chunk(1).status_code, 204)
        r = self.c.get("/api/live/")
        self.assertTrue(r.data["online"])
        self.assertEqual(len(r.data["chunks"]), 1)
        self.assertEqual(self.c.post("/api/live/record/start/").status_code, 200)
        one_sec = b"\x01\x00" * RATE
        self.phone_chunk(2, data=one_sec)
        self.phone_chunk(3, data=one_sec)
        self.assertEqual(self.c.post("/api/live/record/stop/").status_code, 201)


class DeviceRegistrationTests(Base):
    def test_requires_login(self):
        self.assertEqual(APIClient().get("/api/device/").status_code, 401)

    def test_register_get_and_replace(self):
        self.assertIsNone(self.c.get("/api/device/").data["device"])
        r = self.c.post("/api/device/", {"serial": "FD640-1", "sample_rate": 8000}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.c.get("/api/device/").data["device"]["serial"], "FD640-1")
        self.c.post("/api/device/", {"serial": "FD640-2"}, format="json")
        self.assertEqual(self.user.devices.count(), 1)
        self.assertEqual(self.user.devices.first().serial, "FD640-2")

    def test_validation_and_foreign_device(self):
        self.assertEqual(self.c.post("/api/device/", {"serial": "bad serial!"}, format="json").status_code, 400)
        self.assertEqual(self.c.post("/api/device/", {"serial": "A1", "sample_rate": 5}, format="json").status_code, 400)
        Device.objects.create(user=self.other, serial="MINE", key_hash="x")
        self.assertEqual(self.c.post("/api/device/", {"serial": "MINE"}, format="json").status_code, 400)

    def test_unlink(self):
        self.c.post("/api/device/", {"serial": "A1"}, format="json")
        self.assertEqual(self.c.delete("/api/device/").status_code, 204)
        self.assertIsNone(self.c.get("/api/device/").data["device"])


class LiveExtrasTests(DeviceIngestTests):
    def test_levels_rate_and_recording_seconds(self):
        loud = (32767).to_bytes(2, "little", signed=True) * 100
        self.assertEqual(self.chunk(1, data=loud).status_code, 204)
        self.assertEqual(self.chunk(2, data=b"\x00\x00" * 100).status_code, 204)
        r = self.c.get("/api/live/").data
        self.assertEqual(r["levels"], [100, 0])
        self.assertEqual(len(r["levels"]), len(r["chunks"]))
        self.assertEqual(r["sample_rate"], RATE)
        self.assertEqual(r["recording_seconds"], 0)
        self.c.post("/api/live/record/start/")
        self.chunk(3, data=b"\x01\x00" * RATE)
        self.assertEqual(self.c.get("/api/live/").data["recording_seconds"], 1)

    def test_stop_saves_pregnancy_week(self):
        from datetime import timedelta
        from django.utils import timezone
        self.user.due_date = timezone.localdate() + timedelta(weeks=12)   # الان هفتهٔ ۲۸
        self.user.save()
        self.chunk(1)
        self.c.get("/api/live/")
        self.c.post("/api/live/record/start/")
        self.chunk(2, data=b"\x01\x00" * RATE * 2)
        r = self.c.post("/api/live/record/stop/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["pregnancy_week"], 28)
        self.assertEqual(self.c.get(f"/api/recordings/{r.data['id']}/").data["pregnancy_week"], 28)


class ProfileAndFavoriteTests(Base):
    def test_profile_week(self):
        from datetime import timedelta
        from django.utils import timezone
        r = self.c.get("/api/me/")
        self.assertIsNone(r.data["pregnancy_week"])
        due = (timezone.localdate() + timedelta(weeks=20)).isoformat()
        r = self.c.patch("/api/me/", {"name": "مریم", "due_date": due}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["pregnancy_week"], 20)
        self.assertEqual(self.c.patch("/api/me/", {"due_date": "2001-01-01"}, format="json").status_code, 400)
        self.assertEqual(self.c.patch("/api/me/", {"phone": "09000000000"}, format="json").data["phone"], "09111111111")
        self.assertEqual(APIClient().get("/api/me/").status_code, 401)

    def test_upload_snapshots_week(self):
        from datetime import timedelta
        from django.utils import timezone
        self.user.due_date = timezone.localdate() + timedelta(weeks=10)
        self.user.save()
        self.assertEqual(self.upload().data["pregnancy_week"], 30)

    def test_favorite_toggle_filter_and_ownership(self):
        a = self.upload().data["id"]
        b = self.upload().data["id"]
        r = self.c.patch(f"/api/recordings/{a}/", {"is_favorite": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["is_favorite"])
        self.assertIn("audio_url", r.data)
        ids = [x["id"] for x in self.c.get("/api/recordings/?favorite=true").data["results"]]
        self.assertEqual(ids, [a])
        self.assertEqual(len(self.c.get("/api/recordings/").data["results"]), 2)
        # فیلدهای دیگر با PATCH تغییر نمی‌کنند
        self.c.patch(f"/api/recordings/{b}/", {"duration": 1}, format="json")
        self.assertEqual(self.c.get(f"/api/recordings/{b}/").data["duration"], 2)
        # ضبط دیگران
        o = APIClient()
        o.force_authenticate(self.other)
        self.assertEqual(o.patch(f"/api/recordings/{a}/", {"is_favorite": False}, format="json").status_code, 404)


class AlbumNoteAndFilterTests(Base):
    def test_title_note_default_empty_and_patchable(self):
        rid = self.upload().data["id"]
        r = self.c.get(f"/api/recordings/{rid}/")
        self.assertEqual((r.data["title"], r.data["note"]), ("", ""))
        r = self.c.patch(f"/api/recordings/{rid}/",
                         {"title": "  اولین ضربان  ", "note": "امروز برای اولین بار شنیدیمش"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["title"], "اولین ضربان")
        self.assertEqual(self.c.get(f"/api/recordings/{rid}/").data["note"], "امروز برای اولین بار شنیدیمش")

    def test_title_note_limits_and_clearing(self):
        rid = self.upload().data["id"]
        url = f"/api/recordings/{rid}/"
        self.assertEqual(self.c.patch(url, {"title": "x" * 101}, format="json").status_code, 400)
        self.assertEqual(self.c.patch(url, {"note": "x" * 1001}, format="json").status_code, 400)
        self.c.patch(url, {"note": "سلام"}, format="json")
        self.assertEqual(self.c.patch(url, {"note": ""}, format="json").data["note"], "")

    def test_note_on_other_users_recording_is_404(self):
        rid = self.upload().data["id"]
        o = APIClient()
        o.force_authenticate(self.other)
        self.assertEqual(o.patch(f"/api/recordings/{rid}/", {"note": "x"}, format="json").status_code, 404)

    def test_can_set_title_and_note_on_upload(self):
        r = self.upload(title="هفتهٔ اول", note="یادداشت")
        self.assertEqual((r.data["title"], r.data["note"]), ("هفتهٔ اول", "یادداشت"))

    def _make(self, week, days_ago, **kw):
        from datetime import timedelta
        from django.utils import timezone
        rid = self.upload().data["id"]
        Recording.objects.filter(pk=rid).update(
            pregnancy_week=week, created_at=timezone.now() - timedelta(days=days_ago), **kw)
        return rid

    def test_filter_by_week(self):
        a = self._make(20, 5)
        self._make(21, 4)
        ids = [x["id"] for x in self.c.get("/api/recordings/?week=20").data["results"]]
        self.assertEqual(ids, [a])
        self.assertEqual(self.c.get("/api/recordings/?week=abc").status_code, 400)
        self.assertEqual(self.c.get("/api/recordings/?week=99").status_code, 400)

    def test_filter_by_date_range(self):
        from datetime import timedelta
        from django.utils import timezone
        old = self._make(10, 30)
        new = self._make(12, 1)
        today = timezone.localdate()
        r = self.c.get(f"/api/recordings/?from={today - timedelta(days=5)}")
        self.assertEqual([x["id"] for x in r.data["results"]], [new])
        r = self.c.get(f"/api/recordings/?to={today - timedelta(days=10)}")
        self.assertEqual([x["id"] for x in r.data["results"]], [old])
        self.assertEqual(self.c.get("/api/recordings/?from=1405-01-01x").status_code, 400)

    def test_search_in_title_and_note(self):
        a = self._make(10, 3, title="لالایی")
        b = self._make(11, 2, note="صدای قلب خیلی قوی بود")
        self._make(12, 1)
        ids = lambda q: sorted(x["id"] for x in self.c.get(f"/api/recordings/?q={q}").data["results"])
        self.assertEqual(ids("لالایی"), [a])
        self.assertEqual(ids("قوی"), [b])
        self.assertEqual(ids("ناموجود"), [])

    def test_filters_combine_and_stay_isolated(self):
        a = self._make(20, 3)
        self._make(20, 3, is_favorite=False)
        self.c.patch(f"/api/recordings/{a}/", {"is_favorite": True}, format="json")
        o = APIClient()
        o.force_authenticate(self.other)
        self.upload(client=o)
        Recording.objects.filter(user=self.other).update(pregnancy_week=20)
        r = self.c.get("/api/recordings/?week=20&favorite=true")
        self.assertEqual([x["id"] for x in r.data["results"]], [a])


class AlbumWeeksTests(Base):
    URL = "/api/album/weeks/"

    def _make(self, week, user=None, **kw):
        user = user or self.user
        client = self.c if user == self.user else APIClient()
        if user != self.user:
            client.force_authenticate(user)
        rid = self.upload(client=client).data["id"]
        Recording.objects.filter(pk=rid).update(pregnancy_week=week, **kw)
        return rid

    def test_requires_auth(self):
        self.assertEqual(APIClient().get(self.URL).status_code, 401)

    def test_empty_album(self):
        r = self.c.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"total": 0, "weeks": []})

    def test_groups_counts_favorites_duration(self):
        self._make(20, is_favorite=True)
        self._make(20)
        self._make(22)
        r = self.c.get(self.URL)
        self.assertEqual(r.data["total"], 3)
        w22, w20 = r.data["weeks"]                  # جدیدترین هفته اول
        self.assertEqual((w22["week"], w22["count"], w22["favorites"]), (22, 1, 0))
        self.assertEqual((w20["week"], w20["count"], w20["favorites"]), (20, 2, 1))
        self.assertEqual(w20["total_duration"], 4)   # هر ضبط تست ۲ ثانیه است
        self.assertIsNotNone(w20["last_recorded_at"])

    def test_week_null_goes_last(self):
        self._make(None)
        self._make(5)
        self._make(30)
        weeks = [w["week"] for w in self.c.get(self.URL).data["weeks"]]
        self.assertEqual(weeks, [30, 5, None])

    def test_last_recorded_at_is_newest(self):
        from datetime import timedelta
        from django.utils import timezone
        old = timezone.now() - timedelta(days=9)
        self._make(12, created_at=old)
        new_id = self._make(12)
        newest = Recording.objects.get(pk=new_id).created_at
        got = self.c.get(self.URL).data["weeks"][0]["last_recorded_at"]
        self.assertEqual(got.replace(microsecond=0), newest.replace(microsecond=0))

    def test_isolated_between_users(self):
        self._make(20)
        self._make(20, user=self.other)
        self._make(21, user=self.other)
        r = self.c.get(self.URL)
        self.assertEqual(r.data["total"], 1)
        self.assertEqual([w["week"] for w in r.data["weeks"]], [20])

    def test_matches_week_filter_counts(self):
        for _ in range(3):
            self._make(25)
        n = self.c.get(self.URL).data["weeks"][0]["count"]
        self.assertEqual(n, self.c.get("/api/recordings/?week=25").data["count"])


PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def upload_file(name, data, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class MemoryBase(Base):
    def tearDown(self):
        from recordings.models import Memory
        for m in Memory.objects.all():
            try:
                m.file.delete(save=False)
            except PermissionError:
                pass
        super().tearDown()

    def add(self, kind, f, client=None, **extra):
        return (client or self.c).post("/api/memories/", {"kind": kind, "file": f, **extra}, format="multipart")

    def photo(self, **extra):
        return self.add("photo", upload_file("a.png", PNG_1x1), **extra)

    def other_client(self):
        o = APIClient()
        o.force_authenticate(self.other)
        return o


class MemoryCreateTests(MemoryBase):
    def test_auth_required(self):
        self.assertEqual(APIClient().post("/api/memories/", {}, format="multipart").status_code, 401)
        self.assertEqual(APIClient().get("/api/memories/").status_code, 401)

    def test_photo_ok(self):
        r = self.photo(title="شکم هفتهٔ ۲۰", note="اولین عکس")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["kind"], r.data["title"], r.data["note"]), ("photo", "شکم هفتهٔ ۲۰", "اولین عکس"))
        self.assertIsNone(r.data["duration"])
        self.assertEqual(r.data["size"], len(PNG_1x1))
        self.assertIn("file_url", r.data)
        self.assertNotIn("file", r.data)

    def test_jpeg_and_webp_and_heic_accepted(self):
        self.assertEqual(self.add("photo", upload_file("a.jpg", JPEG_BYTES)).status_code, 201)
        webp = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 16
        self.assertEqual(self.add("photo", upload_file("a.webp", webp)).status_code, 201)
        heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
        self.assertEqual(self.add("photo", upload_file("a.heic", heic)).status_code, 201)

    def test_video_ok(self):
        r = self.add("video", upload_file("v.mp4", MP4_BYTES))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["kind"], "video")

    def test_music_ok_duration_from_file(self):
        r = self.add("music", make_wav(seconds=3, name="m.wav"))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["duration"], 3)

    def test_week_snapshot(self):
        from datetime import timedelta
        from django.utils import timezone
        self.user.due_date = timezone.localdate() + timedelta(weeks=10)
        self.user.save()
        self.assertEqual(self.photo().data["pregnancy_week"], 30)

    def test_bad_kind_and_missing_fields(self):
        self.assertEqual(self.add("gif", upload_file("a.png", PNG_1x1)).status_code, 400)
        self.assertEqual(self.c.post("/api/memories/", {"file": upload_file("a.png", PNG_1x1)},
                                     format="multipart").status_code, 400)
        self.assertEqual(self.c.post("/api/memories/", {"kind": "photo"}, format="multipart").status_code, 400)

    def test_extension_must_match_kind(self):
        self.assertEqual(self.add("photo", make_wav(name="a.wav")).status_code, 400)
        self.assertEqual(self.add("music", upload_file("a.png", PNG_1x1)).status_code, 400)
        self.assertEqual(self.add("video", upload_file("a.png", PNG_1x1)).status_code, 400)

    def test_fake_content_rejected(self):
        self.assertEqual(self.add("photo", upload_file("a.png", b"not an image at all")).status_code, 400)
        self.assertEqual(self.add("video", upload_file("a.mp4", b"not a video at all!!")).status_code, 400)
        self.assertEqual(self.add("music", upload_file("a.mp3", b"not audio at all")).status_code, 400)

    def test_empty_file_rejected(self):
        self.assertEqual(self.add("photo", upload_file("a.png", b"")).status_code, 400)

    def test_size_limit(self):
        from recordings import memories
        with mock.patch.dict(memories.RULES["photo"], {"max_size": 10}):
            self.assertEqual(self.photo().status_code, 400)

    def test_music_too_long(self):
        from recordings import memories
        with mock.patch.dict(memories.RULES["music"], {"max_duration": 1}):
            self.assertEqual(self.add("music", make_wav(seconds=3, name="m.wav")).status_code, 400)

    def test_per_user_limit(self):
        from recordings import memories
        with mock.patch.object(memories, "MAX_MEMORIES_PER_USER", 2):
            self.assertEqual(self.photo().status_code, 201)
            self.assertEqual(self.photo().status_code, 201)
            self.assertEqual(self.photo().status_code, 400)
            self.assertEqual(self.add("photo", upload_file("a.png", PNG_1x1), client=self.other_client()).status_code, 201)


class MemoryReadEditDeleteTests(MemoryBase):
    def test_list_newest_first_only_own_and_paginated(self):
        a = self.photo().data["id"]
        b = self.photo().data["id"]
        self.add("photo", upload_file("a.png", PNG_1x1), client=self.other_client())
        r = self.c.get("/api/memories/")
        self.assertEqual(r.data["count"], 2)
        self.assertEqual([x["id"] for x in r.data["results"]], [b, a])
        self.assertEqual(len(self.c.get("/api/memories/?limit=1").data["results"]), 1)

    def test_filter_by_kind(self):
        p = self.photo().data["id"]
        m = self.add("music", make_wav(name="m.wav")).data["id"]
        ids = lambda q: [x["id"] for x in self.c.get(f"/api/memories/{q}").data["results"]]
        self.assertEqual(ids("?kind=photo"), [p])
        self.assertEqual(ids("?kind=music"), [m])
        self.assertEqual(ids("?kind=video"), [])
        self.assertEqual(self.c.get("/api/memories/?kind=zzz").status_code, 400)

    def test_shared_filters_week_date_search_favorite(self):
        from datetime import timedelta
        from django.utils import timezone
        from recordings.models import Memory
        a = self.photo(title="لالایی").data["id"]
        b = self.photo(note="صدای قلب").data["id"]
        Memory.objects.filter(pk=a).update(pregnancy_week=20, created_at=timezone.now() - timedelta(days=30))
        Memory.objects.filter(pk=b).update(pregnancy_week=21)
        self.c.patch(f"/api/memories/{b}/", {"is_favorite": True}, format="json")
        ids = lambda q: [x["id"] for x in self.c.get(f"/api/memories/{q}").data["results"]]
        self.assertEqual(ids("?week=20"), [a])
        self.assertEqual(ids("?q=قلب"), [b])
        self.assertEqual(ids("?favorite=true"), [b])
        self.assertEqual(ids(f"?from={timezone.localdate() - timedelta(days=5)}"), [b])
        self.assertEqual(self.c.get("/api/memories/?week=abc").status_code, 400)

    def test_patch_only_allowed_fields(self):
        rid = self.photo().data["id"]
        r = self.c.patch(f"/api/memories/{rid}/",
                         {"title": " عکس اول ", "note": "یادداشت", "is_favorite": True,
                          "kind": "video", "size": 5}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["title"], r.data["note"], r.data["is_favorite"]), ("عکس اول", "یادداشت", True))
        self.assertEqual((r.data["kind"], r.data["size"]), ("photo", len(PNG_1x1)))
        self.assertEqual(self.c.patch(f"/api/memories/{rid}/", {"title": "x" * 101}, format="json").status_code, 400)
        self.assertEqual(self.c.put(f"/api/memories/{rid}/", {}, format="json").status_code, 405)

    def test_isolation(self):
        rid = self.photo().data["id"]
        o = self.other_client()
        self.assertEqual(o.get(f"/api/memories/{rid}/").status_code, 404)
        self.assertEqual(o.patch(f"/api/memories/{rid}/", {"note": "x"}, format="json").status_code, 404)
        self.assertEqual(o.delete(f"/api/memories/{rid}/").status_code, 404)
        self.assertEqual(o.get(f"/api/memories/{rid}/file/").status_code, 404)

    def test_delete_removes_row_and_file(self):
        from recordings.models import Memory
        rid = self.photo().data["id"]
        path = Memory.objects.get(pk=rid).file.path
        self.assertTrue(Path(path).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.c.delete(f"/api/memories/{rid}/").status_code, 204)
        self.assertFalse(Memory.objects.filter(pk=rid).exists())
        self.assertFalse(Path(path).exists())

    def test_summary_counts(self):
        self.photo()
        self.photo()
        self.add("music", make_wav(name="m.wav"))
        self.add("photo", upload_file("a.png", PNG_1x1), client=self.other_client())
        self.assertEqual(self.c.get("/api/memories/summary/").data, {"photo": 2, "video": 0, "music": 1})
        self.assertEqual(APIClient().get("/api/memories/summary/").status_code, 401)


class MemoryFileTests(MemoryBase):
    def test_full_and_headers(self):
        rid = self.photo().data["id"]
        r = self.c.get(f"/api/memories/{rid}/file/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), PNG_1x1)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Accept-Ranges"], "bytes")

    def test_range(self):
        rid = self.photo().data["id"]
        r = self.c.get(f"/api/memories/{rid}/file/", HTTP_RANGE="bytes=0-9")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(b"".join(r.streaming_content), PNG_1x1[:10])
        self.assertEqual(r["Content-Range"], f"bytes 0-9/{len(PNG_1x1)}")
        r = self.c.get(f"/api/memories/{rid}/file/", HTTP_RANGE="bytes=9999-")
        self.assertEqual(r.status_code, 416)

    def test_requires_auth_and_missing_file_404(self):
        from recordings.models import Memory
        rid = self.photo().data["id"]
        self.assertEqual(APIClient().get(f"/api/memories/{rid}/file/").status_code, 401)
        Path(Memory.objects.get(pk=rid).file.path).unlink()
        self.assertEqual(self.c.get(f"/api/memories/{rid}/file/").status_code, 404)


class UnifiedAlbumTests(MemoryBase):
    URL = "/api/album/"

    def _stamp(self, obj_model, pk, days_ago, **fields):
        from datetime import timedelta
        from django.utils import timezone
        obj_model.objects.filter(pk=pk).update(created_at=timezone.now() - timedelta(days=days_ago), **fields)

    def _seed(self):
        """audio(۴ روز پیش) · photo(۳) · music(۲) · video(۱) — هفته‌ها ۲۰ تا ۲۳"""
        from recordings.models import Memory
        a = self.upload().data["id"]
        p = self.photo(title="عکس").data["id"]
        m = self.add("music", make_wav(name="m.wav"), note="لالایی").data["id"]
        v = self.add("video", upload_file("v.mp4", MP4_BYTES)).data["id"]
        self._stamp(Recording, a, 4, pregnancy_week=20)
        self._stamp(Memory, p, 3, pregnancy_week=21)
        self._stamp(Memory, m, 2, pregnancy_week=22)
        self._stamp(Memory, v, 1, pregnancy_week=23)
        return a, p, m, v

    def kinds(self, q=""):
        r = self.c.get(self.URL + q)
        self.assertEqual(r.status_code, 200, r.data)
        return [x["kind"] for x in r.data["results"]]

    def test_auth_required(self):
        self.assertEqual(APIClient().get(self.URL).status_code, 401)

    def test_empty(self):
        r = self.c.get(self.URL)
        self.assertEqual((r.data["count"], r.data["results"]), (0, []))

    def test_mixed_newest_first(self):
        self._seed()
        self.assertEqual(self.kinds(), ["video", "music", "photo", "audio"])

    def test_item_shape_and_links(self):
        a, p, m, v = self._seed()
        items = {x["kind"]: x for x in self.c.get(self.URL).data["results"]}
        audio = items["audio"]
        self.assertEqual(audio["id"], a)
        self.assertTrue(audio["url"].endswith(f"/api/recordings/{a}/audio/"))
        self.assertTrue(audio["detail_url"].endswith(f"/api/recordings/{a}/"))
        self.assertIn("waveform", audio)
        photo = items["photo"]
        self.assertEqual(photo["title"], "عکس")
        self.assertTrue(photo["url"].endswith(f"/api/memories/{p}/file/"))
        self.assertTrue(photo["detail_url"].endswith(f"/api/memories/{p}/"))
        self.assertEqual(photo["size"], len(PNG_1x1))
        for x in items.values():
            self.assertNotIn("audio", x)
            self.assertNotIn("file", x)
            self.assertIn("pregnancy_week", x)

    def test_filter_by_kind(self):
        self._seed()
        self.assertEqual(self.kinds("?kind=audio"), ["audio"])
        self.assertEqual(self.kinds("?kind=photo"), ["photo"])
        self.assertEqual(self.kinds("?kind=music"), ["music"])
        self.assertEqual(self.kinds("?kind=video"), ["video"])
        self.assertEqual(self.c.get(self.URL + "?kind=zzz").status_code, 400)

    def test_filter_by_week_search_favorite_and_dates(self):
        from datetime import timedelta
        from django.utils import timezone
        a, p, m, v = self._seed()
        self.assertEqual(self.kinds("?week=22"), ["music"])
        self.assertEqual(self.kinds("?week=10"), [])
        self.assertEqual(self.kinds("?q=لالایی"), ["music"])
        self.c.patch(f"/api/recordings/{a}/", {"is_favorite": True}, format="json")
        self.c.patch(f"/api/memories/{p}/", {"is_favorite": True}, format="json")
        self.assertEqual(self.kinds("?favorite=true"), ["photo", "audio"])
        since = timezone.localdate() - timedelta(days=2)
        self.assertEqual(self.kinds(f"?from={since}"), ["video", "music"])
        self.assertEqual(self.c.get(self.URL + "?week=abc").status_code, 400)
        self.assertEqual(self.c.get(self.URL + "?from=غلط").status_code, 400)

    def test_pagination_across_both_tables(self):
        self._seed()
        first = self.c.get(self.URL + "?limit=3")
        self.assertEqual(first.data["count"], 4)
        self.assertEqual([x["kind"] for x in first.data["results"]], ["video", "music", "photo"])
        self.assertIsNotNone(first.data["next"])
        second = self.c.get(self.URL + "?limit=3&offset=3")
        self.assertEqual([x["kind"] for x in second.data["results"]], ["audio"])
        self.assertIsNone(second.data["next"])

    def test_isolated_between_users(self):
        self._seed()
        o = self.other_client()
        self.upload(client=o)
        self.add("photo", upload_file("a.png", PNG_1x1), client=o)
        self.assertEqual(self.c.get(self.URL).data["count"], 4)
        self.assertEqual(o.get(self.URL).data["count"], 2)

    def test_detail_url_roundtrip(self):
        self._seed()
        for item in self.c.get(self.URL).data["results"]:
            path = item["detail_url"].split("testserver")[-1]
            self.assertEqual(self.c.get(path).status_code, 200, path)
            self.assertEqual(self.c.get(path).data["id"], item["id"])


class UnifiedWeeksTests(MemoryBase):
    URL = "/api/album/weeks/"

    def test_weeks_count_all_kinds(self):
        from recordings.models import Memory
        a = self.upload().data["id"]
        p1 = self.photo().data["id"]
        p2 = self.photo().data["id"]
        m = self.add("music", make_wav(seconds=3, name="m.wav")).data["id"]
        v = self.add("video", upload_file("v.mp4", MP4_BYTES)).data["id"]
        Recording.objects.filter(pk=a).update(pregnancy_week=20, is_favorite=True)
        Memory.objects.filter(pk__in=[p1, m]).update(pregnancy_week=20)
        Memory.objects.filter(pk=p2).update(pregnancy_week=22, is_favorite=True)
        Memory.objects.filter(pk=v).update(pregnancy_week=None)
        r = self.c.get(self.URL)
        self.assertEqual(r.data["total"], 5)
        self.assertEqual([w["week"] for w in r.data["weeks"]], [22, 20, None])
        w22, w20, wnull = r.data["weeks"]
        self.assertEqual(w20["count"], 3)
        self.assertEqual(w20["kinds"], {"audio": 1, "photo": 1, "video": 0, "music": 1})
        self.assertEqual(w20["favorites"], 1)
        self.assertEqual(w20["total_duration"], 2 + 3)          # صدا ۲ ثانیه + موسیقی ۳ ثانیه؛ عکس مدت ندارد
        self.assertEqual((w22["count"], w22["favorites"], w22["kinds"]["photo"]), (1, 1, 1))
        self.assertEqual(wnull["kinds"]["video"], 1)

    def test_last_activity_is_newest_across_kinds(self):
        from datetime import timedelta
        from django.utils import timezone
        from recordings.models import Memory
        a = self.upload().data["id"]
        p = self.photo().data["id"]
        Recording.objects.filter(pk=a).update(pregnancy_week=20, created_at=timezone.now() - timedelta(days=5))
        Memory.objects.filter(pk=p).update(pregnancy_week=20)
        newest = Memory.objects.get(pk=p).created_at
        got = self.c.get(self.URL).data["weeks"][0]["last_recorded_at"]
        self.assertEqual(got.replace(microsecond=0), newest.replace(microsecond=0))

    def test_isolation_and_auth(self):
        self.photo()
        self.assertEqual(self.other_client().get(self.URL).data, {"total": 0, "weeks": []})
        self.assertEqual(APIClient().get(self.URL).status_code, 401)
