"""EVERY SQL statement the analytics engine can run lives in this file.

Rules that keep it safe and auditable:
  * Values (dates, filter values, limits) are always bound as `?` parameters.
  * The only text spliced into SQL is (a) a metric expression from the fixed registry,
    (b) a dimension name already validated against the dataset schema and double-quoted,
    and (c) fixed literals (ASC/DESC, direction predicates) chosen from a whitelist.
  * All arithmetic (deltas, percentages, shares, price/volume effects, "All other" rows) is
    done here, in SQL, so each number in a result is reproducible from the recorded query.
  * Parameters are returned in the same order the `?` placeholders appear in the text.
"""
from __future__ import annotations

from typing import Any

from datasets.duck import TABLE, qident

from .metrics import Metric
from .periods import Period

Filters = list[tuple[str, str]]  # [(dimension, value)]

_DIRECTION_PREDICATE = {"decline": "delta < 0", "growth": "delta > 0", "both": "TRUE"}
_ORDER = {"asc": "ASC", "desc": "DESC"}


def _where(period: Period, filters: Filters) -> tuple[str, list[Any]]:
    parts, params = ['"date" BETWEEN ? AND ?'], [period.start, period.end]
    for dimension, value in filters:
        parts.append(f"{qident(dimension)} = ?")
        params.append(value)
    return " AND ".join(parts), params


def totals(metric: Metric, period: Period, filters: Filters) -> tuple[str, list[Any]]:
    where, params = _where(period, filters)
    return f"""
        SELECT {metric.expr} AS value, count(*) AS n
        FROM {TABLE}
        WHERE {where}
    """, params


def compare_totals(metric: Metric, current: Period, prior: Period,
                   filters: Filters) -> tuple[str, list[Any]]:
    wc, pc = _where(current, filters)
    wp, pp = _where(prior, filters)
    return f"""
        WITH cur AS (SELECT {metric.expr} AS v, count(*) AS n FROM {TABLE} WHERE {wc}),
             pri AS (SELECT {metric.expr} AS v, count(*) AS n FROM {TABLE} WHERE {wp})
        SELECT cur.v AS current_value, pri.v AS prior_value,
               cur.v - pri.v AS delta,
               100.0 * (cur.v - pri.v) / nullif(pri.v, 0) AS pct_change,
               cur.n AS rows_current, pri.n AS rows_prior
        FROM cur, pri
    """, pc + pp


def _change_ctes(metric: Metric, dimension: str, current: Period, prior: Period,
                 filters: Filters) -> tuple[str, list[Any]]:
    wc, pc = _where(current, filters)
    wp, pp = _where(prior, filters)
    qd = qident(dimension)
    return f"""
        cur AS (SELECT {qd} AS k, {metric.expr} AS v, count(*) AS n
                FROM {TABLE} WHERE {wc} GROUP BY 1),
        pri AS (SELECT {qd} AS k, {metric.expr} AS v, count(*) AS n
                FROM {TABLE} WHERE {wp} GROUP BY 1),
        joined AS (
            SELECT coalesce(cur.k, pri.k) AS dim_value,
                   coalesce(pri.v, 0) AS prior_value,
                   coalesce(cur.v, 0) AS current_value,
                   coalesce(cur.v, 0) - coalesce(pri.v, 0) AS delta,
                   coalesce(pri.n, 0) AS rows_prior,
                   coalesce(cur.n, 0) AS rows_current
            FROM cur FULL OUTER JOIN pri ON cur.k IS NOT DISTINCT FROM pri.k)
    """, pc + pp


