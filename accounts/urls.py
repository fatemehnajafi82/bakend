from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RequestOTPView, VerifyOTPView,
    RequestEmailOTPView, VerifyEmailOTPView,
)

urlpatterns = [
    path("auth/request-otp/", RequestOTPView.as_view()),
    path("auth/verify-otp/", VerifyOTPView.as_view()),
    path("auth/email/request-otp/", RequestEmailOTPView.as_view()),
    path("auth/email/verify-otp/", VerifyEmailOTPView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
]