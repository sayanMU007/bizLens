"""What every analysis returns. Built so Phase 5 can turn each Measurement into an Evidence
record without re-deriving anything: every number points at the query that produced it."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class QueryRecord(BaseModel):
    id: str
    purpose: str
    sql: str  # exactly what was executed (with ? placeholders)
    params: list[Any]  # the bound parameter values, in order
    sql_rendered: str  # same query with params inlined: paste-and-run for a human
    row_count: int
    duration_ms: int


class Measurement(BaseModel):
    """One headline number."""

    id: str
    label: str
    value: float | int | None
    unit: str  # "currency" | "units" | "transactions" | "count" | "%"
    query_id: str
    rows_used: int | None  # source transaction rows that fed this number
    period: str | None = None


class Column(BaseModel):
    name: str
    unit: str | None = None


class Table(BaseModel):
    id: str
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    query_id: str


class PeriodInfo(BaseModel):
    label: str
    start: str
    end: str
    kind: str
    complete: bool


class DatasetRef(BaseModel):
    id: str
    name: str
    sha256: str  # fingerprint of the exact uploaded file
    row_count: int


class AnalysisResult(BaseModel):
    analysis: str
    title: str
    headline: str  # deterministic sentence built from the numbers below
    params: dict[str, Any]  # the fully resolved request, defaults filled in
    dataset: DatasetRef
    current_period: PeriodInfo | None
    comparison_period: PeriodInfo | None
    measurements: list[Measurement]
    tables: list[Table]
    queries: list[QueryRecord]
    assumptions: list[str]
    warnings: list[str]
