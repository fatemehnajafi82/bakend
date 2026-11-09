import re
from rest_framework import serializers

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