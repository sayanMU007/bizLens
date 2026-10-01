"""Every number here is checked against an independent pandas calculation on the same data."""
import pandas as pd
from pydantic import ValidationError

from analytics.analyses import run_analysis
from analytics.errors import AnalysisError
from analytics.tests.base import SampleDatasetTestCase


class MetricTotalTests(SampleDatasetTestCase):
    def test_all_time_metrics_match_pandas(self):
        df = self.df
        expected = {
            "revenue": df.revenue.sum(), "cost": df.cost.sum(), "units": df.units.sum(),
            "profit": df.revenue.sum() - df.cost.sum(), "transactions": len(df),
            "margin": 100 * (df.revenue.sum() - df.cost.sum()) / df.revenue.sum(),
            "avg_unit_price": df.revenue.sum() / df.units.sum(),
        }
        for metric, want in expected.items():
            with self.subTest(metric=metric):
                r = self.run_analysis("metric_total", metric=metric)
                self.assertAlmostEqual(r.measurements[0].value, want, places=4)
                self.assertEqual(r.measurements[0].rows_used, len(df))

    def test_last_month_is_september_2026(self):
        r = self.run_analysis("metric_total", period={"kind": "last_month"})
        self.assertEqual(r.current_period.label, "2026-09")
        self.assertAlmostEqual(r.measurements[0].value, self.month_total("2026-09"), places=2)
        self.assertEqual(r.measurements[0].rows_used, (self.df.month == "2026-09").sum())
        self.assertTrue(any("last complete month" in a for a in r.assumptions))

    def test_filter_by_dimension(self):
        r = self.run_analysis("metric_total", period={"kind": "month", "month": "2026-08"},
                              filters=[{"dimension": "region", "value": "East"}])
        self.assertAlmostEqual(r.measurements[0].value,
                               self.month_total("2026-08", region="East"), places=2)
        self.assertIn("Filtered to region = East.", r.assumptions)
        self.assertIn("(region = East)", r.headline)

    def test_two_filters_combine_with_and(self):
        r = self.run_analysis("metric_total", filters=[
            {"dimension": "region", "value": "West"}, {"dimension": "category", "value": "Audio"}])
        d = self.df[(self.df.region == "West") & (self.df.category == "Audio")]
        self.assertAlmostEqual(r.measurements[0].value, d.revenue.sum(), places=2)

    def test_unknown_dimension_lists_the_valid_ones(self):
        with self.assertRaises(AnalysisError) as ctx:
            run_analysis(self.dataset, "metric_total",
                         {"filters": [{"dimension": "colour", "value": "red"}]})
        self.assertEqual(ctx.exception.code, "unknown_dimension")
        self.assertIn("region", ctx.exception.details["available"])

    def test_sql_injection_attempts_are_inert(self):
        r = self.run_analysis("metric_total",
                              filters=[{"dimension": "region", "value": "x' OR '1'='1"}])
        self.assertEqual(r.measurements[0].value, 0)  # matched nothing, executed nothing extra
        with self.assertRaises(AnalysisError):
            run_analysis(self.dataset, "metric_total",
                         {"filters": [{"dimension": 'region" = \'East\' OR "1', "value": "x"}]})
        with self.assertRaises(AnalysisError):
            run_analysis(self.dataset, "rank_by_dimension",
                         {"dimension": "region; DROP TABLE sales"})


