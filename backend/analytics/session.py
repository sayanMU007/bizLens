"""One analysis run: an open sandboxed connection plus a recorder for everything produced."""
from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from datasets import duck

from .errors import AnalysisError
from .periods import DataBounds
from .results import Column, Measurement, QueryRecord, Table


@dataclass(frozen=True)
class DatasetSchema:
    dimensions: tuple[str, ...]  # every text column a user may group or filter by
    default_dimensions: tuple[str, ...]  # the required dimensions, in schema order


def load_schema(dataset) -> DatasetSchema:
    columns = list(dataset.columns.all())
    return DatasetSchema(
        dimensions=tuple(c.name for c in columns if c.role == "dimension"),
        default_dimensions=tuple(c.name for c in columns
                                 if c.role == "dimension" and c.is_required),
    )


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime.date):
        return f"DATE '{value.isoformat()}'"
    return duck.qliteral(str(value))


def render_sql(sql: str, params: list[Any]) -> str:
    """Inline bound parameters for DISPLAY ONLY. Execution always uses bound parameters."""
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"SQL has {len(parts) - 1} placeholders but {len(params)} params")
    out = [parts[0]]
    for value, tail in zip(params, parts[1:]):
        out.append(_literal(value))
        out.append(tail)
    return "".join(out)


@dataclass(frozen=True)
class Rows:
    query_id: str
    columns: list[str]
    rows: list[dict[str, Any]]


class Session:
    MAX_ROWS = 5000

    def __init__(self, dataset, con, schema: DatasetSchema):
        self.dataset, self.con, self.schema = dataset, con, schema
        self.queries: list[QueryRecord] = []
        self.measurements: list[Measurement] = []
        self.tables: list[Table] = []
        self.assumptions: list[str] = []
        self.warnings: list[str] = []
        self._bounds: DataBounds | None = None

    def run(self, purpose: str, sql: str, params: list[Any] | None = None) -> Rows:
        params = list(params or [])
        sql = dedent(sql).strip()
        started = time.perf_counter()
        try:
            result = duck.execute(self.con, sql, params, max_rows=self.MAX_ROWS)
        except duck.QueryTimeout:
            raise
        except duck.QueryError as exc:  # our own SQL failed: a bug, never user error
            raise AnalysisError("query_failed", f"Internal query error while: {purpose}.",
                                {"detail": str(exc)[:300]}) from exc
        if result.truncated:
            raise AnalysisError("result_too_large", f"Too many rows while: {purpose}.")
        query_id = f"q{len(self.queries) + 1}"
        rows = [dict(zip(result.columns, row)) for row in result.rows]
        self.queries.append(QueryRecord(
            id=query_id, purpose=purpose, sql=sql,
            params=[duck.jsonable(p) if not isinstance(p, datetime.date) else p.isoformat()
                    for p in params],
            sql_rendered=render_sql(sql, params), row_count=len(rows),
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))
        return Rows(query_id, result.columns, rows)

    def bounds(self) -> DataBounds:
        if self._bounds is None:
            rows = self.run(
                "Find the first and last date in the data (anchor for relative periods)",
                f'SELECT min("date") AS min_date, max("date") AS max_date FROM {duck.TABLE}',
            )
            row = rows.rows[0]
            self._bounds = DataBounds(datetime.date.fromisoformat(row["min_date"]),
                                      datetime.date.fromisoformat(row["max_date"]))
        return self._bounds

    def measure(self, label: str, value, unit: str, query_id: str,
                rows_used: int | None = None, period: str | None = None) -> str:
        measurement_id = f"m{len(self.measurements) + 1}"
        self.measurements.append(Measurement(
            id=measurement_id, label=label, value=value, unit=unit, query_id=query_id,
            rows_used=rows_used, period=period))
        return measurement_id

    def add_table(self, table_id: str, title: str, rows: Rows, columns: list[Column]) -> None:
        self.tables.append(Table(id=table_id, title=title, columns=columns,
                                 rows=rows.rows, query_id=rows.query_id))
