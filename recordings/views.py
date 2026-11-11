import mimetypes
import re

from django.http import Http404, HttpResponse, StreamingHttpResponse
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import Recording
from .serializers import RecordingSerializer

MAX_RECORDINGS_PER_USER = 500
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


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
    """GET: لیست (?limit=3 برای «ضبط‌های اخیر») · POST: ذخیرهٔ ضبط جدید"""
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = RecordingPagination
    throttle_classes = [UploadThrottle]

    def perform_create(self, serializer):
        if self.get_queryset().count() >= MAX_RECORDINGS_PER_USER:
            raise ValidationError({"detail": "به سقف تعداد ضبط‌ها رسیده‌ای. چند ضبط قدیمی را حذف کن."})
        serializer.save(user=self.request.user)


class RecordingDetailView(OwnRecordingsMixin, generics.RetrieveDestroyAPIView):
    """GET: یک ضبط · DELETE: حذف (فایل با signal پاک می‌شود)"""


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


class RecordingAudioView(APIView):
    """فایل صوتی با پشتیبانی از Range؛ فقط برای صاحب ضبط"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            rec = Recording.objects.get(pk=pk, user=request.user)
            size = rec.audio.size
            f = rec.audio.open("rb")
        except (Recording.DoesNotExist, FileNotFoundError, ValueError):
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
        content_type = mimetypes.guess_type(rec.audio.name)[0] or "application/octet-stream"
        resp = StreamingHttpResponse(iter_range(f, length), status=status, content_type=content_type)
        resp["Content-Length"] = str(length)
        resp["Accept-Ranges"] = "bytes"
        resp["Cache-Control"] = "private, max-age=3600"
        if status == 206:
            resp["Content-Range"] = f"bytes {start}-{end}/{size}"
        return resp