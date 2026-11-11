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
    bpm = models.PositiveSmallIntegerField(null=True, blank=True)
    duration = models.PositiveIntegerField()                 # ثانیه، محاسبه‌شده در سرور
    waveform = models.JSONField(null=True, blank=True)       # نمونه‌های موج (۰ تا ۱۰۰) برای رسم در اپ
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

class Device(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="devices")
    serial = models.CharField(max_length=64, unique=True)
    key_hash = models.CharField(max_length=64)          # sha256 کلید دستگاه
    sample_rate = models.PositiveIntegerField(default=16000)
    recording_since = models.DateTimeField(null=True, blank=True)
    bpm_sum = models.PositiveIntegerField(default=0)
    bpm_count = models.PositiveIntegerField(default=0)        