"""The five deterministic analyses. No LLM anywhere in this file.

Each analysis: resolve periods -> validate names against the schema -> run recorded SQL ->
verify the numbers reconcile -> return measurements/tables/queries/assumptions/warnings.
The `headline` is a templated sentence built only from the numbers just computed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from datasets import duck

from . import queries as q
from .errors import AnalysisError
from .metrics import METRICS, Metric, format_value
from .params import (DriverAnalysisParams, MetricTotalParams, MoversParams,
                     PeriodOverPeriodParams, RankByDimensionParams)
from .periods import Period, resolve_comparison, resolve_period
from .results import AnalysisResult, Column, DatasetRef, PeriodInfo
from .session import Rows, Session, load_schema

FLAT_THRESHOLD_PCT = 0.5  # |change| below this is reported as "flat"
MIN_DRIVER_SHARE_PCT = 30.0  # a single slice must explain at least this much to be called "the driver"


# --------------------------------------------------------------------------- helpers
def _fmt(value, unit, signed=False) -> str:
    return format_value(value, unit, signed=signed)


def _direction(delta, pct) -> str:
    if delta is None or delta == 0:
        return "flat"
    if pct is not None and abs(pct) < FLAT_THRESHOLD_PCT:
        return "flat"
    return "increase" if delta > 0 else "decrease"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "value"


def _periods(s: Session, spec, comparison=None) -> tuple[Period, Period | None]:
    bounds = s.bounds()
    current, assumptions = resolve_period(spec, bounds)
    s.assumptions.extend(assumptions)
    if not current.complete:
        s.warnings.append(
            f"The data ({bounds.min} to {bounds.max}) only partly covers {current.label}, "
            "so figures for it are understated.")
    prior = None
    if comparison:
        prior = resolve_comparison(current, comparison, bounds)
        s.assumptions.append(
            f"Compared with {prior.label} "
            f"({'the immediately preceding period' if comparison == 'previous_period' else 'the same period one year earlier'}).")
        if not prior.complete:
            s.warnings.append(
                f"The data only partly covers the comparison period {prior.label}, "
                "so the comparison is unfair.")
    return current, prior


def _dimension(s: Session, name: str) -> str:
    if name not in s.schema.dimensions:
        raise AnalysisError(
            "unknown_dimension",
            f"'{name}' is not a dimension in this dataset. Available: "
            f"{', '.join(s.schema.dimensions)}.",
            {"available": list(s.schema.dimensions)})
    return name


def _filters(s: Session, filters) -> list[tuple[str, str]]:
    resolved = [(_dimension(s, f.dimension), f.value) for f in filters]
    if resolved:
        s.assumptions.append("Filtered to " + " and ".join(f"{d} = {v}" for d, v in resolved) + ".")
    return resolved


def _filter_text(filters) -> str:
    return " (" + ", ".join(f"{d} = {v}" for d, v in filters) + ")" if filters else ""


def _period_info(p: Period | None) -> PeriodInfo | None:
    return None if p is None else PeriodInfo(
        label=p.label, start=p.start.isoformat(), end=p.end.isoformat(), kind=p.kind,
        complete=p.complete)


def _change_columns(unit: str) -> list[Column]:
    return [Column(name="dim_value"), Column(name="prior_value", unit=unit),
            Column(name="current_value", unit=unit), Column(name="delta", unit=unit),
            Column(name="pct_change", unit="%"), Column(name="share_of_change", unit="%"),
            Column(name="status"), Column(name="rows_prior", unit="rows"),
            Column(name="rows_current", unit="rows"), Column(name="is_other")]


def _check_reconciles(rows: list[dict], expected_delta, what: str) -> None:
    """Integrity check: the parts must add up to the whole. Refuse to return numbers that don't."""
    total = sum(r["delta"] for r in rows)
    tolerance = 0.01 + 1e-9 * max(abs(total), abs(expected_delta))
    if abs(total - expected_delta) > tolerance:
        raise AnalysisError(
            "reconciliation_failed",
            f"The breakdown {what} sums to {total:,.2f} but the total change is "
            f"{expected_delta:,.2f}.")


