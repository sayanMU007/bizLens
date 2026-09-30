from django.test import TestCase, override_settings
from rest_framework.test import APIClient


class HealthTests(TestCase):
    @override_settings(GROQ_API_KEY="gsk_super_secret_value")
    def test_health_reports_status_but_never_the_key(self):
        res = APIClient().get("/api/health/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["database"])
        self.assertTrue(body["llm"]["configured"])
        self.assertNotIn("gsk_super_secret_value", res.content.decode())
