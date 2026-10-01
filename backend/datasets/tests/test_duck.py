import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import duckdb
from django.test import SimpleTestCase, override_settings

from datasets import duck, storage
from datasets.ingest import clean_sales, write_parquet
from datasets.tests.test_ingest import make_df


class SandboxTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        override = override_settings(DATA_DIR=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.dataset = SimpleNamespace(id=uuid.uuid4())
        clean, _ = clean_sales(make_df(10))
        write_parquet(clean, storage.parquet_path(self.dataset.id))
        self.secret = Path(self.tmp) / "secret.csv"
        self.secret.write_text("password\nhunter2\n")

    def q(self, sql, params=None, **kw):
        return duck.run_query(self.dataset, sql, params, **kw)

    def test_query_and_parameters(self):
        r = self.q("SELECT count(*) AS n, sum(revenue) AS r FROM sales WHERE units = ?", [3])
        self.assertEqual(r.columns, ["n", "r"])
        self.assertEqual(r.rows, [[10, 305.0]])

    def test_dates_are_json_safe(self):
        self.assertEqual(self.q('SELECT "date" FROM sales LIMIT 1').rows, [["2025-03-01"]])

    def test_truncation_flag(self):
        r = self.q("SELECT * FROM sales", max_rows=4)
        self.assertEqual((len(r.rows), r.truncated), (4, True))

    def test_invalid_sql_is_a_query_error(self):
        with self.assertRaises(duck.QueryError):
            self.q("SELECT nope FROM sales")

    def test_timeout_interrupts_runaway_query(self):
        with self.assertRaises(duck.QueryTimeout):
            self.q("SELECT count(*) FROM range(100000000000) a, range(1000) b",
                   timeout_seconds=0.3)

    def test_cannot_read_other_files(self):
        for sql in (f"SELECT * FROM read_csv('{self.secret}')",
                    "SELECT * FROM read_text('/etc/passwd')",
                    f"SELECT * FROM glob('{self.tmp}/*')"):
            with self.subTest(sql=sql), self.assertRaises(duck.QueryError):
                self.q(sql)

    def test_cannot_write_files(self):
        out = Path(self.tmp) / "out.csv"
        with self.assertRaises(duck.QueryError):
            self.q(f"COPY (SELECT * FROM sales) TO '{out}'")
        self.assertFalse(out.exists())

    def test_cannot_attach_install_or_change_config(self):
        for sql in (f"ATTACH '{self.tmp}/x.db'", "INSTALL httpfs",
                    "SET enable_external_access=true", "SET threads=64",
                    "SET allowed_paths=['/']"):
            with self.subTest(sql=sql), self.assertRaises(duck.QueryError):
                self.q(sql)

    def test_missing_parquet_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            duck.run_query(SimpleNamespace(id=uuid.uuid4()), "SELECT 1")

    def test_repeated_float_aggregation_is_bit_identical(self):
        """Multi-threaded float sums add in a varying order and differ in the last digits.
        Evidence must be reproducible ("re-run the SQL, get the same number"), so sandboxed
        connections run single-threaded. Regression test for exactly that."""
        path = storage.parquet_path(self.dataset.id)
        path.unlink()
        con = duckdb.connect(":memory:")
        con.execute(
            "COPY (SELECT ['East','West'][1 + (i % 2)] AS region, "
            "round(random() * 1000, 2) AS revenue FROM range(400000) t(i)) "
            f"TO {duck.qliteral(path)} (FORMAT PARQUET, ROW_GROUP_SIZE 20000)")
        con.close()
        sql = "SELECT region, sum(revenue), avg(revenue) FROM sales GROUP BY region ORDER BY region"
        results = {repr(self.q(sql).rows) for _ in range(40)}
        self.assertEqual(len(results), 1, "the same query returned different floats on re-run")

    def test_identifier_quoting(self):
        self.assertEqual(duck.qident('we"ird'), '"we""ird"')
        self.assertEqual(duck.qliteral("it's"), "'it''s'")
