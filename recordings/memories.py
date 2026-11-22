"""آلبوم: خاطره‌های غیرصوتی (عکس، ویدئو، موسیقی ساخته‌شده)."""
import os

import mutagen
from django.db.models import Count
from django.http import Http404
from django.urls import reverse
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Memory
from .views import (RecordingPagination, UploadThrottle, apply_album_filters,
                    stream_fieldfile)

MB = 1024 * 1024
MAX_MEMORIES_PER_USER = 500

RULES = {
    Memory.PHOTO: {"ext": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}, "max_size": 10 * MB},
    Memory.VIDEO: {"ext": {".mp4", ".mov", ".m4v"}, "max_size": 100 * MB},
    Memory.MUSIC: {"ext": {".mp3", ".m4a", ".aac", ".wav", ".ogg"}, "max_size": 30 * MB, "max_duration": 600},
}
HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"heif"}


def _head(f, n=16):
    head = f.read(n)
    f.seek(0)
    return head


def looks_like_photo(f):
    h = _head(f)
    return (
        h[:3] == b"\xff\xd8\xff"                                   # JPEG
        or h[:8] == b"\x89PNG\r\n\x1a\n"                           # PNG
        or (h[:4] == b"RIFF" and h[8:12] == b"WEBP")               # WebP
        or (h[4:8] == b"ftyp" and h[8:12] in HEIF_BRANDS)          # HEIC/HEIF
    )


def looks_like_video(f):
    return _head(f)[4:8] == b"ftyp"                                # MP4 / MOV / M4V


def media_seconds(f):
    """مدت را از خود فایل می‌خواند؛ اگر نتوانست None."""
    try:
        audio = mutagen.File(f)
    except Exception:
        audio = None
    finally:
        f.seek(0)
    length = getattr(getattr(audio, "info", None), "length", None)
    return round(length) if length else None


class MemorySerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Memory
        fields = ["id", "kind", "title", "note", "duration", "size", "pregnancy_week",
                  "is_favorite", "created_at", "file", "file_url"]
        read_only_fields = ["id", "duration", "size", "pregnancy_week", "is_favorite", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def get_file_url(self, obj):
        url = reverse("memory-file", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def validate(self, attrs):
        kind, f = attrs["kind"], attrs["file"]
        rule = RULES[kind]
        if os.path.splitext(f.name)[1].lower() not in rule["ext"]:
            raise serializers.ValidationError({"file": "فرمت فایل برای این نوع خاطره پشتیبانی نمی‌شود."})
        if f.size > rule["max_size"]:
            raise serializers.ValidationError(
                {"file": f"حجم فایل بیشتر از {rule['max_size'] // MB} مگابایت است."})
        if f.size == 0:
            raise serializers.ValidationError({"file": "فایل خالی است."})

        if kind == Memory.PHOTO:
            if not looks_like_photo(f):
                raise serializers.ValidationError({"file": "فایل تصویر معتبر نیست."})
            attrs["duration"] = None
        elif kind == Memory.VIDEO:
            if not looks_like_video(f):
                raise serializers.ValidationError({"file": "فایل ویدئو معتبر نیست."})
            attrs["duration"] = media_seconds(f)          # اگر خوانده نشد، خالی می‌ماند
        else:
            seconds = media_seconds(f)
            if not seconds:
                raise serializers.ValidationError({"file": "فایل صوتی معتبر نیست."})
            if seconds > rule["max_duration"]:
                raise serializers.ValidationError({"file": "مدت موسیقی بیشتر از ۱۰ دقیقه است."})
            attrs["duration"] = seconds
        attrs["size"] = f.size
        return attrs


class MemoryUpdateSerializer(serializers.ModelSerializer):
    """فقط علاقه‌مندی، عنوان و یادداشت قابل تغییرند."""

    class Meta:
        model = Memory
        fields = ["is_favorite", "title", "note"]

    def validate_title(self, v):
        return v.strip()

    def validate_note(self, v):
        return v.strip()


class OwnMemoriesMixin:
    permission_classes = [IsAuthenticated]
    serializer_class = MemorySerializer

    def get_queryset(self):
        return Memory.objects.filter(user=self.request.user)


class MemoryListCreateView(OwnMemoriesMixin, generics.ListCreateAPIView):
    """GET: لیست (?kind=photo|video|music · ?week= · ?from= · ?to= · ?q= · ?favorite=true) · POST: خاطرهٔ جدید"""
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = RecordingPagination
    throttle_classes = [UploadThrottle]

    def get_queryset(self):
        qs = apply_album_filters(super().get_queryset(), self.request.query_params)
        kind = self.request.query_params.get("kind", "")
        if kind:
            if kind not in RULES:
                raise ValidationError({"kind": "نوع خاطره باید photo یا video یا music باشد."})
            qs = qs.filter(kind=kind)
        return qs

    def perform_create(self, serializer):
        if Memory.objects.filter(user=self.request.user).count() >= MAX_MEMORIES_PER_USER:
            raise ValidationError({"detail": "به سقف تعداد خاطره‌ها رسیده‌ای. چند خاطرهٔ قدیمی را حذف کن."})
        serializer.save(user=self.request.user, pregnancy_week=self.request.user.pregnancy_week())


class MemoryDetailView(OwnMemoriesMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET: یک خاطره · PATCH {"is_favorite", "title", "note"} · DELETE: حذف (فایل هم پاک می‌شود)"""
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        return MemoryUpdateSerializer if self.request.method == "PATCH" else MemorySerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        return Response(MemorySerializer(self.get_object(), context=self.get_serializer_context()).data)


class MemoryFileView(APIView):
    """فایل خاطره با پشتیبانی از Range؛ فقط برای صاحب آن"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            mem = Memory.objects.get(pk=pk, user=request.user)
        except Memory.DoesNotExist:
            raise Http404
        return stream_fieldfile(request, mem.file)


class MemorySummaryView(APIView):
    """تعداد خاطره‌ها به تفکیک نوع: {"photo": 3, "video": 1, "music": 0}"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        counts = dict(Memory.objects.filter(user=request.user)
                      .values_list("kind").annotate(n=Count("id")).order_by())
        return Response({k: counts.get(k, 0) for k in RULES})