def dimension_changes(metric: Metric, dimension: str, current: Period, prior: Period,
                      filters: Filters, direction: str, top_n: int,
                      order: str) -> tuple[str, list[Any]]:
    """Change in an ADDITIVE metric per value of `dimension`.

    Returns the `top_n` values with the largest absolute change (within `direction`), plus one
    "All other" row aggregating the rest, so the rows always sum to the total change.
    `share_of_change` is relative to the total change across ALL values of the dimension.
    """
    ctes, params = _change_ctes(metric, dimension, current, prior, filters)
    return f"""
        WITH {ctes},
        total AS (SELECT sum(delta) AS total_delta FROM joined),
        scoped AS (SELECT * FROM joined WHERE {_DIRECTION_PREDICATE[direction]}),
        ranked AS (SELECT *, row_number() OVER (ORDER BY abs(delta) DESC, dim_value) AS rk
                   FROM scoped)
        SELECT * FROM (
            SELECT coalesce(CAST(dim_value AS VARCHAR), '(blank)') AS dim_value,
                   prior_value, current_value, delta,
                   100.0 * delta / nullif(prior_value, 0) AS pct_change,
                   100.0 * delta / nullif((SELECT total_delta FROM total), 0) AS share_of_change,
                   CASE WHEN prior_value = 0 AND current_value <> 0 THEN 'new'
                        WHEN current_value = 0 AND prior_value <> 0 THEN 'lost'
                        ELSE 'changed' END AS status,
                   rows_prior, rows_current, 0 AS is_other
            FROM ranked WHERE rk <= ?
            UNION ALL
            SELECT 'All other (' || count(*) || ' values)',
                   sum(prior_value), sum(current_value), sum(delta),
                   100.0 * sum(delta) / nullif(sum(prior_value), 0),
                   100.0 * sum(delta) / nullif((SELECT total_delta FROM total), 0),
                   'changed', sum(rows_prior), sum(rows_current), 1
            FROM ranked WHERE rk > ?
            HAVING count(*) > 0
        ) ORDER BY is_other, delta {_ORDER[order]}, dim_value
    """, params + [top_n, top_n]


def change_counts(metric: Metric, dimension: str, current: Period, prior: Period,
                  filters: Filters) -> tuple[str, list[Any]]:
    ctes, params = _change_ctes(metric, dimension, current, prior, filters)
    return f"""
        WITH {ctes}
        SELECT count(*) FILTER (WHERE delta < 0) AS n_declined,
               count(*) FILTER (WHERE delta > 0) AS n_grew,
               count(*) FILTER (WHERE delta = 0) AS n_flat,
               count(*) AS n_total
        FROM joined
    """, params


def _pvm_ctes(current: Period, prior: Period, filters: Filters) -> tuple[str, list[Any]]:
    """Price/volume effects per product.

    volume_effect = (units_now - units_before) * price_before
    price_effect  = (price_now - price_before) * units_now      (price = revenue / units)
    They sum exactly to the revenue change. Products sold in only one of the two periods
    (new / lost), or with non-positive units, count entirely as volume.
    """
    wc, pc = _where(current, filters)
    wp, pp = _where(prior, filters)
    return f"""
        cur AS (SELECT "product" AS k, sum("units") AS u, sum("revenue") AS r, count(*) AS n
                FROM {TABLE} WHERE {wc} GROUP BY 1),
        pri AS (SELECT "product" AS k, sum("units") AS u, sum("revenue") AS r, count(*) AS n
                FROM {TABLE} WHERE {wp} GROUP BY 1),
        joined AS (
            SELECT coalesce(cur.k, pri.k) AS dim_value,
                   coalesce(pri.u, 0) AS u0, coalesce(pri.r, 0) AS r0,
                   coalesce(cur.u, 0) AS u1, coalesce(cur.r, 0) AS r1,
                   coalesce(pri.n, 0) AS rows_prior, coalesce(cur.n, 0) AS rows_current
            FROM cur FULL OUTER JOIN pri ON cur.k = pri.k),
        eff AS (
            SELECT *, r1 - r0 AS delta,
                   CASE WHEN u0 > 0 AND u1 > 0 THEN (u1 - u0) * (r0 / u0)
                        ELSE r1 - r0 END AS volume_effect,
                   CASE WHEN u0 > 0 AND u1 > 0 THEN (r1 / u1 - r0 / u0) * u1
                        ELSE 0 END AS price_effect
            FROM joined)
    """, pc + pp