def _compare_totals(s: Session, metric: Metric, current: Period, prior: Period, filters):
    """Run the current-vs-prior totals query and register the four headline measurements."""
    sql, params = q.compare_totals(metric, current, prior, filters)
    rows = s.run(f"{metric.label}: {current.label} vs {prior.label}", sql, params)
    t = rows.rows[0]
    if t["rows_prior"] == 0:
        s.warnings.append(f"There are no rows in the comparison period {prior.label} "
                          "for this request, so percentage change is undefined.")
    if t["rows_current"] == 0:
        s.warnings.append(f"There are no rows in {current.label} for this request.")
    both = t["rows_current"] + t["rows_prior"]
    label = metric.label.lower()
    ids = {
        "current": s.measure(f"{metric.label} ({current.label})", t["current_value"],
                             metric.unit, rows.query_id, t["rows_current"], current.label),
        "prior": s.measure(f"{metric.label} ({prior.label})", t["prior_value"],
                           metric.unit, rows.query_id, t["rows_prior"], prior.label),
        "delta": s.measure(f"Change in {label}", t["delta"], metric.unit, rows.query_id, both),
        "pct": s.measure(f"% change in {label}", t["pct_change"], "%", rows.query_id, both),
    }
    return t, rows.query_id, ids


def _change_sentence(metric: Metric, current: Period, prior: Period, t: dict) -> str:
    cur, pri, delta, pct = t["current_value"], t["prior_value"], t["delta"], t["pct_change"]
    if metric.unit == "%":
        if cur is None or pri is None:
            return f"{metric.label} could not be computed for both periods."
        return (f"{metric.label} moved from {pri:.1f}% in {prior.label} to {cur:.1f}% in "
                f"{current.label} ({delta:+.1f} percentage points).")
    direction = _direction(delta, pct)
    if direction == "flat":
        return (f"{metric.label} was essentially flat: {_fmt(pri, metric.unit)} in {prior.label} "
                f"vs {_fmt(cur, metric.unit)} in {current.label}.")
    word = "increased" if direction == "increase" else "decreased"
    if pct is None:
        return (f"{metric.label} {word} from {_fmt(pri, metric.unit)} in {prior.label} to "
                f"{_fmt(cur, metric.unit)} in {current.label}.")
    return (f"{metric.label} {word} {abs(pct):.1f}% ({_fmt(delta, metric.unit, signed=True)}) "
            f"from {prior.label} to {current.label}.")


# --------------------------------------------------------------------------- analyses
def metric_total(s: Session, p: MetricTotalParams) -> AnalysisResult:
    metric = METRICS[p.metric]
    period, _ = _periods(s, p.period)
    filters = _filters(s, p.filters)
    sql, params = q.totals(metric, period, filters)
    rows = s.run(f"Total {metric.label.lower()} for {period.label}", sql, params)
    value, n = rows.rows[0]["value"], rows.rows[0]["n"]
    s.measure(f"{metric.label} ({period.label})", value, metric.unit, rows.query_id, n, period.label)
    if n == 0:
        s.warnings.append("No rows match this period and filter.")
    headline = (f"{metric.label} for {period.label}{_filter_text(filters)}: "
                f"{_fmt(value, metric.unit)} across {n:,} transaction rows.")
    return _result(s, "metric_total", "Metric total", headline, p, period, None)


def rank_by_dimension(s: Session, p: RankByDimensionParams) -> AnalysisResult:
    metric = METRICS[p.metric]
    period, _ = _periods(s, p.period)
    filters = _filters(s, p.filters)
    dimension = _dimension(s, p.dimension)

    if metric.additive:
        sql, params = q.totals(metric, period, filters)
        tot = s.run(f"Overall {metric.label.lower()} for {period.label}", sql, params)
        s.measure(f"Total {metric.label.lower()} ({period.label})", tot.rows[0]["value"],
                  metric.unit, tot.query_id, tot.rows[0]["n"], period.label)
    sql, params = q.rank(metric, dimension, period, filters, p.n, p.order)
    rows = s.run(f"{metric.label} by {dimension}, {p.order} first", sql, params)
    if not rows.rows:
        raise AnalysisError("no_data", f"No {metric.label.lower()} values for {period.label}"
                                       f"{_filter_text(filters)}.")
    s.add_table(f"rank_by_{dimension}",
                f"{metric.label} by {dimension} ({period.label}), {p.order} first", rows,
                [Column(name="dim_value"), Column(name="value", unit=metric.unit),
                 Column(name="n", unit="rows"), Column(name="share_of_total", unit="%"),
                 Column(name="rank_no")])
    top = rows.rows[0]
    word = "highest" if p.order == "best" else "lowest"
    s.measure(f"{word.capitalize()} {metric.label.lower()} by {dimension}: {top['dim_value']}",
              top["value"], metric.unit, rows.query_id, top["n"], period.label)
    if top["share_of_total"] is not None:
        s.measure(f"Share of total {metric.label.lower()}: {top['dim_value']}",
                  top["share_of_total"], "%", rows.query_id, top["n"], period.label)
    share = f" ({top['share_of_total']:.1f}% of the total)" if top["share_of_total"] is not None else ""
    headline = (f"{top['dim_value']} had the {word} {metric.label.lower()} by {dimension} for "
                f"{period.label}{_filter_text(filters)}: {_fmt(top['value'], metric.unit)}{share}.")
    return _result(s, "rank_by_dimension", "Ranking by dimension", headline, p, period, None)


