import hashlib
import io
import shutil
import tempfile
from unittest import mock

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import TestCase, override_settings
from unittest import skipUnless
from rest_framework.test import APIClient

from datasets import duck, storage
from datasets.models import Dataset
from datasets.sample_data import generate_sales
from datasets.tests.test_ingest import csv_bytes, make_df

URL = "/api/datasets/"


class ApiTestBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        override = override_settings(DATA_DIR=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()

    def upload(self, content, filename="sales.csv", **extra):
        f = SimpleUploadedFile(filename, content, content_type="application/octet-stream")
        return self.client.post(URL, {"file": f, **extra}, format="multipart")


class UploadTests(ApiTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sample = generate_sales(42)
        cls.sample_bytes = cls.sample.to_csv(index=False).encode()

    def test_upload_sample_creates_dataset_files_and_schema(self):
        res = self.upload(self.sample_bytes)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["row_count"], len(self.sample))
        self.assertEqual(body["sha256"], hashlib.sha256(self.sample_bytes).hexdigest())
        self.assertEqual([c["name"] for c in body["columns"]],
                         ["date", "region", "product", "category", "units", "revenue", "cost",
                          "customer", "salesperson"])
        roles = {c["name"]: c["role"] for c in body["columns"]}
        self.assertEqual(roles["date"], "time")
        self.assertEqual(roles["revenue"], "measure")
        self.assertEqual(roles["region"], "dimension")
        types = {c["name"]: c["dtype"] for c in body["columns"]}
        self.assertEqual((types["date"], types["units"], types["revenue"], types["region"]),
                         ("DATE", "BIGINT", "DOUBLE", "VARCHAR"))
        self.assertEqual(body["profile"]["date_range"], {"min": "2025-01-01", "max": "2026-09-30"})
        self.assertEqual(body["profile"]["distinct_months"], 21)
        ds = Dataset.objects.get(pk=body["id"])
        self.assertTrue(storage.parquet_path(ds.id).exists())
        self.assertTrue(storage.raw_path(ds.id, "csv").exists())
        self.assertEqual(storage.raw_path(ds.id, "csv").read_bytes(), self.sample_bytes)

    def test_duckdb_totals_match_independent_pandas_totals(self):
        ds = Dataset.objects.get(pk=self.upload(self.sample_bytes).json()["id"])
        r = duck.run_query(ds, "SELECT sum(revenue), sum(cost), sum(units), count(*) FROM sales")
        rev, cost, units, n = r.rows[0]
        self.assertAlmostEqual(rev, self.sample["revenue"].sum(), places=2)
        self.assertAlmostEqual(cost, self.sample["cost"].sum(), places=2)
        self.assertEqual((units, n), (int(self.sample["units"].sum()), len(self.sample)))

    def test_profile_column_stats(self):
        body = self.upload(self.sample_bytes).json()
        cols = {c["name"]: c for c in body["profile"]["columns"]}
        self.assertEqual(cols["region"]["distinct"], 4)
        self.assertEqual({t["value"] for t in cols["region"]["top_values"]},
                         {"East", "West", "North", "South"})
        self.assertAlmostEqual(cols["revenue"]["min"], self.sample["revenue"].min())
        self.assertAlmostEqual(cols["revenue"]["max"], self.sample["revenue"].max())
        self.assertEqual(cols["units"]["nulls"], 0)
        self.assertNotIn("_source_row", cols)

    def test_dropped_rows_are_reported_and_absent_from_data(self):
        df = make_df(200, date=[f"2025-01-{(i % 28) + 1:02d}" for i in range(200)])
        df.loc[4, "revenue"] = "abc"     # source row 5
        df.loc[99, "units"] = "many"     # source row 100
        body = self.upload(csv_bytes(df)).json()
        v = body["profile"]["validation"]
        self.assertEqual((v["rows_read"], v["rows_kept"], v["rows_dropped"]), (200, 198, 2))
        self.assertEqual({e["source_row"] for e in v["dropped_examples"]}, {5, 100})
        ds = Dataset.objects.get(pk=body["id"])
        kept = {r[0] for r in duck.run_query(ds, "SELECT _source_row FROM sales").rows}
        self.assertTrue({5, 100}.isdisjoint(kept))
        self.assertEqual(len(kept), 198)
        self.assertEqual(ds.row_count, 198)

    def test_xlsx_upload(self):
        df = make_df(50)
        df["date"] = pd.to_datetime(["2025-06-15"] * 50)
        df["units"], df["revenue"], df["cost"] = 2, 10.5, 6.0
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        res = self.upload(buf.getvalue(), filename="Q2 Sales.xlsx")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["name"], "Q2 Sales")
        self.assertEqual(res.json()["file_format"], "xlsx")

    def test_custom_name_and_hostile_filename(self):
        res = self.upload(csv_bytes(make_df(30)), filename="../../etc/evil.csv", name="Q1 data")
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertEqual(body["name"], "Q1 data")
        self.assertEqual(body["original_filename"], "evil.csv")
        self.assertTrue(storage.dataset_dir(body["id"]).is_relative_to(self.tmp))


