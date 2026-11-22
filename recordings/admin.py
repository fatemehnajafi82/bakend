import hashlib
import secrets

from django import forms
from django.contrib import admin

from .models import Device, Memory, Recording


class DeviceForm(forms.ModelForm):
    # در مسیر گوشی (JWT) کلید دستگاه لازم نیست؛ اگر خالی بماند خودکار پر می‌شود
    key_hash = forms.CharField(required=False, max_length=64,
                               help_text="خالی بگذارید؛ خودکار پر می‌شود.")

    class Meta:
        model = Device
        fields = "__all__"


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    form = DeviceForm
    list_display = ("serial", "user", "sample_rate", "recording_since")

    def save_model(self, request, obj, form, change):
        if not obj.key_hash:
            obj.key_hash = hashlib.sha256(secrets.token_hex(16).encode()).hexdigest()
        super().save_model(request, obj, form, change)


admin.site.register(Recording)
admin.site.register(Memory)