def period_over_period(s: Session, p: PeriodOverPeriodParams) -> AnalysisResult:
    metric = METRICS[p.metric]
    current, prior = _periods(s, p.period, p.comparison)
    filters = _filters(s, p.filters)
    t, _, _ = _compare_totals(s, metric, current, prior, filters)

    bounds = s.bounds()
    sql, params = q.monthly_trend(metric, current, filters, bounds.min, bounds.max, p.trend_months)
    trend = s.run(f"Monthly {metric.label.lower()} trend through {current.label}", sql, params)
    s.add_table("monthly_trend", f"{metric.label} by month (through {current.label})", trend,
                [Column(name="month"), Column(name="value", unit=metric.unit),
                 Column(name="n", unit="rows"), Column(name="change", unit=metric.unit),
                 Column(name="pct_change", unit="%"), Column(name="complete")])
    if any(not r["complete"] for r in trend.rows):
        s.warnings.append("The trend includes a month the data only partly covers; "
                          "its change is not comparable with complete months.")
    headline = _change_sentence(metric, current, prior, t) + _filter_text(filters)
    return _result(s, "period_over_period", "Period-over-period change", headline, p, current, prior)


def movers(s: Session, p: MoversParams) -> AnalysisResult:
    metric = METRICS[p.metric]
    current, prior = _periods(s, p.period, p.comparison)
    filters = _filters(s, p.filters)
    dimension = _dimension(s, p.dimension)
    t, _, _ = _compare_totals(s, metric, current, prior, filters)

    order = {"decline": "asc", "growth": "desc"}.get(
        p.direction, "asc" if (t["delta"] or 0) < 0 else "desc")
    sql, params = q.dimension_changes(metric, dimension, current, prior, filters,
                                      p.direction, p.n, order)
    rows = s.run(f"{metric.label} change by {dimension} ({p.direction})", sql, params)
    if p.direction == "both":
        _check_reconciles(rows.rows, t["delta"], f"by {dimension}")
    s.add_table(f"movers_by_{dimension}",
                f"{metric.label} change by {dimension}, {p.direction} ({prior.label} to {current.label})",
                rows, _change_columns(metric.unit))

    sql, params = q.change_counts(metric, dimension, current, prior, filters)
    counts = s.run(f"How many {dimension} values declined or grew", sql, params)
    c = counts.rows[0]
    both = t["rows_current"] + t["rows_prior"]
    s.measure(f"{dimension} values that declined", c["n_declined"], "count", counts.query_id, both)
    s.measure(f"{dimension} values that grew", c["n_grew"], "count", counts.query_id, both)
    s.measure(f"{dimension} values in total", c["n_total"], "count", counts.query_id, both)

    listed = [r for r in rows.rows if not r["is_other"]]
    what = f"{metric.label.lower()}{_filter_text(filters)}"
    span = f"from {prior.label} to {current.label}"
    if not listed:
        headline = f"No {dimension} values {'declined' if p.direction == 'decline' else 'grew' if p.direction == 'growth' else 'changed'} in {what} {span}."
    else:
        top = listed[0]
        pct = f", {top['pct_change']:.1f}%" if top["pct_change"] is not None else ""
        lead = {"decline": f"{c['n_declined']} of {c['n_total']} {dimension} values declined",
                "growth": f"{c['n_grew']} of {c['n_total']} {dimension} values grew",
                "both": f"{c['n_declined']} {dimension} values declined and {c['n_grew']} grew "
                        f"(of {c['n_total']})"}[p.direction]
        headline = (f"{lead} in {what} {span}. The largest move was {top['dim_value']} "
                    f"({_fmt(top['delta'], metric.unit, signed=True)}{pct}).")
    return _result(s, "movers", "Movers by dimension", headline, p, current, prior)


