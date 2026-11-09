from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .serializers import (
    RequestOTPSerializer, VerifyOTPSerializer,
    RequestEmailOTPSerializer, VerifyEmailOTPSerializer,
)
from . import services


def make_tokens(user, created):
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "is_new_user": created,
    }


# ---------- ورود با موبایل ----------
class RequestOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RequestOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            services.send_otp(s.validated_data["phone"], services.send_sms)
        except services.OTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response({"detail": "کد ارسال شد."})


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = VerifyOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        phone, code = s.validated_data["phone"], s.validated_data["code"]
        try:
            ok = services.verify_otp(phone, code)
        except services.OTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not ok:
            return Response({"detail": "کد اشتباه است."}, status=status.HTTP_400_BAD_REQUEST)

        user, created = User.objects.get_or_create(phone=phone)
        return Response(make_tokens(user, created))


# ---------- ورود با ایمیل ----------
class RequestEmailOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        s = RequestEmailOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"]
        try:
            services.send_otp(email, services.send_email_code)
        except services.OTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except Exception as e:
            print("EMAIL ERROR:", repr(e))   # برای دیدن علت خطا در کنسول
            return Response(
                {"detail": "ارسال ایمیل ناموفق بود. بعداً دوباره تلاش کن."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"detail": "کد به ایمیل ارسال شد."})


class VerifyEmailOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = VerifyEmailOTPSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email, code = s.validated_data["email"], s.validated_data["code"]
        try:
            ok = services.verify_otp(email, code)
        except services.OTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not ok:
            return Response({"detail": "کد اشتباه است."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user, created = User.objects.get(email=email), False
        except User.DoesNotExist:
            user, created = User.objects.create_user(email=email), True
        return Response(make_tokens(user, created))