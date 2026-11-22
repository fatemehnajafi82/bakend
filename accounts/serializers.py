import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .models import User

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        phone = super().to_internal_value(data).translate(FA_DIGITS)
        phone = re.sub(r"\D", "", phone)
        if not re.fullmatch(r"09\d{9}", phone):
            raise serializers.ValidationError("شمارهٔ موبایل معتبر نیست.")
        return phone


class EmailField(serializers.EmailField):
    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().lower()


class RequestOTPSerializer(serializers.Serializer):
    phone = PhoneField()


class VerifyOTPSerializer(serializers.Serializer):
    phone = PhoneField()
    code = serializers.CharField(min_length=6, max_length=6)


class RequestEmailOTPSerializer(serializers.Serializer):
    email = EmailField()


class VerifyEmailOTPSerializer(serializers.Serializer):
    email = EmailField()
    code = serializers.CharField(min_length=6, max_length=6)


class ProfileSerializer(serializers.ModelSerializer):
    pregnancy_week = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone", "email", "name", "due_date", "pregnancy_week"]
        read_only_fields = ["id", "phone", "email"]

    def get_pregnancy_week(self, obj):
        return obj.pregnancy_week()

    def validate_due_date(self, v):
        if v is not None:
            today = timezone.localdate()
            if v < today - timedelta(days=30) or v > today + timedelta(days=300):
                raise serializers.ValidationError("تاریخ زایمان معتبر نیست.")
        return v