class RankTests(SampleDatasetTestCase):
    def test_best_region_matches_pandas(self):
        r = self.run_analysis("rank_by_dimension", dimension="region")
        by = self.df.groupby("region").revenue.sum().sort_values(ascending=False)
        rows = self.table(r, "rank_by_region").rows
        self.assertEqual([x["dim_value"] for x in rows[:4]], list(by.index))
        self.assertAlmostEqual(rows[0]["value"], by.iloc[0], places=2)
        self.assertAlmostEqual(rows[0]["share_of_total"], 100 * by.iloc[0] / by.sum(), places=4)
        self.assertEqual([x["rank_no"] for x in rows], [1, 2, 3, 4])
        self.assertIn(by.index[0], r.headline)
        self.assertIn("highest", r.headline)

    def test_shares_sum_to_100_when_everything_is_listed(self):
        r = self.run_analysis("rank_by_dimension", dimension="product", n=25)
        self.assertAlmostEqual(sum(x["share_of_total"] for x in self.table(r, "rank_by_product").rows),
                               100.0, places=6)

    def test_worst_first_and_limit(self):
        r = self.run_analysis("rank_by_dimension", dimension="customer", n=3, order="worst",
                              period={"kind": "month", "month": "2026-05"})
        rows = self.table(r, "rank_by_customer").rows
        by = self.df[self.df.month == "2026-05"].groupby("customer").revenue.sum().sort_values()
        self.assertEqual([x["dim_value"] for x in rows], list(by.index[:3]))
        self.assertIn("lowest", r.headline)

    def test_ratio_metric_ranks_without_a_share(self):
        r = self.run_analysis("rank_by_dimension", dimension="product", metric="margin")
        g = self.df.groupby("product")[["revenue", "cost"]].sum()
        margin = (100 * (g.revenue - g.cost) / g.revenue).sort_values(ascending=False)
        rows = self.table(r, "rank_by_product").rows
        self.assertEqual(rows[0]["dim_value"], margin.index[0])
        self.assertAlmostEqual(rows[0]["value"], margin.iloc[0], places=6)
        self.assertIsNone(rows[0]["share_of_total"])

    def test_bad_n_rejected(self):
        for n in (0, 26):
            with self.assertRaises(ValidationError):
                run_analysis(self.dataset, "rank_by_dimension", {"dimension": "region", "n": n})


