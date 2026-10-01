import datetime

from django.test import SimpleTestCase
from pydantic import ValidationError

from analytics.analyses import REGISTRY
from analytics.errors import AnalysisError
from analytics.metrics import METRICS, format_value
from analytics.periods import (DataBounds, PeriodSpec, resolve_comparison, resolve_period)
from analytics.session import render_sql
from ai.strict_schema import to_strict_json_schema

D = datetime.date
BOUNDS = DataBounds(D(2025, 1, 1), D(2026, 9, 30))


class PeriodSpecTests(SimpleTestCase):
    def test_valid_specs(self):
        PeriodSpec(kind="all")
        PeriodSpec(kind="month", month="2026-09")
        PeriodSpec(kind="range", start="2026-01-01", end="2026-01-31")

    def test_invalid_specs(self):
        for kwargs in ({"kind": "month"}, {"kind": "month", "month": "2026-13"},
                       {"kind": "month", "month": "Sept 2026"}, {"kind": "range"},
                       {"kind": "range", "start": "2026-02-01", "end": "2026-01-01"},
                       {"kind": "range", "start": "yesterday", "end": "today"},
                       {"kind": "decade"}, {"kind": "all", "bogus": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                PeriodSpec(**kwargs)


class ResolvePeriodTests(SimpleTestCase):
    def test_last_month_when_data_ends_on_month_end(self):
        period, notes = resolve_period(PeriodSpec(kind="last_month"), BOUNDS)
        self.assertEqual((period.start, period.end, period.label),
                         (D(2026, 9, 1), D(2026, 9, 30), "2026-09"))
        self.assertIn("not the current calendar month", notes[0])

    def test_last_month_skips_a_partial_trailing_month(self):
        bounds = DataBounds(D(2025, 1, 1), D(2026, 4, 12))
        period, notes = resolve_period(PeriodSpec(kind="last_month"), bounds)
        self.assertEqual(period.label, "2026-03")
        self.assertIn("2026-04 was excluded", notes[0])

    def test_last_month_needs_a_complete_month(self):
        with self.assertRaises(AnalysisError) as ctx:
            resolve_period(PeriodSpec(kind="last_month"), DataBounds(D(2026, 3, 5), D(2026, 3, 20)))
        self.assertEqual(ctx.exception.code, "no_complete_month")

    def test_leap_year_february(self):
        bounds = DataBounds(D(2023, 1, 1), D(2024, 2, 29))
        period, _ = resolve_period(PeriodSpec(kind="last_month"), bounds)
        self.assertEqual((period.start, period.end), (D(2024, 2, 1), D(2024, 2, 29)))

    def test_explicit_month_flags_partial_coverage(self):
        bounds = DataBounds(D(2025, 1, 1), D(2026, 4, 12))
        period, _ = resolve_period(PeriodSpec(kind="month", month="2026-04"), bounds)
        self.assertFalse(period.complete)
        period, _ = resolve_period(PeriodSpec(kind="month", month="2026-03"), bounds)
        self.assertTrue(period.complete)

    def test_period_outside_data(self):
        with self.assertRaises(AnalysisError) as ctx:
            resolve_period(PeriodSpec(kind="month", month="2027-01"), BOUNDS)
        self.assertEqual(ctx.exception.code, "period_outside_data")
        self.assertIn("2025-01-01", ctx.exception.message)

    def test_all(self):
        period, _ = resolve_period(PeriodSpec(kind="all"), BOUNDS)
        self.assertEqual((period.start, period.end), (BOUNDS.min, BOUNDS.max))


class ResolveComparisonTests(SimpleTestCase):
    def month(self, m):
        return resolve_period(PeriodSpec(kind="month", month=m), BOUNDS)[0]

    def test_previous_month_crosses_year_boundary(self):
        prior = resolve_comparison(self.month("2026-01"), "previous_period", BOUNDS)
        self.assertEqual(prior.label, "2025-12")

    def test_same_month_last_year(self):
        prior = resolve_comparison(self.month("2026-09"), "same_period_last_year", BOUNDS)
        self.assertEqual((prior.label, prior.start, prior.end),
                         ("2025-09", D(2025, 9, 1), D(2025, 9, 30)))

    def test_range_previous_window_has_equal_length(self):
        current = resolve_period(
            PeriodSpec(kind="range", start="2026-06-01", end="2026-06-30"), BOUNDS)[0]
        prior = resolve_comparison(current, "previous_period", BOUNDS)
        self.assertEqual((prior.start, prior.end), (D(2026, 5, 2), D(2026, 5, 31)))
        self.assertEqual((prior.end - prior.start).days, (current.end - current.start).days)

    def test_range_same_period_last_year_handles_leap_day(self):
        current = resolve_period(
            PeriodSpec(kind="range", start="2026-02-01", end="2026-02-28"), BOUNDS)[0]
        prior = resolve_comparison(current, "same_period_last_year", BOUNDS)
        self.assertEqual((prior.start, prior.end), (D(2025, 2, 1), D(2025, 2, 28)))

    def test_all_has_no_comparison(self):
        current = resolve_period(PeriodSpec(kind="all"), BOUNDS)[0]
        with self.assertRaises(AnalysisError) as ctx:
            resolve_comparison(current, "previous_period", BOUNDS)
        self.assertEqual(ctx.exception.code, "comparison_unavailable")

    def test_comparison_before_the_data_starts(self):
        with self.assertRaises(AnalysisError) as ctx:
            resolve_comparison(self.month("2025-01"), "previous_period", BOUNDS)
        self.assertEqual(ctx.exception.code, "no_comparison_data")


class RenderSqlTests(SimpleTestCase):
    def test_inlines_and_escapes(self):
        sql = render_sql("SELECT ? , ?, ?, ?, ?",
                         [D(2026, 9, 1), "it's", None, 3, 2.5])
        self.assertEqual(sql, "SELECT DATE '2026-09-01' , 'it''s', NULL, 3, 2.5")

    def test_hostile_value_stays_a_string_literal(self):
        sql = render_sql("WHERE r = ?", ["x' OR '1'='1"])
        self.assertEqual(sql, "WHERE r = 'x'' OR ''1''=''1'")

    def test_placeholder_mismatch_raises(self):
        with self.assertRaises(ValueError):
            render_sql("SELECT ?", [1, 2])


class MetricTests(SimpleTestCase):
    def test_registry(self):
        self.assertEqual({m for m, v in METRICS.items() if v.additive},
                         {"revenue", "cost", "units", "profit", "transactions"})
        self.assertFalse(METRICS["margin"].additive)

    def test_formatting(self):
        self.assertEqual(format_value(1234567.891, "currency"), "1,234,568")
        self.assertEqual(format_value(12.345, "currency"), "12.35")
        self.assertEqual(format_value(-230746.7, "currency", signed=True), "-230,747")
        self.assertEqual(format_value(5.0, "currency", signed=True), "+5.00")
        self.assertEqual(format_value(-20.53, "%"), "-20.5%")
        self.assertEqual(format_value(None, "currency"), "n/a")

    def test_every_params_model_is_groq_strict_compatible(self):
        """Phase 4 hands these models to strict structured output, so they must convert."""
        for spec in REGISTRY.values():
            with self.subTest(analysis=spec.name):
                schema = to_strict_json_schema(spec.params_model)
                self.assertFalse(schema["additionalProperties"])