class UploadRejectionTests(ApiTestBase):
    def assertRejected(self, res, code):
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json()["error"]["code"], code)
        self.assertEqual(Dataset.objects.count(), 0)

    def test_wrong_extension(self):
        self.assertRejected(self.upload(b"x", filename="sales.xls"), "unsupported_format")
        self.assertRejected(self.upload(b"x", filename="sales.pdf"), "unsupported_format")

    def test_no_file(self):
        res = self.client.post(URL, {}, format="multipart")
        self.assertRejected(res, "invalid_request")
        self.assertIn("multipart form field named 'file'", res.json()["error"]["message"])

    def test_file_sent_under_wrong_field_name(self):
        f = SimpleUploadedFile("sales.csv", csv_bytes(make_df(30)))
        res = self.client.post(URL, {"sales": f}, format="multipart")
        self.assertRejected(res, "invalid_request")

    def test_missing_columns_named_in_error(self):
        res = self.upload(csv_bytes(make_df(30).drop(columns=["cost"])))
        self.assertRejected(res, "missing_columns")
        self.assertEqual(res.json()["error"]["details"]["missing"], ["cost"])

    @override_settings(MAX_UPLOAD_MB=0)
    def test_too_large(self):
        self.assertRejected(self.upload(csv_bytes(make_df(30))), "file_too_large")

    def test_garbage_csv(self):
        self.assertRejected(self.upload(b"\x00\x01\x02" * 100), "not_a_csv")

    def test_too_many_invalid_rows(self):
        self.assertRejected(self.upload(csv_bytes(make_df(50, revenue="n/a"))),
                            "too_many_invalid_rows")

    def test_failure_midway_leaves_no_files_or_rows(self):
        with mock.patch("datasets.ingest.write_parquet", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.upload(csv_bytes(make_df(30)))
        self.assertEqual(Dataset.objects.count(), 0)
        datasets_dir = storage.dataset_dir("x").parent
        self.assertEqual(list(datasets_dir.glob("*")) if datasets_dir.exists() else [], [])


class ReadEndpointsTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        df = make_df(60, date=[f"2025-02-{(i % 28) + 1:02d}" for i in range(60)])
        self.body = self.upload(csv_bytes(df)).json()
        self.id = self.body["id"]

    def test_list_and_detail(self):
        listing = self.client.get(URL).json()
        self.assertEqual([d["id"] for d in listing], [self.id])
        self.assertNotIn("profile", listing[0])
        detail = self.client.get(f"{URL}{self.id}/").json()
        self.assertEqual(detail["row_count"], 60)
        self.assertIn("profile", detail)

    def test_unknown_and_malformed_ids_404(self):
        self.assertEqual(self.client.get(f"{URL}00000000-0000-0000-0000-000000000000/").status_code, 404)
        self.assertEqual(self.client.get(f"{URL}not-a-uuid/").status_code, 404)

    def test_preview_default_and_paging(self):
        body = self.client.get(f"{URL}{self.id}/preview/").json()
        self.assertEqual(body["columns"][0], "_source_row")
        self.assertEqual(len(body["rows"]), 20)
        self.assertEqual((body["total"], body["limit"], body["offset"]), (60, 20, 0))
        self.assertEqual([r["_source_row"] for r in body["rows"]], list(range(1, 21)))
        page = self.client.get(f"{URL}{self.id}/preview/?limit=5&offset=10").json()
        self.assertEqual([r["_source_row"] for r in page["rows"]], [11, 12, 13, 14, 15])
        self.assertEqual(page["rows"][0]["region"], "East")
        self.assertEqual(page["rows"][0]["revenue"], 30.5)

    def test_preview_rejects_bad_params(self):
        for qs in ("limit=101", "limit=0", "offset=-1", "limit=abc"):
            with self.subTest(qs=qs):
                res = self.client.get(f"{URL}{self.id}/preview/?{qs}")
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()["error"]["code"], "invalid_request")

    def test_delete_removes_row_and_files(self):
        self.assertTrue(storage.dataset_dir(self.id).exists())
        self.assertEqual(self.client.delete(f"{URL}{self.id}/").status_code, 204)
        self.assertFalse(storage.dataset_dir(self.id).exists())
        self.assertEqual(self.client.get(f"{URL}{self.id}/").status_code, 404)
        self.assertEqual(Dataset.objects.count(), 0)


BROWSABLE = any("Browsable" in r for r in settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"])


@skipUnless(BROWSABLE, "browsable API is only enabled when DJANGO_DEBUG is on")
class BrowsableApiTests(ApiTestBase):
    def test_html_page_renders_with_upload_form(self):
        res = self.client.get(URL, HTTP_ACCEPT="text/html")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('type="file"', html)
        self.assertIn('name="dayfirst"', html)
