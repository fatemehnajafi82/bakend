import array, base64, hashlib, hmac, io, re, secrets, sys, time, wave
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
MAX_RECORD_SECONDS = 120   # حداکثر مدت ضبط: ۲ دقیقه
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


def chunk_level(data):
    """بلندی تکهٔ صوتی (PCM ۱۶بیتی) به‌صورت ۰ تا ۱۰۰ برای رسم شکل موج در اپ."""
    data = data[: len(data) // 2 * 2]
    if not data:
        return 0
    samples = array.array("h", data)
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(max(samples), -min(samples))
    return min(100, round(peak * 100 / 32768))


def record_seconds(d):
    """مدت ضبط جاری (ثانیه) از روی حجم واقعی دادهٔ ذخیره‌شده."""
    if not d.recording_since:
        return 0
    p = pcm_path(d)
    return p.stat().st_size // (d.sample_rate * 2) if p.exists() else 0


def store_chunk(d, data, seq):
    """منطق مشترک ذخیرهٔ تکهٔ صوتی (هم برای دستگاه، هم برای گوشی)."""
    cache.set(f"live:{d.id}:{seq}", data, 30)
    cache.set(f"live:{d.id}:last", seq, 30)
    cache.set(f"live:{d.id}:last_time", time.time(), 30)

    if d.recording_since:
        max_bytes = MAX_RECORD_SECONDS * d.sample_rate * 2
        p = pcm_path(d)
        if not p.exists() or p.stat().st_size < max_bytes:
            with open(p, "ab") as f:
                f.write(data)


def parse_chunk(request):
    """بدنهٔ خام و X-Seq را اعتبارسنجی می‌کند. خروجی: (data, seq) یا (None, Response خطا)."""
    data = request.body
    if not data or len(data) > MAX_CHUNK:
        return None, Response({"detail": "اندازهٔ داده معتبر نیست."}, status=400)
    try:
        seq = int(request.headers["X-Seq"])
    except (KeyError, ValueError):
        return None, Response({"detail": "X-Seq لازم است."}, status=400)
    return data, seq


class DeviceIngestView(APIView):
    """دستگاه مستقیم (WiFi/LTE): POST با بدنهٔ خام PCM. هدرها: X-Device-Serial, X-Device-Key, X-Seq"""
    authentication_classes = []
    permission_classes = []
    parser_classes = []
    throttle_classes = []

    def post(self, request):
        d = auth_device(request)
        if not d:
            return Response(status=401)
        data, seq = parse_chunk(request)
        if data is None:
            return seq
        store_chunk(d, data, seq)
        return Response(status=204)


class PhoneIngestView(APIView):
    """گوشی (رلهٔ بلوتوث): با JWT خود کاربر، بدون کلید دستگاه در اپ.
    بدنهٔ خام PCM؛ هدرها: Authorization: Bearer <access>, X-Seq"""
    permission_classes = [IsAuthenticated]
    parser_classes = []
    throttle_classes = []

    def post(self, request):
        d = my_device(request)          # فقط دستگاهِ متصل به همین کاربر
        data, seq = parse_chunk(request)
        if data is None:
            return seq
        store_chunk(d, data, seq)
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
                "online": False, "last_seq": after, "chunks": [], "levels": [],
                "recording": d.recording_since is not None,
                "recording_seconds": record_seconds(d),
                "sample_rate": d.sample_rate, "max_record_seconds": MAX_RECORD_SECONDS,
                "can_record": False,
            })

        start = max(after + 1, last - 49)       # حداکثر ۵۰ تکهٔ آخر
        chunks, levels = [], []
        for s in range(start, last + 1):
            c = cache.get(f"live:{d.id}:{s}")
            if c:
                chunks.append(base64.b64encode(c).decode())
                levels.append(chunk_level(c))     # هم‌اندازهٔ chunks؛ برای شکل موج زنده
        return Response({
            "online": True, "last_seq": last, "chunks": chunks, "levels": levels,
            "recording": d.recording_since is not None,
            "recording_seconds": record_seconds(d),
            "sample_rate": d.sample_rate,        # اپ برای پخش PCM لازم دارد (۱۶بیت، مونو)
            "max_record_seconds": MAX_RECORD_SECONDS,   # سقف ضبط؛ اپ در این مدت ضبط را متوقف کند
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
            recording_since=timezone.now())
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

        rec = Recording.objects.create(
            user=request.user, duration=seconds,
            pregnancy_week=request.user.pregnancy_week(),
            audio=ContentFile(buf.getvalue(), name="rec.wav"),
        )
        # فقط بعد از موفقیت
        Device.objects.filter(pk=d.pk).update(recording_since=None)
        p.unlink(missing_ok=True)
        return Response({"id": rec.id, "duration": seconds,
                         "pregnancy_week": rec.pregnancy_week}, status=201)


SERIAL_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class DeviceView(APIView):
    """ثبت و مدیریت دستگاه کاربر (هر کاربر یک دستگاه).
    GET: وضعیت · POST {serial, sample_rate?}: اتصال دستگاه · DELETE: جدا کردن"""
    permission_classes = [IsAuthenticated]

    @staticmethod
    def info(d):
        return {"serial": d.serial, "sample_rate": d.sample_rate} if d else None

    def get(self, request):
        return Response({"device": self.info(request.user.devices.first())})

    def post(self, request):
        serial = str(request.data.get("serial", "")).strip()
        if not SERIAL_RE.fullmatch(serial):
            raise ValidationError({"serial": "شمارهٔ سریال معتبر نیست."})
        try:
            rate = int(request.data.get("sample_rate", 16000))
        except (TypeError, ValueError):
            rate = 0
        if not 8000 <= rate <= 48000:
            raise ValidationError({"sample_rate": "نرخ نمونه‌برداری معتبر نیست."})

        d = Device.objects.filter(serial=serial).first()
        if d and d.user_id not in (None, request.user.id):
            raise ValidationError({"serial": "این دستگاه به حساب دیگری وصل است."})
        if d is None:
            d = Device(serial=serial,
                       key_hash=hashlib.sha256(secrets.token_hex(16).encode()).hexdigest())
        d.user, d.sample_rate = request.user, rate
        d.save()
        request.user.devices.exclude(pk=d.pk).update(user=None)   # فقط یک دستگاه
        return Response(self.info(d), status=201)

    def delete(self, request):
        d = request.user.devices.first()
        if d:
            Device.objects.filter(pk=d.pk).update(user=None, recording_since=None)
            pcm_path(d).unlink(missing_ok=True)
        return Response(status=204)
