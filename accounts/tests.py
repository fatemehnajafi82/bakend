import re
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts import services
from accounts.models import User

PHONE = "09123456789"
EMAIL = "mom@example.com"


def last_code_from_mail():
    return re.search(r"\d{6}", mail.outbox[-1].body).group()


class BaseCase(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()

    def post(self, path, data):
        return self.c.post(path, data, format="json")

    def request_phone_code(self, phone=PHONE):
        """کد را از تابع ارسال پیامک می‌گیرد."""
        sent = {}
        with mock.patch.object(services, "send_sms", lambda p, code: sent.update(code=code)):
            # views از services.send_sms در لحظهٔ فراخوانی استفاده می‌کند
            r = self.post("/api/auth/request-otp/", {"phone": phone})
        return r, sent.get("code")


class PhoneOTPTests(BaseCase):
    def test_invalid_phone_rejected(self):
        for bad in ["123", "0812345678", "0912345678", "abcdefghijk", ""]:
            r = self.post("/api/auth/request-otp/", {"phone": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_request_otp_ok(self):
        r, code = self.request_phone_code()
        self.assertEqual(r.status_code, 200)
        self.assertRegex(code, r"^\d{6}$")

    def test_cooldown_blocks_second_request(self):
        self.request_phone_code()
        r, _ = self.request_phone_code()
        self.assertEqual(r.status_code, 429)

    def test_persian_digits_normalised(self):
        r, code = self.request_phone_code("۰۹۱۲۳۴۵۶۷۸۹")
        self.assertEqual(r.status_code, 200)
        r = self.post("/api/auth/verify-otp/", {"phone": "۰۹۱۲۳۴۵۶۷۸۹", "code": code})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(User.objects.filter(phone=PHONE).exists())

    def test_verify_creates_user_then_returns_existing(self):
        _, code = self.request_phone_code()
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": code})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_new_user"])
        self.assertIn("access", r.data)
        self.assertIn("refresh", r.data)

        cache.clear()  # رد کردن cooldown
        _, code = self.request_phone_code()
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": code})
        self.assertFalse(r.data["is_new_user"])
        self.assertEqual(User.objects.filter(phone=PHONE).count(), 1)

    def test_wrong_code_is_400(self):
        _, code = self.request_phone_code()
        wrong = "111111" if code != "111111" else "222222"
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": wrong})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_code_cannot_be_reused(self):
        _, code = self.request_phone_code()
        self.assertEqual(self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": code}).status_code, 200)
        self.assertEqual(self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": code}).status_code, 400)

    def test_verify_without_request_is_expired(self):
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": "123456"})
        self.assertEqual(r.status_code, 400)

    def test_code_length_validated(self):
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": "123"})
        self.assertEqual(r.status_code, 400)

    def test_max_tries_locks_even_correct_code(self):
        _, code = self.request_phone_code()
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(services.MAX_TRIES):
            self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": wrong})
        r = self.post("/api/auth/verify-otp/", {"phone": PHONE, "code": code})
        self.assertEqual(r.status_code, 400)

    def test_sms_failure_clears_cooldown(self):
        def boom(*a):
            raise RuntimeError("sms down")
        with mock.patch.object(services, "send_sms", boom):
            with self.assertRaises(RuntimeError):
                self.post("/api/auth/request-otp/", {"phone": PHONE})
        self.assertIsNone(cache.get(f"otp_cool:{PHONE}"))
        self.assertIsNone(cache.get(f"otp:{PHONE}"))


class EmailOTPTests(BaseCase):
    def test_invalid_email(self):
        r = self.post("/api/auth/email/request-otp/", {"email": "not-an-email"})
        self.assertEqual(r.status_code, 400)

    def test_full_flow_and_email_normalisation(self):
        r = self.post("/api/auth/email/request-otp/", {"email": "  Mom@Example.COM "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        code = last_code_from_mail()

        r = self.post("/api/auth/email/verify-otp/", {"email": EMAIL, "code": code})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_new_user"])
        self.assertTrue(User.objects.filter(email=EMAIL).exists())

    def test_existing_user_not_new(self):
        User.objects.create_user(email=EMAIL)
        self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        r = self.post("/api/auth/email/verify-otp/", {"email": EMAIL, "code": last_code_from_mail()})
        self.assertFalse(r.data["is_new_user"])

    def test_cooldown(self):
        self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        r = self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        self.assertEqual(r.status_code, 429)

    def test_wrong_code(self):
        self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        code = last_code_from_mail()
        wrong = "111111" if code != "111111" else "222222"
        r = self.post("/api/auth/email/verify-otp/", {"email": EMAIL, "code": wrong})
        self.assertEqual(r.status_code, 400)

    def test_smtp_failure_returns_503_and_allows_retry(self):
        with mock.patch("accounts.services.send_mail", side_effect=OSError("smtp down")):
            r = self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        self.assertEqual(r.status_code, 503)
        r = self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        self.assertEqual(r.status_code, 200)

    def test_tokens_work_and_refresh(self):
        self.post("/api/auth/email/request-otp/", {"email": EMAIL})
        r = self.post("/api/auth/email/verify-otp/", {"email": EMAIL, "code": last_code_from_mail()})
        access, refresh = r.data["access"], r.data["refresh"]

        self.c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.c.get("/api/recordings/").status_code, 200)

        self.c.credentials()
        r = self.post("/api/auth/refresh/", {"refresh": refresh})
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)

    def test_bad_token_rejected(self):
        self.c.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        self.assertEqual(self.c.get("/api/recordings/").status_code, 401)
