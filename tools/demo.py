#!/usr/bin/env python3
"""
دموی کامل بکند در یک اجرا (بدون نیاز به دستگاه و اپ):
ورود با OTP ← تاریخ زایمان ← اتصال دستگاه ← پخش زنده ← ضبط ← علاقه‌مندی ← دانلود فایل.

پیش‌نیاز: سرور روشن باشد (python manage.py runserver).
اجرا:     python tools/demo.py --phone 09123456789
کد ورود در ترمینال «سرور» چاپ می‌شود؛ همان را اینجا وارد کن.
خروجی عمداً انگلیسی است تا در ترمینال ویندوز درست نمایش داده شود.
"""
import argparse
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import simulate_device as sim     # همین پوشه (tools/)


def call(base, method, path, token=None, body=None, raw=None, headers=None):
    h = dict(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if payload[:1] in (b"{", b"[") else payload)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, payload


def step(n, title):
    print(f"\n=== {n}) {title} ===")


def text(r):
    return (r.decode("utf-8", "replace") if isinstance(r, bytes) else str(r))[:600]


def need(ok, msg):
    if not ok:
        raise SystemExit(f"FAILED: {msg}\n(Look at the SERVER terminal for the error traceback.)")


def bar(level):
    return "#" * max(1, level // 4)


class Feeder(threading.Thread):
    """نقش گوشی/دستگاه: تکه‌های صوتی شبیه ضربان قلب را مرتب می‌فرستد."""

    def __init__(self, base, token, rate, pulse_rate):
        super().__init__(daemon=True)
        self.base, self.token, self.rate, self.pulse_rate = base, token, rate, pulse_rate
        self.stop_flag = threading.Event()

    def run(self):
        import random
        rng, seq, ms = random.Random(1), 1, 250
        n, start = self.rate * ms // 1000, time.time()
        while not self.stop_flag.is_set():
            pcm = sim.make_chunk((seq - 1) * ms / 1000, n, self.rate, self.pulse_rate, rng)
            call(self.base, "POST", "/api/live/ingest/", self.token, raw=pcm, headers={
                "Content-Type": "application/octet-stream",
                "X-Seq": str(seq)})
            seq += 1
            time.sleep(max(0, start + (seq - 1) * ms / 1000 - time.time()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--phone", default="09123456789")
    ap.add_argument("--token", help="اگر داری، مرحلهٔ OTP رد می‌شود")
    ap.add_argument("--record-seconds", type=int, default=6)
    ap.add_argument("--out", default="demo_recording.wav")
    a = ap.parse_args()
    base = a.base.rstrip("/")

    step(1, "Health check")
    code, r = call(base, "GET", "/api/health/")
    print(r)
    need(code == 200, "server is not healthy / not running")

    step(2, "Login with one-time code (OTP)")
    token = a.token
    if not token:
        code, r = call(base, "POST", "/api/auth/request-otp/", body={"phone": a.phone})
        need(code == 200, f"request-otp -> {code} {r}")
        print(f"Code sent to {a.phone}. Look at the SERVER terminal for 'DEBUG OTP' and type the 6 digits.")
        otp = input("Code: ").strip()
        code, r = call(base, "POST", "/api/auth/verify-otp/", body={"phone": a.phone, "code": otp})
        need(code == 200, f"verify-otp -> {code} {r}")
        token = r["access"]
    print("Logged in. JWT access token received.")

    step(3, "Profile: due date -> pregnancy week is derived by the server")
    due = (date.today() + timedelta(weeks=12)).isoformat()      # حدود هفتهٔ ۲۸
    code, r = call(base, "PATCH", "/api/me/", token, {"name": "Demo mother", "due_date": due})
    need(code == 200, f"profile -> {code} {r}")
    print(f"due_date={r['due_date']}  ->  pregnancy_week={r['pregnancy_week']}")

    step(4, "Connect the device (Vcomin FD-640 is simulated here)")
    code, r = call(base, "POST", "/api/device/", token, {"serial": "DEMO-FD640", "sample_rate": 16000})
    need(code == 201, f"device -> {code} {r}")
    rate = r["sample_rate"]
    print(f"Device linked: {r}")
    code, r = call(base, "GET", "/api/live/", token)
    need(code == 200, f"GET /api/live/ -> HTTP {code}: {text(r)}")
    print(f"Before any audio: online={r['online']}  (connection status shown in the app)")

    feeder = Feeder(base, token, rate, pulse_rate=140)
    feeder.start()
    try:
        step(5, "Live audio: connection status + waveform")
        time.sleep(1.5)
        after = 0
        for _ in range(6):
            code, r = call(base, "GET", f"/api/live/?after={after}", token)
            need(code == 200, f"GET /api/live/ -> HTTP {code}: {text(r)}")
            after = r["last_seq"]
            lv = r["levels"]
            print(f"online={r['online']}  new_chunks={len(r['chunks'])}  "
                  f"wave: {' '.join(bar(x) for x in lv[-4:])}")
            time.sleep(1)
        # نمونه‌ای از تکهٔ صوتی که اپ پخش می‌کند
        raw = base64.b64decode(r["chunks"][-1]) if r["chunks"] else b""
        print(f"Each chunk = raw 16-bit PCM @ {r['sample_rate']} Hz ({len(raw)} bytes sample)")

        step(6, "Start recording and show elapsed time")
        code, r = call(base, "POST", "/api/live/record/start/", token)
        need(code == 200, f"record/start -> {code} {r}")
        for _ in range(a.record_seconds):
            time.sleep(1)
            code, r = call(base, "GET", "/api/live/", token)
            need(code == 200, f"GET /api/live/ -> HTTP {code}: {text(r)}")
            print(f"recording={r['recording']}  elapsed={r['recording_seconds']}s (max {r['max_record_seconds']}s)")

        step(7, "Stop recording -> saved as lossless WAV with date and pregnancy week")
        code, r = call(base, "POST", "/api/live/record/stop/", token)
        need(code == 201, f"record/stop -> {code} {r}")
        rec_id = r["id"]
        print(r)
    finally:
        feeder.stop_flag.set()

    step(8, "Manage recordings: favorite, list, filter")
    code, r = call(base, "PATCH", f"/api/recordings/{rec_id}/", token, {"is_favorite": True})
    need(code == 200, f"favorite -> {code} {r}")
    print(f"id={r['id']} duration={r['duration']}s week={r['pregnancy_week']} "
          f"favorite={r['is_favorite']} created_at={r['created_at']}")
    code, r = call(base, "GET", "/api/recordings/?favorite=true", token)
    print(f"Favorites list: {r['count']} item(s)")

    step(9, "Playback: download the original audio file")
    code, wav = call(base, "GET", f"/api/recordings/{rec_id}/audio/", token)
    need(code == 200 and wav[:4] == b"RIFF", f"audio -> {code}")
    open(a.out, "wb").write(wav)
    print(f"Saved {a.out} ({len(wav)} bytes) - open it with any audio player.")

    step(10, "Device goes silent -> status becomes offline")
    time.sleep(4)
    code, r = call(base, "GET", "/api/live/", token)
    need(code == 200, f"GET /api/live/ -> HTTP {code}: {text(r)}")
    print(f"online={r['online']}")

    print("\nDemo finished OK.")


if __name__ == "__main__":
    main()
