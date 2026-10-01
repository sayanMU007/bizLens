import shutil
import tempfile

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from analytics.analyses import run_analysis
from datasets import duck
from datasets.ingest import ingest_upload
from datasets.sample_data import generate_sales


def ingest_frame(df: pd.DataFrame, name: str = "test"):
    upload = SimpleUploadedFile("t.csv", df.to_csv(index=False).encode())
    return ingest_upload(uploaded=upload, name=name)


def tiny_frame(rows) -> pd.DataFrame:
    """rows: (date, region, product, units, revenue, customer). Other columns filled in."""
    return pd.DataFrame([{
        "date": d, "region": region, "product": product, "category": "Cat", "units": units,
        "revenue": revenue, "cost": revenue * 0.6, "customer": customer, "salesperson": "Sam",
    } for d, region, product, units, revenue, customer in rows])


# Hand-computed fixture. Feb revenue 300, Mar 210 (delta -90):
#   by region:  East 200 -> 60 (-140), West 100 -> 150 (+50)
#   by product: P 200 -> 160 (-40), Q 100 -> 0 (-100, lost), R 0 -> 50 (+50, new)
#   by customer: C1 -40, C2 -100 (lost), C3 0, C4 +50 (new)
#   P: 20 units @ 10.00 -> 15 units @ 10.667  => volume -50, price +10
#   volume effect total = -50 - 100 + 50 = -100, price effect total = +10
TINY_ROWS = [
    ("2026-01-01", "East", "P", 1, 10, "C1"),
    ("2026-02-05", "East", "P", 10, 100, "C1"),
    ("2026-02-06", "East", "Q", 5, 100, "C2"),
    ("2026-02-07", "West", "P", 10, 100, "C3"),
    ("2026-03-05", "East", "P", 5, 60, "C1"),
    ("2026-03-06", "West", "P", 10, 100, "C3"),
    ("2026-03-31", "West", "R", 2, 50, "C4"),
]


class TempDataMixin:
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._override = override_settings(DATA_DIR=cls._tmp)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)


class AnalysisAssertions:
    def assert_well_formed(self, dataset, result):
        """Structural guarantees every result must satisfy (traceability contract)."""
        query_ids = {q.id for q in result.queries}
        ids = [m.id for m in result.measurements]
        self.assertEqual(len(ids), len(set(ids)), "measurement ids must be unique")
        for m in result.measurements:
            self.assertIn(m.query_id, query_ids, f"{m.id} points at a missing query")
        for t in result.tables:
            self.assertIn(t.query_id, query_ids, f"table {t.id} points at a missing query")
        self.assertEqual(len({t.id for t in result.tables}), len(result.tables))
        for q in result.queries:
            self.assertEqual(q.sql.count("?"), len(q.params))
            self.assertNotIn("?", q.sql_rendered)
            # The human-readable SQL must reproduce the same result set.
            rerun = duck.run_query(dataset, q.sql_rendered, max_rows=5000)
            self.assertEqual(len(rerun.rows), q.row_count, q.purpose)
        self.assertEqual(result.dataset.id, str(dataset.id))

    def measurement(self, result, label_prefix):
        matches = [m for m in result.measurements if m.label.startswith(label_prefix)]
        self.assertTrue(matches, f"no measurement starting {label_prefix!r}: "
                                 f"{[m.label for m in result.measurements]}")
        return matches[0]

    def table(self, result, table_id):
        return next(t for t in result.tables if t.id == table_id)


class SampleDatasetTestCase(TempDataMixin, AnalysisAssertions, TestCase):
    @classmethod
    def setUpTestData(cls):
        df = generate_sales(42)
        df["month"] = df["date"].str[:7]
        cls.df = df
        cls.dataset = ingest_frame(df.drop(columns="month"))

    def run_analysis(self, name, **params):
        result = run_analysis(self.dataset, name, params)
        self.assert_well_formed(self.dataset, result)
        return result

    def month_total(self, month, column="revenue", **where):
        d = self.df[self.df.month == month]
        for key, value in where.items():
            d = d[d[key] == value]
        return d[column].sum()


class TinyDatasetTestCase(TempDataMixin, AnalysisAssertions, TestCase):
    extra_rows: list = []

    @classmethod
    def setUpTestData(cls):
        cls.dataset = ingest_frame(tiny_frame(TINY_ROWS + list(cls.extra_rows)))

    def run_analysis(self, name, **params):
        result = run_analysis(self.dataset, name, params)
        self.assert_well_formed(self.dataset, result)
        return result
