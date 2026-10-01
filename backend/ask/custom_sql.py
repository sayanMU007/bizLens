"""Execute validated custom SQL and package it in the same contract as a vetted analysis."""
from __future__ import annotations

import time

from analytics.results import (AnalysisResult, Column, DatasetRef, QueryRecord, Table)
from datasets import duck

CUSTOM_MAX_ROWS = 200

CUSTOM_SQL_WARNING = (
    "This answer comes from AI-written SQL, not one of BizLens's vetted analyses. The SQL was "
    "checked to be one read-only SELECT over the sales table and ran in a locked sandbox, but its "
    "logic was not independently reconciled. Read the SQL before relying on the numbers.")


def execute_custom_sql(con, dataset, sql: str, goal: str) -> AnalysisResult:
    """Raises duck.QueryError (repairable) or duck.QueryTimeout (not)."""
    started = time.perf_counter()
    result = duck.execute(con, sql, max_rows=CUSTOM_MAX_ROWS)
    rows = [dict(zip(result.columns, row)) for row in result.rows]
    warnings = [CUSTOM_SQL_WARNING]
    if result.truncated:
        warnings.append(f"Only the first {CUSTOM_MAX_ROWS} rows are shown.")
    if not rows:
        warnings.append("The query returned no rows.")
    record = QueryRecord(
        id="q1", purpose=goal, sql=sql, params=[], sql_rendered=sql, row_count=len(rows),
        duration_ms=int((time.perf_counter() - started) * 1000))
    plural = "row" if len(rows) == 1 else "rows"
    return AnalysisResult(
        analysis="custom_sql", title="Custom query",
        headline=f"The custom query returned {len(rows)} {plural}.",
        params={"sql_goal": goal},
        dataset=DatasetRef(id=str(dataset.id), name=dataset.name, sha256=dataset.sha256,
                           row_count=dataset.row_count),
        current_period=None, comparison_period=None, measurements=[],
        tables=[Table(id="t1", title="Query result", columns=[Column(name=c) for c in result.columns],
                      rows=rows, query_id="q1")],
        queries=[record],
        assumptions=["No vetted analysis fit this question, so a custom query was used."],
        warnings=warnings)