class PeriodOverPeriodTests(SampleDatasetTestCase):
    def test_sep_vs_aug_2026(self):
        r = self.run_analysis("period_over_period")
        cur, pri = self.month_total("2026-09"), self.month_total("2026-08")
        self.assertAlmostEqual(self.measurement(r, "Revenue (2026-09)").value, cur, places=2)
        self.assertAlmostEqual(self.measurement(r, "Revenue (2026-08)").value, pri, places=2)
        self.assertAlmostEqual(self.measurement(r, "Change in").value, cur - pri, places=2)
        self.assertAlmostEqual(self.measurement(r, "% change").value, 100 * (cur - pri) / pri, places=6)
        self.assertIn("decreased 20.5%", r.headline)
        self.assertEqual(r.comparison_period.label, "2026-08")

    def test_trend_matches_pandas_month_over_month(self):
        r = self.run_analysis("period_over_period", trend_months=6)
        rows = self.table(r, "monthly_trend").rows
        self.assertEqual([x["month"] for x in rows],
                         ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"])
        monthly = self.df.groupby("month").revenue.sum()
        for row in rows:
            self.assertAlmostEqual(row["value"], monthly[row["month"]], places=2)
        last = rows[-1]
        self.assertAlmostEqual(last["pct_change"], 100 * (monthly["2026-09"] / monthly["2026-08"] - 1), places=6)
        self.assertTrue(all(x["complete"] for x in rows))
        self.assertEqual(r.warnings, [])

    def test_year_over_year(self):
        r = self.run_analysis("period_over_period", comparison="same_period_last_year")
        self.assertEqual(r.comparison_period.label, "2025-09")
        self.assertAlmostEqual(self.measurement(r, "Revenue (2025-09)").value,
                               self.month_total("2025-09"), places=2)

    def test_date_range_uses_equal_length_previous_window(self):
        r = self.run_analysis("period_over_period", period={
            "kind": "range", "start": "2026-06-01", "end": "2026-06-30"})
        d = self.df
        prior = d[(d.date >= "2026-05-02") & (d.date <= "2026-05-31")].revenue.sum()
        self.assertEqual(r.comparison_period.label, "2026-05-02 to 2026-05-31")
        self.assertAlmostEqual(self.measurement(r, "Revenue (2026-05-02").value, prior, places=2)

    def test_ratio_metric_reports_percentage_points(self):
        r = self.run_analysis("period_over_period", metric="margin")
        self.assertIn("percentage points", r.headline)

    def test_errors(self):
        with self.assertRaises(AnalysisError) as ctx:
            run_analysis(self.dataset, "period_over_period", {"period": {"kind": "all"}})
        self.assertEqual(ctx.exception.code, "comparison_unavailable")
        with self.assertRaises(AnalysisError) as ctx:
            run_analysis(self.dataset, "period_over_period",
                         {"period": {"kind": "month", "month": "2025-01"}})
        self.assertEqual(ctx.exception.code, "no_comparison_data")


class MoversTests(SampleDatasetTestCase):
    def deltas(self, dim, cur="2026-09", pri="2026-08", col="revenue"):
        c = self.df[self.df.month == cur].groupby(dim)[col].sum()
        p = self.df[self.df.month == pri].groupby(dim)[col].sum()
        return (c.sub(p, fill_value=0)).sort_values()

    def test_declining_products_match_pandas(self):
        r = self.run_analysis("movers", dimension="product", n=25)
        expected = self.deltas("product")
        expected = expected[expected < 0]
        rows = [x for x in self.table(r, "movers_by_product").rows if not x["is_other"]]
        self.assertEqual([x["dim_value"] for x in rows], list(expected.index))
        for row in rows:
            self.assertLess(row["delta"], 0)
            self.assertAlmostEqual(row["delta"], expected[row["dim_value"]], places=2)
        self.assertEqual(self.measurement(r, "product values that declined").value, len(expected))
        self.assertEqual(self.measurement(r, "product values in total").value, 6)
        self.assertIn(f"{len(expected)} of 6 product values declined", r.headline)

    def test_growth_direction(self):
        r = self.run_analysis("movers", dimension="region", direction="growth",
                              period={"kind": "month", "month": "2026-05"})
        rows = self.table(r, "movers_by_region").rows
        expected = self.deltas("region", cur="2026-05", pri="2026-04")
        self.assertTrue(rows and all(x["delta"] > 0 for x in rows))
        self.assertEqual({x["dim_value"] for x in rows}, set(expected[expected > 0].index))

    def test_empty_result_is_stated_plainly(self):
        # In 2026-09 every region declined, so there is nothing to list under "growth".
        r = self.run_analysis("movers", dimension="region", direction="growth")
        self.assertEqual(self.table(r, "movers_by_region").rows, [])
        self.assertIn("No region values grew", r.headline)

    def test_both_directions_reconcile_and_group_the_tail(self):
        r = self.run_analysis("movers", dimension="customer", direction="both", n=3)
        rows = self.table(r, "movers_by_customer").rows
        self.assertEqual(sum(1 for x in rows if x["is_other"]), 1)
        self.assertTrue(rows[-1]["is_other"])
        self.assertTrue(rows[-1]["dim_value"].startswith("All other ("))
        total = self.month_total("2026-09") - self.month_total("2026-08")
        self.assertAlmostEqual(sum(x["delta"] for x in rows), total, places=2)

    def test_lost_customers_flagged(self):
        r = self.run_analysis("movers", dimension="customer", n=10,
                              filters=[{"dimension": "region", "value": "East"}])
        lost = {x["dim_value"] for x in self.table(r, "movers_by_customer").rows
                if x["status"] == "lost"}
        self.assertEqual(lost, {"Northwind Traders", "Contoso Retail"})

    def test_metric_must_be_additive(self):
        with self.assertRaises(ValidationError):
            run_analysis(self.dataset, "movers", {"metric": "margin"})


class DriverAnalysisTests(SampleDatasetTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.result = run_analysis(cls.dataset, "driver_analysis", {})

    def test_well_formed_and_traceable(self):
        self.assert_well_formed(self.dataset, self.result)

    def test_total_change_matches_pandas(self):
        cur, pri = self.month_total("2026-09"), self.month_total("2026-08")
        self.assertAlmostEqual(self.measurement(self.result, "Change in revenue").value,
                               cur - pri, places=2)
        self.assertIn("Revenue decreased 20.5%", self.result.headline)

    def test_every_breakdown_reconciles_to_the_total(self):
        total = self.measurement(self.result, "Change in revenue").value
        for dim in ("region", "product", "category", "customer", "salesperson"):
            rows = self.table(self.result, f"by_{dim}").rows
            self.assertAlmostEqual(sum(x["delta"] for x in rows), total, places=2, msg=dim)

    def test_region_breakdown_matches_pandas(self):
        rows = {x["dim_value"]: x for x in self.table(self.result, "by_region").rows}
        for region in ("East", "West", "North", "South"):
            want = self.month_total("2026-09", region=region) - self.month_total("2026-08", region=region)
            self.assertAlmostEqual(rows[region]["delta"], want, places=2)
        self.assertEqual(self.table(self.result, "by_region").rows[0]["dim_value"], "East")

    def test_finds_east_as_the_primary_driver(self):
        total = self.month_total("2026-09") - self.month_total("2026-08")
        east = self.month_total("2026-09", region="East") - self.month_total("2026-08", region="East")
        m = self.measurement(self.result, "Change in revenue for region = East")
        self.assertAlmostEqual(m.value, east, places=2)
        share = self.measurement(self.result, "Share of total change explained by region = East")
        self.assertAlmostEqual(share.value, 100 * east / total, places=4)
        self.assertIn("region 'East'", self.result.headline)

    def test_drilldown_finds_the_two_lost_customers(self):
        table = self.table(self.result, "drilldown_east_by_customer")
        rows = table.rows
        self.assertEqual([x["dim_value"] for x in rows[:2]], ["Northwind Traders", "Contoso Retail"])
        self.assertTrue(all(x["status"] == "lost" and x["current_value"] == 0 for x in rows[:2]))
        east = self.month_total("2026-09", region="East") - self.month_total("2026-08", region="East")
        self.assertAlmostEqual(sum(x["delta"] for x in rows), east, places=2)
        self.assertGreater(rows[0]["share_of_change"] + rows[1]["share_of_change"], 80)

    def test_drills_into_every_other_dimension(self):
        ids = {t.id for t in self.result.tables}
        self.assertEqual({i for i in ids if i.startswith("drilldown_east_by_")},
                         {f"drilldown_east_by_{d}" for d in ("product", "category", "customer", "salesperson")})

    def test_price_volume_split_is_exact_and_matches_pandas(self):
        vol = self.measurement(self.result, "Volume effect").value
        price = self.measurement(self.result, "Price effect").value
        total = self.measurement(self.result, "Change in revenue").value
        self.assertAlmostEqual(vol + price, total, places=2)
        self.assertLess(vol, 0)
        self.assertLess(abs(price), 0.01 * abs(vol))  # a volume problem, not a pricing one
        cur = self.df[self.df.month == "2026-09"].groupby("product")[["units", "revenue"]].sum()
        pri = self.df[self.df.month == "2026-08"].groupby("product")[["units", "revenue"]].sum()
        want_volume = ((cur.units - pri.units) * (pri.revenue / pri.units)).sum()
        want_price = ((cur.revenue / cur.units - pri.revenue / pri.units) * cur.units).sum()
        self.assertAlmostEqual(vol, want_volume, places=2)
        self.assertAlmostEqual(price, want_price, places=2)

    def test_assumptions_and_no_warnings(self):
        text = " ".join(self.result.assumptions)
        self.assertIn("last complete month", text)
        self.assertIn("must not be added together", text)
        self.assertIn("Volume effect", text)
        self.assertEqual(self.result.warnings, [])

    def test_increase_scenario(self):
        r = self.run_analysis("driver_analysis", period={"kind": "month", "month": "2026-05"})
        self.assertIn("Revenue increased", r.headline)
        self.assertGreater(self.measurement(r, "Change in revenue").value, 0)
        first = self.table(r, "by_region").rows[0]
        self.assertGreater(first["delta"], 0)  # largest move in the total's direction comes first

    def test_top_n_groups_the_tail_and_still_reconciles(self):
        r = self.run_analysis("driver_analysis", top_n=2)
        rows = self.table(r, "by_customer").rows
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[-1]["is_other"])

    def test_single_dimension_has_no_drilldown(self):
        r = self.run_analysis("driver_analysis", dimensions=["region"])
        self.assertEqual([t.id for t in r.tables], ["by_region", "price_volume_by_product"])

    def test_non_revenue_metric_skips_price_volume(self):
        r = self.run_analysis("driver_analysis", metric="units")
        self.assertNotIn("price_volume_by_product", [t.id for t in r.tables])
        self.assertIn("Units sold decreased", r.headline)

    def test_ratio_metric_is_rejected(self):
        with self.assertRaises(ValidationError):
            run_analysis(self.dataset, "driver_analysis", {"metric": "margin"})

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(AnalysisError) as ctx:
            run_analysis(self.dataset, "driver_analysis", {"dimensions": ["region", "planet"]})
        self.assertEqual(ctx.exception.code, "unknown_dimension")

    def test_result_is_json_serialisable_and_deterministic(self):
        again = run_analysis(self.dataset, "driver_analysis", {})
        a, b = self.result.model_dump(mode="json"), again.model_dump(mode="json")
        for result in (a, b):
            for q in result["queries"]:
                q["duration_ms"] = 0  # timing is the only thing allowed to differ
        self.assertEqual(a, b)


class RegistryTests(SampleDatasetTestCase):
    def test_unknown_analysis(self):
        with self.assertRaises(AnalysisError) as ctx:
            run_analysis(self.dataset, "make_me_rich", {})
        self.assertEqual(ctx.exception.code, "unknown_analysis")
        self.assertIn("driver_analysis", ctx.exception.details["available"])

    def test_extra_params_forbidden(self):
        with self.assertRaises(ValidationError):
            run_analysis(self.dataset, "metric_total", {"metric": "revenue", "sql": "DROP"})
