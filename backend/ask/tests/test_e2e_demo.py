"""The exact demo path on the real sample file: upload -> ask -> evidence/SQL/narrative.
The LLM is scripted (no network); everything else is the real code path."""
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from analytics.tests.base import TempDataMixin
from datasets import duck

from .fakes import ScriptedProvider, plan
from .test_ask import narrative

SAMPLE = Path(settings.BASE_DIR) / "sample_data" / "sales.csv"


class DemoPathTests(TempDataMixin, TestCase):
    def test_demo_page_and_sample_are_served(self):
        c = APIClient()
        page = c.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Open SQL", page.content)
        self.assertEqual(c.get("/demo/sample.csv").status_code, 200)

    def test_upload_then_why_did_revenue_decrease(self):
        c = APIClient()
        up = c.post("/api/datasets/", {"file": SimpleUploadedFile("sales.csv", SAMPLE.read_bytes())},
                    format="multipart")
        self.assertEqual(up.status_code, 201, up.content)
        ds_id = up.json()["id"]
        url = f"/api/datasets/{ds_id}/ask/"

        # probe: run the engine once to read the real numbers the narrative may cite
        from datasets.models import Dataset
        from ask.pipeline import ask
        ds = Dataset.objects.prefetch_related("columns").get(pk=ds_id)
        probe = ask(ds, "Why did revenue decrease last month?",
                    ScriptedProvider(plan(analysis="driver_analysis", metric="revenue")), narrate=False)
        a = probe.analysis
        print("\nHEADLINE:", a.headline)
        for m in a.measurements:
            print(f"  {m.id} {m.label} = {m.value}")
        print("  tables:", [t.id for t in a.tables])
        print("  queries:", len(a.queries), "| assumptions:", len(a.assumptions), "| warnings:", a.warnings)
        m_pct = next(m for m in a.measurements if m.label.startswith("% change"))

        text = f"Revenue changed by {m_pct.value:.1f}% versus the previous month."
        provider = ScriptedProvider(plan(analysis="driver_analysis", metric="revenue"),
                                    narrative(answer=text, ids=(m_pct.id,), step_ids=(m_pct.id,)))
        with mock.patch("ask.pipeline.get_llm_provider", return_value=provider):
            r = c.post(url, {"question": "Why did revenue decrease last month?"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["narrative_status"], "grounded")
        self.assertEqual([t["stage"] for t in body["trace"]],
                         ["plan", "plan_check", "run_analysis", "narrate", "narrative_audit"])
        # every number in the answer is traceable: its query reproduces from the rendered SQL
        q = next(q for q in body["analysis"]["queries"] if q["id"] == m_pct.query_id)
        self.assertEqual(len(duck.run_query(ds, q["sql_rendered"], max_rows=5000).rows), q["row_count"])
