from rest_framework.test import APIClient

from analytics.tests.base import SampleDatasetTestCase


class AnalyticsApiTests(SampleDatasetTestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = f"/api/datasets/{self.dataset.id}/analyze/"

    def post(self, body, url=None):
        return self.client.post(url or self.url, body, format="json")

    def test_catalog(self):
        body = self.client.get("/api/analytics/catalog/").json()
        self.assertEqual({a["name"] for a in body["analyses"]},
                         {"metric_total", "rank_by_dimension", "period_over_period",
                          "movers", "driver_analysis"})
        self.assertIn("properties", body["analyses"][0]["params_schema"])
        self.assertEqual({m["name"] for m in body["metrics"] if not m["additive"]},
                         {"margin", "avg_unit_price"})

    def test_driver_analysis_over_http(self):
        res = self.post({"analysis": "driver_analysis"})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertIn("Revenue decreased 20.5%", body["headline"])
        self.assertEqual(body["dataset"]["sha256"], self.dataset.sha256)
        self.assertTrue(all(q["sql_rendered"] for q in body["queries"]))

    def test_params_pass_through(self):
        res = self.post({"analysis": "metric_total",
                         "params": {"metric": "units", "period": {"kind": "month", "month": "2026-08"}}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["params"]["metric"], "units")

    def test_invalid_params_are_a_400_with_details(self):
        res = self.post({"analysis": "movers", "params": {"metric": "margin", "n": 999}})
        self.assertEqual(res.status_code, 400)
        error = res.json()["error"]
        self.assertEqual(error["code"], "invalid_params")
        self.assertTrue(error["details"]["errors"])

    def test_engine_errors_are_400s(self):
        res = self.post({"analysis": "rank_by_dimension", "params": {"dimension": "colour"}})
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (400, "unknown_dimension"))
        res = self.post({"analysis": "period_over_period",
                         "params": {"period": {"kind": "month", "month": "2030-01"}}})
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (400, "period_outside_data"))

    def test_unknown_analysis_and_bad_bodies(self):
        self.assertEqual(self.post({"analysis": "nope"}).json()["error"]["code"], "unknown_analysis")
        self.assertEqual(self.post({}).json()["error"]["code"], "unknown_analysis")
        self.assertEqual(self.post([1, 2]).status_code, 400)
        self.assertEqual(self.post({"analysis": "metric_total", "params": [1]}).status_code, 400)

    def test_unknown_dataset_is_404(self):
        res = self.post({"analysis": "metric_total"},
                        url="/api/datasets/00000000-0000-0000-0000-000000000000/analyze/")
        self.assertEqual(res.status_code, 404)
