import os
import uuid

from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver


def recording_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"recordings/user_{instance.user_id}/{uuid.uuid4().hex}{ext}"


class Recording(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recordings"
    )
    audio = models.FileField(upload_to=recording_path)
    duration = models.PositiveIntegerField()                 # ثانیه، محاسبه‌شده در سرور
    waveform = models.JSONField(null=True, blank=True)       # نمونه‌های موج (۰ تا ۱۰۰) برای رسم در اپ
    pregnancy_week = models.PositiveSmallIntegerField(null=True, blank=True)   # هفتهٔ بارداری در لحظهٔ ضبط
    is_favorite = models.BooleanField(default=False)
    title = models.CharField(max_length=100, blank=True, default="")     # عنوان دلخواه خاطره
    note = models.TextField(max_length=1000, blank=True, default="")     # یادداشت شخصی مادر
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user_id} · {self.created_at:%Y-%m-%d %H:%M}"


@receiver(post_delete, sender=Recording)
def delete_audio_file(sender, instance, **kwargs):
    # با حذف تکی، حذف گروهی و حذف کاربر (CASCADE) فایل هم پاک می‌شود
    if instance.audio:
        transaction.on_commit(lambda: instance.audio.delete(save=False))

def memory_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"memories/user_{instance.user_id}/{instance.kind}/{uuid.uuid4().hex}{ext}"


class Memory(models.Model):
    """خاطرهٔ غیرصوتی در آلبوم: عکس، ویدئو یا موسیقی ساخته‌شده."""
    PHOTO, VIDEO, MUSIC = "photo", "video", "music"
    KINDS = [(PHOTO, "عکس"), (VIDEO, "ویدئو"), (MUSIC, "موسیقی")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memories"
    )
    kind = models.CharField(max_length=10, choices=KINDS)
    file = models.FileField(upload_to=memory_path)
    size = models.PositiveBigIntegerField(default=0)                       # بایت
    duration = models.PositiveIntegerField(null=True, blank=True)          # ثانیه (عکس: خالی)
    title = models.CharField(max_length=100, blank=True, default="")
    note = models.TextField(max_length=1000, blank=True, default="")
    pregnancy_week = models.PositiveSmallIntegerField(null=True, blank=True)
    is_favorite = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "kind"]),
        ]

    def __str__(self):
        return f"{self.user_id} · {self.kind} · {self.created_at:%Y-%m-%d %H:%M}"


@receiver(post_delete, sender=Memory)
def delete_memory_file(sender, instance, **kwargs):
    if instance.file:
        transaction.on_commit(lambda: instance.file.delete(save=False))


class Device(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="devices")
    serial = models.CharField(max_length=64, unique=True)
    key_hash = models.CharField(max_length=64)          # sha256 کلید دستگاه
    sample_rate = models.PositiveIntegerField(default=16000)
    recording_since = models.DateTimeField(null=True, blank=True)
