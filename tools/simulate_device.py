#!/usr/bin/env python3
"""
شبیه‌ساز دستگاه/گوشی: بدون نیاز به FD-640 صدای آزمایشی شبیه ضربان قلب جنین
(PCM ۱۶بیتی مونو) را به /api/live/ingest/ می‌فرستد.

نمونه:
  python tools/simulate_device.py --token <JWT access> --seconds 60 --bpm 140

فقط از کتابخانهٔ استاندارد پایتون استفاده می‌کند.
"""
import argparse
import math
import random
import struct
import sys
import time
import urllib.error
import urllib.request


def make_chunk(t0, n, rate, bpm, rng):
    """n نمونه از زمان t0: نویز کم + پالس کوتاه ۱۵۰هرتزی در هر ضربان."""
    period = 60.0 / bpm
    out = []
    for i in range(n):
        t = t0 + i / rate
        phase = t % period
        v = rng.gauss(0, 120)                                   # نویز زمینه
        if phase < 0.06:                                         # «ضربان» ۶۰ میلی‌ثانیه‌ای
            env = math.sin(math.pi * phase / 0.06)
            v += 9000 * env * math.sin(2 * math.pi * 150 * t)
        out.append(max(-32768, min(32767, int(v))))
    return struct.pack(f"<{n}h", *out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True, help="JWT access کاربر (دستگاه باید برای او ثبت شده باشد)")
    ap.add_argument("--seconds", type=float, default=30, help="مدت ارسال؛ ۰ = تا Ctrl+C")
    ap.add_argument("--bpm", type=int, default=140, help="فقط سرعتِ ضربانِ صدای ساختگی؛ به سرور فرستاده نمی‌شود")
    ap.add_argument("--rate", type=int, default=16000, help="باید با sample_rate دستگاه یکی باشد")
    ap.add_argument("--chunk-ms", type=int, default=250)
    a = ap.parse_args()

    rng = random.Random(1)
    n = a.rate * a.chunk_ms // 1000
    url = a.base.rstrip("/") + "/api/live/ingest/"
    seq, sent, start = 1, 0, time.time()
    print(f"ارسال به {url}  ({a.bpm} bpm، {a.rate} Hz، هر {a.chunk_ms}ms)  — Ctrl+C برای توقف")
    try:
        while a.seconds == 0 or time.time() - start < a.seconds:
            data = make_chunk((seq - 1) * a.chunk_ms / 1000, n, a.rate, a.bpm, rng)
            req = urllib.request.Request(url, data=data, method="POST", headers={
                "Authorization": f"Bearer {a.token}",
                "Content-Type": "application/octet-stream",
                "X-Seq": str(seq),
            })
            try:
                urllib.request.urlopen(req, timeout=5).read()
                sent += 1
            except urllib.error.HTTPError as e:
                print(f"خطا {e.code}: {e.read().decode(errors='replace')[:200]}")
                if e.code in (400, 401):
                    sys.exit(1)      # توکن نامعتبر یا دستگاهی ثبت نشده
            except urllib.error.URLError as e:
                print(f"اتصال ناموفق: {e.reason}")
            seq += 1
            if seq % 4 == 1:
                print(f"\rارسال‌شده: {sent} تکه ({sent * a.chunk_ms / 1000:.0f} ثانیه)", end="", flush=True)
            # هم‌گام با زمان واقعی
            time.sleep(max(0, start + (seq - 1) * a.chunk_ms / 1000 - time.time()))
    except KeyboardInterrupt:
        pass
    print(f"\nتمام شد؛ {sent} تکه ارسال شد.")


if __name__ == "__main__":
    main()