def driver_analysis(s: Session, p: DriverAnalysisParams) -> AnalysisResult:
    metric = METRICS[p.metric]
    current, prior = _periods(s, p.period, p.comparison)
    filters = _filters(s, p.filters)
    dimensions = list(dict.fromkeys(
        _dimension(s, d) for d in (p.dimensions or s.schema.default_dimensions)))
    if not dimensions:
        raise AnalysisError("no_dimensions", "This dataset has no dimensions to break down by.")

    t, total_query, _ = _compare_totals(s, metric, current, prior, filters)
    total_delta = t["delta"] or 0
    direction = _direction(t["delta"], t["pct_change"])
    order = "desc" if total_delta > 0 else "asc"  # largest moves in the total's direction first
    span = f"{prior.label} to {current.label}"

    candidates = []  # (dimension, row, query_id) for slices moving with the total
    for dimension in dimensions:
        sql, params = q.dimension_changes(metric, dimension, current, prior, filters,
                                          "both", p.top_n, order)
        rows = s.run(f"{metric.label} change by {dimension}", sql, params)
        _check_reconciles(rows.rows, total_delta, f"by {dimension}")
        s.add_table(f"by_{dimension}", f"{metric.label} change by {dimension} ({span})", rows,
                    _change_columns(metric.unit))
        for r in rows.rows:
            if not r["is_other"] and total_delta and r["delta"] * total_delta > 0:
                candidates.append((dimension, r, rows.query_id))
    s.assumptions.append(
        "Each breakdown adds up to the total change on its own, but the breakdowns overlap "
        "(a region's change is also spread across its customers and products), so they must "
        "not be added together.")

    primary_sentence = ""
    primary = max(candidates, key=lambda c: abs(c[1]["delta"]), default=None)
    if direction != "flat" and primary and abs(primary[1]["share_of_change"] or 0) >= MIN_DRIVER_SHARE_PCT:
        dim, row, qid = primary
        both = row["rows_prior"] + row["rows_current"]
        s.measure(f"Change in {metric.label.lower()} for {dim} = {row['dim_value']}",
                  row["delta"], metric.unit, qid, both, f"{prior.label} to {current.label}")
        s.measure(f"Share of total change explained by {dim} = {row['dim_value']}",
                  row["share_of_change"], "%", qid, both)
        primary_sentence = (f" The largest single driver is {dim} '{row['dim_value']}' "
                            f"({_fmt(row['delta'], metric.unit, signed=True)}, "
                            f"{row['share_of_change']:.0f}% of the total change).")
        s.assumptions.append(
            f"The primary driver is the single dimension value with the largest change in the "
            f"same direction as the total, and only when it explains at least "
            f"{MIN_DRIVER_SHARE_PCT:.0f}% of it.")
        if row["dim_value"] != "(blank)":
            slice_filters = filters + [(dim, row["dim_value"])]
            for other in (d for d in dimensions if d != dim):
                sql, params = q.dimension_changes(metric, other, current, prior, slice_filters,
                                                  "both", p.top_n, order)
                rows = s.run(f"Inside {dim} = {row['dim_value']}: {metric.label.lower()} change by {other}",
                             sql, params)
                _check_reconciles(rows.rows, row["delta"], f"inside {dim} = {row['dim_value']} by {other}")
                s.add_table(f"drilldown_{_slug(row['dim_value'])}_by_{other}",
                            f"Inside {dim} = {row['dim_value']}: {metric.label.lower()} change by {other}",
                            rows, _change_columns(metric.unit))

    pvm_sentence = ""
    if p.metric == "revenue":
        sql, params = q.pvm_totals(current, prior, filters)
        totals = s.run("Split the revenue change into volume and price effects (by product)",
                       sql, params)
        e = totals.rows[0]
        if e["volume_effect"] is not None:
            both = e["rows_prior"] + e["rows_current"]
            _check_reconciles([{"delta": e["volume_effect"] + e["price_effect"]}], total_delta,
                              "of volume + price effects")
            s.measure("Volume effect on revenue", e["volume_effect"], "currency",
                      totals.query_id, both)
            s.measure("Price effect on revenue", e["price_effect"], "currency",
                      totals.query_id, both)
            sql, params = q.pvm_rows(current, prior, filters, p.top_n, order)
            rows = s.run("Volume and price effects per product", sql, params)
            s.add_table("price_volume_by_product",
                        f"Volume vs price effect on revenue by product ({span})", rows,
                        [Column(name="dim_value"), Column(name="prior_units", unit="units"),
                         Column(name="current_units", unit="units"),
                         Column(name="prior_revenue", unit="currency"),
                         Column(name="current_revenue", unit="currency"),
                         Column(name="delta", unit="currency"),
                         Column(name="volume_effect", unit="currency"),
                         Column(name="price_effect", unit="currency"), Column(name="is_other")])
            s.assumptions.append(
                "Volume effect = change in units x the product's earlier average price; price "
                "effect = change in average price x current units, summed over products. Volume "
                "therefore includes shifts in mix between products, and new or lost products "
                "count fully as volume.")
            pvm_sentence = (f" Volume effects account for {_fmt(e['volume_effect'], 'currency', signed=True)} "
                            f"and price effects for {_fmt(e['price_effect'], 'currency', signed=True)}.")

    headline = _change_sentence(metric, current, prior, t) + _filter_text(filters) + primary_sentence + pvm_sentence
    return _result(s, "driver_analysis", "Why did it change?", headline, p, current, prior)


