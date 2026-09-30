"""Deterministic dataset profile, computed by DuckDB over the stored Parquet file."""
from __future__ import annotations

from typing import Any

from .duck import TABLE, jsonable, qident

_NUMERIC = {"BIGINT", "INTEGER", "DOUBLE", "FLOAT", "HUGEINT", "SMALLINT"}
TOP_VALUES = 10


def profile_dataset(con, roles: dict[str, str]) -> dict[str, Any]:
    described = con.execute(f"DESCRIBE {TABLE}").fetchall()
    row_count = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]

    columns = []
    for name, dtype, *_ in described:
        if name.startswith("_"):  # system columns (e.g. _source_row) are not part of the schema
            continue
        q = qident(name)
        nulls, distinct = con.execute(
            f"SELECT count(*) - count({q}), count(DISTINCT {q}) FROM {TABLE}"
        ).fetchone()
        col: dict[str, Any] = {
            "name": name,
            "dtype": dtype,
            "role": roles.get(name, "dimension"),
            "nulls": nulls,
            "distinct": distinct,
        }
        if dtype in _NUMERIC:
            lo, hi, mean = con.execute(
                f"SELECT min({q}), max({q}), avg({q}) FROM {TABLE}"
            ).fetchone()
            col.update(min=jsonable(lo), max=jsonable(hi), mean=jsonable(mean))
        elif dtype == "DATE":
            lo, hi = con.execute(f"SELECT min({q}), max({q}) FROM {TABLE}").fetchone()
            col.update(min=jsonable(lo), max=jsonable(hi))
        else:
            top = con.execute(
                f"SELECT {q}, count(*) AS n FROM {TABLE} WHERE {q} IS NOT NULL "
                f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {TOP_VALUES}"
            ).fetchall()
            col["top_values"] = [{"value": v, "count": n} for v, n in top]
        columns.append(col)

    lo, hi, months = con.execute(
        f"SELECT min(\"date\"), max(\"date\"), count(DISTINCT date_trunc('month', \"date\")) "
        f"FROM {TABLE}"
    ).fetchone()
    return {
        "row_count": row_count,
        "date_range": {"min": jsonable(lo), "max": jsonable(hi)},
        "distinct_months": months,
        "columns": columns,
    }
