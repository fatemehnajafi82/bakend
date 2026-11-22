from django.test import TestCase


class HealthTests(TestCase):
    def test_health_ok_without_auth(self):
        r = self.client.get("/api/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "db": "ok", "cache": "ok"})
