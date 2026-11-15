"""تنظیمات مخصوص تست: کش در حافظه، مدیا در پوشهٔ موقت، ایمیل در حافظه."""
import tempfile
from .settings import *  # noqa

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MEDIA_ROOT = tempfile.mkdtemp(prefix="hamava_test_media_")
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # تست سریع‌تر
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"otp": "1000/hour", "upload": "1000/hour"}}
