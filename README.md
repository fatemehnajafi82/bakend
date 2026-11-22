# Hamava Backend

بکند اپ ثبت ضربان قلب جنین (Django + DRF + JWT).

## اجرا برای توسعه

```
python -m venv venv
venv\Scripts\activate            # ویندوز  (مک/لینوکس: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # مک/لینوکس: cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- تست‌ها: `python manage.py test`
- سلامت سرویس: `GET /api/health/`
- ایمیل: تا وقتی `EMAIL_HOST_USER` و `EMAIL_HOST_PASSWORD` را در `.env` نگذاری، کد ورود فقط در کنسول چاپ می‌شود.

## تست بدون دستگاه (شبیه‌ساز)

1. در `/admin/` یا با `POST /api/device/` یک دستگاه برای کاربرت ثبت کن.
2. توکن بگیر (`/api/auth/.../verify-otp/` → فیلد `access`).
3. `python tools/simulate_device.py --token <access> --seconds 60 --bpm 140`
4. در اپ (یا با curl) `GET /api/live/` را بزن.

## API (خلاصه)

| مسیر | کار |
|---|---|
| `POST /api/auth/request-otp/`، `verify-otp/` (و نسخهٔ `email/`) | ورود با کد یک‌بارمصرف |
| `GET/PATCH /api/me/` | نام و تاریخ زایمان (هفتهٔ بارداری از آن حساب می‌شود) |
| `GET/POST/DELETE /api/device/` | وضعیت / اتصال / جدا کردن دستگاه |
| `POST /api/live/ingest/` | گوشی: ارسال تکهٔ PCM (`Authorization`, `X-Seq`) |
| `POST /api/device/chunks/` | دستگاهِ مستقیم با سریال و کلید |
| `GET /api/live/?after=N` | وضعیت اتصال، تکه‌های جدید، سطح موج، مدت ضبط (سقف ۲ دقیقه: `max_record_seconds`) |
| `POST /api/live/record/start/`، `stop/` | شروع و توقف ضبط |
| `GET/POST /api/recordings/` | لیست (`?favorite=true`, `?limit=`) / آپلود |
| `GET/PATCH/DELETE /api/recordings/<id>/` | جزئیات / علاقه‌مندی / حذف |
| `GET /api/recordings/<id>/audio/` | پخش فایل (Range) |

فرمت صدای زنده: PCM ۱۶بیتی little-endian مونو؛ نرخ نمونه‌برداری در `sample_rate` دستگاه.
این مقدار برای FD-640 فرض شده و باید با مشخصات واقعی SDK تطبیق داده شود.

## محیط واقعی (Production)

```
pip install -r requirements-prod.txt
```

متغیرهای لازم در `.env.example` توضیح داده شده‌اند. حداقل‌ها:
`DJANGO_DEBUG=False`، `DJANGO_SECRET_KEY`، `DJANGO_ALLOWED_HOSTS`.
بعد از تنظیم: `python manage.py check --deploy` و `python manage.py collectstatic`.

- **کش:** پخش زنده و OTP روی کش هستند. با `REDIS_URL` از Redis استفاده می‌شود (پیشنهادی)؛ بدون آن کش فایلی است که روی یک سرور کار می‌کند ولی کندتر است.
- **یک سرور:** بافر ضبط در حال انجام روی دیسک همان سرور است (`MEDIA_ROOT/tmp`)؛ برای چند سرور باید درخواست‌های یک کاربر به یک سرور بروند (sticky) یا بافر به Redis منتقل شود.
- **پیامک:** ارسال SMS هنوز به سرویس واقعی وصل نیست (`accounts/services.py`)؛ ورود با موبایل در محیط واقعی تا اتصال آن کار نمی‌کند.
- پشت Nginx/Load balancer با HTTPS: `DJANGO_BEHIND_PROXY=True`.