# --------------------------------------------------------------------------- registry
def _result(s: Session, name: str, title: str, headline: str, params, current: Period,
            prior: Period | None) -> AnalysisResult:
    validation = (s.dataset.profile or {}).get("validation", {})
    if validation.get("rows_dropped"):
        s.assumptions.append(
            f"{validation['rows_dropped']:,} of {validation['rows_read']:,} rows in the uploaded "
            "file were excluded at upload because a date, units, revenue or cost value was "
            "missing or unreadable (see the dataset's validation report).")
    if validation.get("filled_unknown"):
        s.assumptions.append(
            "Blank text values were labelled 'Unknown' at upload: "
            + ", ".join(f"{k} ({v:,})" for k, v in validation["filled_unknown"].items()) + ".")
    return AnalysisResult(
        analysis=name, title=title, headline=headline, params=params.model_dump(mode="json"),
        dataset=DatasetRef(id=str(s.dataset.id), name=s.dataset.name, sha256=s.dataset.sha256,
                           row_count=s.dataset.row_count),
        current_period=_period_info(current), comparison_period=_period_info(prior),
        measurements=s.measurements, tables=s.tables, queries=s.queries,
        assumptions=s.assumptions, warnings=s.warnings)


@dataclass(frozen=True)
class AnalysisSpec:
    name: str
    description: str
    params_model: type
    run: Callable[[Session, object], AnalysisResult]


REGISTRY: dict[str, AnalysisSpec] = {a.name: a for a in (
    AnalysisSpec("metric_total",
                 "A single metric (revenue, cost, units, profit, margin...) over a period, "
                 "optionally filtered. Answers: 'What is total revenue?'",
                 MetricTotalParams, metric_total),
    AnalysisSpec("rank_by_dimension",
                 "Rank the values of a dimension (region, product, customer...) by a metric. "
                 "Answers: 'Which region performed best?'",
                 RankByDimensionParams, rank_by_dimension),
    AnalysisSpec("period_over_period",
                 "Compare a metric between two periods and show the monthly trend. "
                 "Answers: 'What changed month-over-month?'",
                 PeriodOverPeriodParams, period_over_period),
    AnalysisSpec("movers",
                 "Which values of a dimension declined or grew between two periods. "
                 "Answers: 'Which products declined?'",
                 MoversParams, movers),
    AnalysisSpec("driver_analysis",
                 "Explain why a metric changed: break the change down by every dimension, "
                 "drill into the biggest driver, and split revenue into volume and price effects. "
                 "Answers: 'Why did revenue decrease?'",
                 DriverAnalysisParams, driver_analysis),
)}


def run_analysis(dataset, name: str, raw_params: dict | None) -> AnalysisResult:
    spec = REGISTRY.get(name)
    if spec is None:
        raise AnalysisError("unknown_analysis", f"Unknown analysis '{name}'.",
                            {"available": sorted(REGISTRY)})
    params = spec.params_model.model_validate(raw_params or {})  # pydantic.ValidationError -> caller
    schema = load_schema(dataset)
    with duck.open_dataset(dataset) as con:
        return spec.run(Session(dataset, con, schema), params)
