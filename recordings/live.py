import hashlib, hmac, io, wave, base64
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Device, Recording

MAX_CHUNK = 64 * 1024
MAX_RECORD_SECONDS = 600
ONLINE_WINDOW = 3   # ثانیه


def pcm_path(device):
    p = Path(settings.MEDIA_ROOT) / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"dev_{device.id}.pcm"


def auth_device(request):
    serial = request.headers.get("X-Device-Serial", "")
    key = request.headers.get("X-Device-Key", "")
    try:
        d = Device.objects.get(serial=serial)
    except Device.DoesNotExist:
        return None
    digest = hashlib.sha256(key.encode()).hexdigest()
    return d if hmac.compare_digest(digest, d.key_hash) else None


class DeviceIngestView(APIView):
    """دستگاه: POST با بدنهٔ خام PCM. هدرها: X-Device-Serial, X-Device-Key, X-Seq, X-BPM"""
    authentication_classes = []
    permission_classes = []
    parser_classes = []
    throttle_classes = []

    def post(self, request):
        d = auth_device(request)
        if not d:
            return Response(status=401)
        data = request.body
        if not data or len(data) > MAX_CHUNK:
            return Response({"detail": "اندازهٔ داده معتبر نیست."}, status=400)
        try:
            seq = int(request.headers["X-Seq"])
        except (KeyError, ValueError):
            return Response({"detail": "X-Seq لازم است."}, status=400)

        cache.set(f"live:{d.id}:{seq}", data, 30)
        cache.set(f"live:{d.id}:last", seq, 30)
        cache.set(f"live:{d.id}:last_time", time.time(), 30)

        bpm = request.headers.get("X-BPM", "")
        if bpm.isdigit() and 30 <= int(bpm) <= 300:
            cache.set(f"live:{d.id}:bpm", int(bpm), 30)
            if d.recording_since:
                Device.objects.filter(pk=d.pk).update(
                    bpm_sum=F("bpm_sum") + int(bpm), bpm_count=F("bpm_count") + 1)

        if d.recording_since:
            max_bytes = MAX_RECORD_SECONDS * d.sample_rate * 2
            p = pcm_path(d)
            if not p.exists() or p.stat().st_size < max_bytes:
                with open(p, "ab") as f:
                    f.write(data)
        return Response(status=204)


def my_device(request):
    d = request.user.devices.first()
    if not d:
        raise ValidationError({"detail": "دستگاهی به حساب تو وصل نیست."})
    return d


class LiveView(APIView):
    """اپ: GET ?after=N → تکه‌های جدید (base64) برای پخش زنده"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        d = my_device(request)
        now = time.time()
        cache.set(f"live:{d.id}:listen_time", now, 30)

        last = cache.get(f"live:{d.id}:last")
        after = int(request.query_params.get("after", 0) or 0)
        last_time = cache.get(f"live:{d.id}:last_time")
        online = last is not None and last_time is not None and now - last_time <= ONLINE_WINDOW

        if not online:
            return Response({
                "online": False, "last_seq": after, "chunks": [],
                "bpm": None, "recording": d.recording_since is not None,
                "can_record": False,
            })

        start = max(after + 1, last - 49)       # حداکثر ۵۰ تکهٔ آخر
        chunks = []
        for s in range(start, last + 1):
            c = cache.get(f"live:{d.id}:{s}")
            if c:
                chunks.append(base64.b64encode(c).decode())
        return Response({
            "online": True, "last_seq": last, "chunks": chunks,
            "bpm": cache.get(f"live:{d.id}:bpm"),
            "recording": d.recording_since is not None,
            "can_record": True,
        })

 # ثانیه: اگر آخرین تکه قدیمی‌تر از این باشد، دستگاه آفلاین حساب می‌شود


class RecordStartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = my_device(request)
        now = time.time()

        last_time = cache.get(f"live:{d.id}:last_time")
        if last_time is None or now - last_time > ONLINE_WINDOW:
            raise ValidationError(
                {"detail": "دستگاه متصل نیست یا صدایی ارسال نمی‌کند."}
            )

        listen_time = cache.get(f"live:{d.id}:listen_time")
        if listen_time is None or now - listen_time > ONLINE_WINDOW:
            raise ValidationError(
                {"detail": "پخش صدا متوقف است. اول پخش را شروع کن، بعد ضبط کن."}
            )

        pcm_path(d).unlink(missing_ok=True)
        Device.objects.filter(pk=d.pk).update(
            recording_since=timezone.now(), bpm_sum=0, bpm_count=0)
        return Response({"recording": True})


class RecordStopView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = my_device(request)
        if not d.recording_since:
            raise ValidationError({"detail": "ضبطی در جریان نیست."})
        p = pcm_path(d)
        pcm = p.read_bytes() if p.exists() else b""

        seconds = len(pcm) // (d.sample_rate * 2)
        if seconds < 1:
            raise ValidationError({"detail": "ضبط خیلی کوتاه بود. دستگاه روشن است؟"})

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(d.sample_rate)
            w.writeframes(pcm)

        bpm = round(d.bpm_sum / d.bpm_count) if d.bpm_count else None
        rec = Recording.objects.create(
            user=request.user, bpm=bpm, duration=seconds,
            audio=ContentFile(buf.getvalue(), name="rec.wav"),
        )
        # فقط بعد از موفقیت
        Device.objects.filter(pk=d.pk).update(recording_since=None)
        p.unlink(missing_ok=True)
        return Response({"id": rec.id, "duration": seconds, "bpm": bpm}, status=201)