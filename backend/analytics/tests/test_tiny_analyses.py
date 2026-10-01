"""Hand-computed expectations on a 7-row dataset (see TINY_ROWS in base.py)."""
from analytics.analyses import run_analysis
from analytics.tests.base import TinyDatasetTestCase, tiny_frame, ingest_frame, TINY_ROWS, TempDataMixin, AnalysisAssertions
from django.test import TestCase


class HandComputedTests(TinyDatasetTestCase):
    def test_totals(self):
        r = self.run_analysis("period_over_period")
        self.assertEqual(r.current_period.label, "2026-03")
        self.assertEqual(self.measurement(r, "Revenue (2026-03)").value, 210)
        self.assertEqual(self.measurement(r, "Revenue (2026-02)").value, 300)
        self.assertEqual(self.measurement(r, "Change in").value, -90)
        self.assertAlmostEqual(self.measurement(r, "% change").value, -30.0)
        self.assertEqual(self.measurement(r, "Revenue (2026-03)").rows_used, 3)

    def test_movers_by_product_finds_lost_and_new(self):
        r = self.run_analysis("movers", dimension="product", direction="both")
        rows = {x["dim_value"]: x for x in self.table(r, "movers_by_product").rows}
        self.assertEqual((rows["Q"]["delta"], rows["Q"]["status"], rows["Q"]["pct_change"]),
                         (-100, "lost", -100.0))
        self.assertEqual((rows["P"]["delta"], rows["P"]["status"]), (-40, "changed"))
        self.assertEqual((rows["R"]["delta"], rows["R"]["status"], rows["R"]["pct_change"]),
                         (50, "new", None))
        self.assertAlmostEqual(rows["Q"]["share_of_change"], 100 * -100 / -90)

    def test_decliners_only(self):
        r = self.run_analysis("movers", dimension="product")
        rows = self.table(r, "movers_by_product").rows
        self.assertEqual([(x["dim_value"], x["delta"]) for x in rows], [("Q", -100), ("P", -40)])
        self.assertEqual(self.measurement(r, "product values that declined").value, 2)
        self.assertEqual(self.measurement(r, "product values that grew").value, 1)
        self.assertEqual(self.measurement(r, "product values in total").value, 3)

    def test_driver_analysis(self):
        r = self.run_analysis("driver_analysis")
        region = {x["dim_value"]: x["delta"] for x in self.table(r, "by_region").rows}
        self.assertEqual(region, {"East": -140, "West": 50})
        customer = {x["dim_value"]: x["delta"] for x in self.table(r, "by_customer").rows}
        self.assertEqual(customer, {"C2": -100, "C1": -40, "C3": 0, "C4": 50})
        self.assertEqual(self.measurement(r, "Change in revenue for region = East").value, -140)
        east_products = {x["dim_value"]: x["delta"]
                         for x in self.table(r, "drilldown_east_by_product").rows}
        self.assertEqual(east_products, {"Q": -100, "P": -40})

    def test_price_volume_effects(self):
        r = self.run_analysis("driver_analysis")
        self.assertAlmostEqual(self.measurement(r, "Volume effect").value, -100.0)
        self.assertAlmostEqual(self.measurement(r, "Price effect").value, 10.0)
        rows = {x["dim_value"]: x for x in self.table(r, "price_volume_by_product").rows}
        self.assertAlmostEqual(rows["P"]["volume_effect"], -50.0)
        self.assertAlmostEqual(rows["P"]["price_effect"], 10.0)
        self.assertEqual((rows["Q"]["volume_effect"], rows["Q"]["price_effect"]), (-100, 0))
        self.assertEqual((rows["R"]["volume_effect"], rows["R"]["price_effect"]), (50, 0))

    def test_ranking_with_ties_is_stable(self):
        r = self.run_analysis("rank_by_dimension", dimension="salesperson",
                              period={"kind": "month", "month": "2026-03"})
        rows = self.table(r, "rank_by_salesperson").rows
        self.assertEqual([(x["dim_value"], x["value"], x["share_of_total"]) for x in rows],
                         [("Sam", 210, 100.0)])


class FlatChangeTests(TempDataMixin, AnalysisAssertions, TestCase):
    @classmethod
    def setUpTestData(cls):
        rows = [r for r in TINY_ROWS if not r[0].startswith("2026-03")] + [
            ("2026-03-05", "East", "P", 10, 100, "C1"), ("2026-03-06", "East", "Q", 5, 100, "C2"),
            ("2026-03-07", "West", "P", 10, 100, "C3"), ("2026-03-31", "West", "P", 0, 0, "C3")]
        cls.dataset = ingest_frame(tiny_frame(rows))

    def test_no_change_is_reported_as_flat_with_no_driver(self):
        r = run_analysis(self.dataset, "driver_analysis", {})
        self.assert_well_formed(self.dataset, r)
        self.assertIn("essentially flat", r.headline)
        self.assertNotIn("largest single driver", r.headline)


class PartialMonthTests(TinyDatasetTestCase):
    extra_rows = [("2026-04-01", "East", "P", 1, 10, "C1"), ("2026-04-12", "West", "P", 1, 10, "C3")]

    def test_last_month_excludes_the_partial_trailing_month(self):
        r = self.run_analysis("metric_total", period={"kind": "last_month"})
        self.assertEqual(r.current_period.label, "2026-03")
        self.assertEqual(r.measurements[0].value, 210)
        self.assertTrue(any("2026-04 was excluded" in a for a in r.assumptions))

    def test_explicit_partial_month_is_flagged(self):
        r = self.run_analysis("period_over_period", period={"kind": "month", "month": "2026-04"})
        self.assertFalse(r.current_period.complete)
        self.assertTrue(any("only partly covers 2026-04" in w for w in r.warnings))
        self.assertTrue(any("partly covers a month" in w or "partly covers" in w for w in r.warnings))

    def test_trend_marks_the_partial_month(self):
        r = self.run_analysis("period_over_period", period={"kind": "month", "month": "2026-04"})
        flags = {x["month"]: x["complete"] for x in self.table(r, "monthly_trend").rows}
        # The data runs 2026-01-01 to 2026-04-12: Jan-Mar are fully inside it, April is cut off.
        self.assertEqual(flags, {"2026-01": True, "2026-02": True, "2026-03": True, "2026-04": False})
