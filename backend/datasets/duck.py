"""The ONLY way BizLens runs SQL against business data.

Every connection is in-memory, sees exactly one dataset (as the view `sales`), can read
exactly one file (that dataset's Parquet), and has its configuration locked. Even if a
malicious or buggy query got past the Phase 4 validator, it cannot read other files,
write files, ATTACH databases, load extensions, or relax these limits.
"""
from __future__ import annotations

import datetime
import decimal
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import duckdb
from django.conf import settings

from . import storage

TABLE = "sales"  # fixed name: the MVP schema is fixed, and each connection sees one dataset


class QueryError(Exception):
    """The SQL is invalid or failed to execute."""


class QueryTimeout(QueryError):
    """The query exceeded its time budget and was interrupted."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qliteral(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def jsonable(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@contextmanager
def open_dataset(dataset) -> Iterator[duckdb.DuckDBPyConnection]:
    path = storage.parquet_path(dataset.id)
    if not path.exists():
        raise FileNotFoundError(f"Parquet file missing for dataset {dataset.id}")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET memory_limit={qliteral(settings.DUCKDB_MEMORY_LIMIT)}")
        con.execute("SET threads=2")
        con.execute(f"CREATE VIEW {TABLE} AS SELECT * FROM read_parquet({qliteral(path)})")
        con.execute(f"SET allowed_paths=[{qliteral(path)}]")
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")  # must be last: nothing can be changed after
        yield con
    finally:
        con.close()


def run_query(
    dataset,
    sql: str,
    params: list[Any] | None = None,
    *,
    max_rows: int = 1000,
    timeout_seconds: float | None = None,
) -> QueryResult:
    timeout = timeout_seconds or settings.DUCKDB_QUERY_TIMEOUT_SECONDS
    with open_dataset(dataset) as con:
        timer = threading.Timer(timeout, con.interrupt)
        timer.start()
        try:
            cursor = con.execute(sql, params or [])
            columns = [d[0] for d in cursor.description]
            fetched = cursor.fetchmany(max_rows + 1)
        except duckdb.InterruptException as exc:
            raise QueryTimeout(f"Query exceeded {timeout:g}s and was cancelled.") from exc
        except duckdb.Error as exc:
            raise QueryError(str(exc)) from exc
        finally:
            timer.cancel()
    truncated = len(fetched) > max_rows
    rows = [[jsonable(v) for v in row] for row in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)
