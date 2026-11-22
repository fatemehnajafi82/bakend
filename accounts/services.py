import logging
import secrets
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

OTP_TTL = 120          # اعتبار کد: ۲ دقیقه
RESEND_COOLDOWN = 60   # فاصلهٔ ارسال مجدد
MAX_TRIES = 5


class OTPError(Exception):
    pass


def send_sms(phone, code):
    # TODO: اتصال به سرویس پیامک واقعی. فعلاً فقط در حالت توسعه در کنسول چاپ می‌شود.
    if settings.DEBUG:
        print("OTP (dev only):", code)
    else:
        logger.error("SMS provider is not configured; OTP for %s was not delivered", phone)


def send_email_code(email, code):
    send_mail(
        subject="Hamava login code",
        message=(
            f"کد تأیید شما: {code}\n"
            "این کد تا ۲ دقیقه معتبر است.\n"
            "اگر شما درخواست نداده‌اید، این ایمیل را نادیده بگیرید."
        ),
        html_message=(
            '<div dir="rtl" style="font-family:Tahoma,sans-serif">'
            "<p>کد تأیید شما:</p>"
            f'<p style="font-size:28px;letter-spacing:6px"><b>{code}</b></p>'
            "<p>این کد تا ۲ دقیقه معتبر است. اگر شما درخواست نداده‌اید، این ایمیل را نادیده بگیرید.</p>"
            "</div>"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def send_otp(identifier: str, deliver):
    """identifier = شماره موبایل یا ایمیل، deliver = تابع ارسال (send_sms یا send_email_code)"""
    if cache.get(f"otp_cool:{identifier}"):
        raise OTPError("لطفاً کمی بعد دوباره تلاش کن.")
    code = f"{secrets.randbelow(10**6):06d}"
    if settings.DEBUG:
        print("DEBUG OTP:", identifier, code)
    cache.set(f"otp:{identifier}", make_password(code), OTP_TTL)
    cache.set(f"otp_cool:{identifier}", 1, RESEND_COOLDOWN)
    cache.delete(f"otp_tries:{identifier}")
    try:
        deliver(identifier, code)
    except Exception:
        # اگر ارسال نشد، کاربر مجبور نباشد ۶۰ ثانیه صبر کند
        cache.delete(f"otp:{identifier}")
        cache.delete(f"otp_cool:{identifier}")
        raise


def verify_otp(identifier: str, code: str) -> bool:
    tries = cache.get(f"otp_tries:{identifier}", 0)
    if tries >= MAX_TRIES:
        raise OTPError("تعداد تلاش‌ها زیاد بود. کد جدید بگیر.")
    hashed = cache.get(f"otp:{identifier}")
    if not hashed:
        raise OTPError("کد منقضی شده است.")
    if not check_password(code, hashed):
        cache.set(f"otp_tries:{identifier}", tries + 1, OTP_TTL)
        return False
    cache.delete(f"otp:{identifier}")
    return True