def pvm_totals(current: Period, prior: Period, filters: Filters) -> tuple[str, list[Any]]:
    ctes, params = _pvm_ctes(current, prior, filters)
    return f"""
        WITH {ctes}
        SELECT sum(volume_effect) AS volume_effect, sum(price_effect) AS price_effect,
               sum(delta) AS delta, sum(rows_prior) AS rows_prior,
               sum(rows_current) AS rows_current
        FROM eff
    """, params


def pvm_rows(current: Period, prior: Period, filters: Filters, top_n: int,
             order: str) -> tuple[str, list[Any]]:
    ctes, params = _pvm_ctes(current, prior, filters)
    return f"""
        WITH {ctes},
        ranked AS (SELECT *, row_number() OVER (ORDER BY abs(delta) DESC, dim_value) AS rk
                   FROM eff)
        SELECT * FROM (
            SELECT dim_value, u0 AS prior_units, u1 AS current_units,
                   r0 AS prior_revenue, r1 AS current_revenue,
                   delta, volume_effect, price_effect, 0 AS is_other
            FROM ranked WHERE rk <= ?
            UNION ALL
            SELECT 'All other (' || count(*) || ' products)',
                   sum(u0), sum(u1), sum(r0), sum(r1),
                   sum(delta), sum(volume_effect), sum(price_effect), 1
            FROM ranked WHERE rk > ?
            HAVING count(*) > 0
        ) ORDER BY is_other, delta {_ORDER[order]}, dim_value
    """, params + [top_n, top_n]


def rank(metric: Metric, dimension: str, period: Period, filters: Filters, n: int,
         order: str) -> tuple[str, list[Any]]:
    where, params = _where(period, filters)
    direction = "DESC" if order == "best" else "ASC"
    share = ("100.0 * value / nullif(sum(value) OVER (), 0)" if metric.additive
             else "CAST(NULL AS DOUBLE)")
    return f"""
        WITH g AS (SELECT {qident(dimension)} AS dim_value, {metric.expr} AS value,
                          count(*) AS n
                   FROM {TABLE} WHERE {where} GROUP BY 1)
        SELECT coalesce(CAST(dim_value AS VARCHAR), '(blank)') AS dim_value, value, n,
               {share} AS share_of_total,
               rank() OVER (ORDER BY value {direction}) AS rank_no
        FROM g WHERE value IS NOT NULL
        ORDER BY value {direction}, dim_value
        LIMIT ?
    """, params + [n]


def monthly_trend(metric: Metric, through: Period, filters: Filters, data_min, data_max,
                  months: int) -> tuple[str, list[Any]]:
    """Month-by-month series up to the end of `through`, with month-over-month change."""
    parts, params = ['"date" <= ?'], [through.end]
    for dimension, value in filters:
        parts.append(f"{qident(dimension)} = ?")
        params.append(value)
    where = " AND ".join(parts)
    return f"""
        WITH m AS (
            SELECT CAST(date_trunc('month', "date") AS DATE) AS month_start,
                   {metric.expr} AS value, count(*) AS n
            FROM {TABLE} WHERE {where} GROUP BY 1),
        series AS (
            SELECT month_start, value, n,
                   value - lag(value) OVER w AS change,
                   100.0 * (value - lag(value) OVER w) / nullif(lag(value) OVER w, 0) AS pct_change,
                   (month_start >= CAST(? AS DATE)
                    AND last_day(month_start) <= CAST(? AS DATE)) AS complete
            FROM m WINDOW w AS (ORDER BY month_start))
        SELECT strftime(month_start, '%Y-%m') AS month, value, n, change, pct_change, complete
        FROM (SELECT * FROM series ORDER BY month_start DESC LIMIT ?)
        ORDER BY month_start
    """, params + [data_min, data_max, months]
