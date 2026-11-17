"""
Django settings for config project.

همهٔ مقادیر حساس و وابسته به محیط از متغیرهای محیطی (یا فایل .env) خوانده می‌شوند.
نمونه: .env.example
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv


# ---------- خواندن تنظیمات از محیط ----------
def _load_dotenv(path):
    """خواندن ساده‌ی .env (بدون وابستگی). متغیرهای محیطی واقعی اولویت دارند."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# ---------- هستهٔ امنیتی ----------
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY تنظیم نشده. فایل .env.example را به .env کپی کن "
            "(برای توسعه: DJANGO_DEBUG=True) یا متغیر محیطی را بگذار."
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if not DEBUG and not TESTING:
    # پشت Nginx/Load balancer با HTTPS
    if env_bool("DJANGO_BEHIND_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]      # health-check پشت LB بدون ریدایرکت
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",  # باطل کردن توکن در خروج از حساب
    "accounts",
    "recordings",
    # "drf_spectacular",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ---------- دیتابیس ----------
# پیش‌فرض SQLite (توسعه). برای Postgres کافی است POSTGRES_DB و بقیه را در .env بگذاری.
if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': env("SQLITE_PATH", BASE_DIR / 'db.sqlite3'),
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"          # python manage.py collectstatic


AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # در توسعه محدودیت بالاست؛ در محیط واقعی پیش‌فرض ۱۰ بار در ساعت (قابل تغییر با THROTTLE_OTP)
    "DEFAULT_THROTTLE_RATES": {
        "otp": env("THROTTLE_OTP", "1000/hour" if DEBUG else "10/hour"),
        "upload": env("THROTTLE_UPLOAD", "60/hour"),
    },
}


# ---------- توکن‌ها ----------
# کاربر یک بار وارد می‌شود و تا خروج از حساب در اپ می‌ماند: refresh بلندمدت است و با هر تمدید
# یک refresh تازه می‌دهد (اپ باید refresh جدید را جایگزین قبلی کند). refresh قدیمی باطل می‌شود.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=80),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=365),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}


# ---------- ایمیل ----------
# اگر EMAIL_HOST_USER و EMAIL_HOST_PASSWORD تنظیم نشده باشند، ایمیل‌ها فقط در کنسول چاپ می‌شوند.
if env("EMAIL_HOST_USER") and env("EMAIL_HOST_PASSWORD"):
    _mail = {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env("EMAIL_HOST", "smtp.gmail.com"),
            "port": int(env("EMAIL_PORT", "587")),
            "use_tls": env_bool("EMAIL_USE_TLS", True),
            "username": env("EMAIL_HOST_USER"),
            "password": env("EMAIL_HOST_PASSWORD"),
            "timeout": 10,
        },
    }
else:
    _mail = {"BACKEND": "django.core.mail.backends.console.EmailBackend"}

MAILERS = {"default": _mail}

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Hamava <noreply@localhost>")

MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))

SPECTACULAR_SETTINGS = {
    "TITLE": "Hamava API",
    "VERSION": "1.0.0",
}


# ---------- کش (پخش زنده و OTP) ----------
# با REDIS_URL از Redis استفاده می‌شود (پیشنهاد برای محیط واقعی: سریع‌تر و بدون I/O دیسک).
# بدون Redis: در توسعه کش حافظه‌ای (thread-safe؛ روی ویندوز هم بدون مشکل قفل فایل)،
# در غیر توسعه کش فایلی (بین چند پروسهٔ یک سرور مشترک است).
if env("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("REDIS_URL"),
        }
    }
elif DEBUG or TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "OPTIONS": {"MAX_ENTRIES": 20000},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": Path(env("CACHE_DIR", BASE_DIR / "cache_data")),
            # پیش‌فرض Django فقط ۳۰۰ ورودی است و بعد از آن بخشی از تکه‌های زندهٔ صدا را حذف می‌کند
            "OPTIONS": {"MAX_ENTRIES": 20000},
        }
    }


# ---------- لاگ ----------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
