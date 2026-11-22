import os

import mutagen
from django.urls import reverse
from rest_framework import serializers

from .models import Recording

ALLOWED_EXT = {".m4a", ".aac", ".mp3", ".wav", ".ogg"}
MAX_SIZE = 20 * 1024 * 1024
MAX_DURATION = 120          # حداکثر مدت ضبط: ۲ دقیقه
MAX_WAVEFORM_POINTS = 128


def probe_duration(f):
    """مدت را از خود فایل می‌خواند؛ فایل نامعتبر رد می‌شود."""
    try:
        audio = mutagen.File(f)
    except Exception:
        audio = None
    finally:
        f.seek(0)
    seconds = round(getattr(getattr(audio, "info", None), "length", 0) or 0)
    if seconds < 1:
        raise serializers.ValidationError({"audio": "فایل صوتی معتبر نیست."})
    if seconds > MAX_DURATION:
        raise serializers.ValidationError({"audio": "مدت ضبط بیشتر از ۲ دقیقه است."})
    return seconds


class RecordingSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()
    waveform = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = Recording
        fields = ["id", "title", "note", "duration", "waveform", "pregnancy_week",
                  "is_favorite", "created_at", "audio", "audio_url"]
        read_only_fields = ["id", "duration", "pregnancy_week", "is_favorite", "created_at"]
        extra_kwargs = {"audio": {"write_only": True}}

    def get_audio_url(self, obj):
        url = reverse("recording-audio", args=[obj.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def validate_audio(self, f):
        if os.path.splitext(f.name)[1].lower() not in ALLOWED_EXT:
            raise serializers.ValidationError("فرمت فایل صوتی پشتیبانی نمی‌شود.")
        if f.size > MAX_SIZE:
            raise serializers.ValidationError("حجم فایل بیشتر از ۲۰ مگابایت است.")
        return f

    def validate_waveform(self, v):
        if v is None:
            return v
        ok = (
            isinstance(v, list)
            and len(v) <= MAX_WAVEFORM_POINTS
            and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 100 for x in v)
        )
        if not ok:
            raise serializers.ValidationError("داده موج صدا معتبر نیست.")
        return v

    def validate(self, attrs):
        attrs["duration"] = probe_duration(attrs["audio"])
        return attrs


class RecordingUpdateSerializer(serializers.ModelSerializer):
    """فقط علاقه‌مندی، عنوان و یادداشت قابل تغییرند؛ بقیه (صدا، مدت، تاریخ) ثابت‌اند."""

    class Meta:
        model = Recording
        fields = ["is_favorite", "title", "note"]

    def validate_title(self, v):
        return v.strip()

    def validate_note(self, v):
        return v.strip()
