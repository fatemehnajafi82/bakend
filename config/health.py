from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def health(request):
    """برای Load balancer / مانیتورینگ: دیتابیس و کش را بررسی می‌کند."""
    checks = {}
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    try:
        cache.set("health", 1, 5)
        checks["cache"] = "ok" if cache.get("health") == 1 else "error"
    except Exception:
        checks["cache"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"status": "ok" if healthy else "error", **checks},
                        status=200 if healthy else 503)
