import mimetypes
import re
from datetime import date

from django.db.models import Q
from django.http import Http404, HttpResponse, StreamingHttpResponse
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import Recording
from .serializers import RecordingSerializer, RecordingUpdateSerializer

MAX_RECORDINGS_PER_USER = 500
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def apply_album_filters(qs, params):
    """فیلترهای مشترک آلبوم (ضبط‌ها و خاطره‌ها):
    ?favorite=true · ?week=24 · ?from=2026-11-01 · ?to=2026-11-30 · ?q=کلمه"""
    if params.get("favorite", "").lower() in ("1", "true"):
        qs = qs.filter(is_favorite=True)
    week = params.get("week")
    if week not in (None, ""):
        if not week.isdigit() or not 0 <= int(week) <= 45:
            raise ValidationError({"week": "هفتهٔ بارداری نامعتبر است."})
        qs = qs.filter(pregnancy_week=int(week))
    for key, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
        value = params.get(key)
        if value:
            try:
                qs = qs.filter(**{lookup: date.fromisoformat(value)})
            except ValueError:
                raise ValidationError({key: "تاریخ باید به شکل YYYY-MM-DD باشد."})
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(note__icontains=q))
    return qs


class RecordingPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class UploadThrottle(UserRateThrottle):
    """فقط روی POST اعمال می‌شود، نه روی لیست."""
    scope = "upload"

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)


class OwnRecordingsMixin:
    permission_classes = [IsAuthenticated]
    serializer_class = RecordingSerializer

    def get_queryset(self):
        return Recording.objects.filter(user=self.request.user)


class RecordingListCreateView(OwnRecordingsMixin, generics.ListCreateAPIView):
    """GET: لیست (?limit=3 برای «ضبط‌های اخیر»، ?favorite=true برای علاقه‌مندی‌ها) · POST: ذخیرهٔ ضبط جدید"""
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = RecordingPagination
    throttle_classes = [UploadThrottle]

    def get_queryset(self):
        return apply_album_filters(super().get_queryset(), self.request.query_params)

    def perform_create(self, serializer):
        if Recording.objects.filter(user=self.request.user).count() >= MAX_RECORDINGS_PER_USER:
            raise ValidationError({"detail": "به سقف تعداد ضبط‌ها رسیده‌ای. چند ضبط قدیمی را حذف کن."})
        serializer.save(user=self.request.user, pregnancy_week=self.request.user.pregnancy_week())


class RecordingDetailView(OwnRecordingsMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET: یک ضبط · PATCH {"is_favorite", "title", "note"} · DELETE: حذف (فایل با signal پاک می‌شود)"""
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        return RecordingUpdateSerializer if self.request.method == "PATCH" else RecordingSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        data = RecordingSerializer(self.get_object(), context=self.get_serializer_context()).data
        return Response(data)


def iter_range(f, length, chunk=64 * 1024):
    try:
        while length > 0:
            data = f.read(min(chunk, length))
            if not data:
                break
            length -= len(data)
            yield data
    finally:
        f.close()


def stream_fieldfile(request, fieldfile):
    """پخش فایل با پشتیبانی از Range (۲۰۶ / ۴۱۶)؛ اگر فایل نباشد ۴۰۴."""
    try:
        size = fieldfile.size
        f = fieldfile.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404

    start, end, status = 0, size - 1, 200
    m = RANGE_RE.fullmatch(request.headers.get("Range", "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
        else:  # bytes=-N یعنی آخرین N بایت
            start = max(size - int(m.group(2)), 0)
        if start > end:
            f.close()
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{size}"
            return resp
        status = 206

    length = end - start + 1
    f.seek(start)
    content_type = mimetypes.guess_type(fieldfile.name)[0] or "application/octet-stream"
    resp = StreamingHttpResponse(iter_range(f, length), status=status, content_type=content_type)
    resp["Content-Length"] = str(length)
    resp["Accept-Ranges"] = "bytes"
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    if status == 206:
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    return resp


class RecordingAudioView(APIView):
    """فایل صوتی با پشتیبانی از Range؛ فقط برای صاحب ضبط"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            rec = Recording.objects.get(pk=pk, user=request.user)
        except Recording.DoesNotExist:
            raise Http404
        return stream_fieldfile(request, rec.audio)
