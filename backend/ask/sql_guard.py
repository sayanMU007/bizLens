"""Static validation of LLM-written SQL. Runs BEFORE anything executes.

Uses DuckDB's own parser (not regexes), so what we inspect is exactly what DuckDB would run:

  * `extract_statements`  -> exactly one statement, and it is a SELECT
  * `json_serialize_sql`  -> the parsed syntax tree, which we walk to enforce:
        - the only tables read are `sales` and the query's own CTE names
        - no schema-qualified tables (information_schema, other catalogs)
        - no table functions (read_csv, glob, duckdb_settings, ...)
        - no blocked scalar functions (read_text, getenv, ...)
        - no bound parameters (the query must be self-contained and paste-and-run)
        - `sales` is actually read (a query of constants is invented data, not analysis)

This is the first of two layers. The second is the sandboxed connection in `datasets.duck`
(no file access, config locked, one thread, timeout), which still holds if a check here
has a gap.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import duckdb

from datasets.duck import TABLE

MAX_SQL_CHARS = 4000

# Scalar/table functions that touch the environment. The sandbox blocks them anyway; we
# reject early so the model gets a clear repair message instead of a runtime failure.
_BLOCKED_FUNCTION_PREFIXES = ("read_", "write_", "duckdb_", "pragma_", "glob", "sniff_",
                              "parquet_", "json_serialize", "load_", "install_")
_BLOCKED_FUNCTIONS = {"getenv", "current_setting", "set_config", "query", "query_table",
                      "checkpoint", "force_checkpoint", "which_secret", "current_schema",
                      "current_database", "version"}


class SQLRejected(Exception):
    """The SQL failed static validation. The message is written for the model to repair from."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _parse(sql: str) -> dict:
    con = duckdb.connect(":memory:")  # parse-only: holds no data and is never queried
    try:
        try:
            statements = con.extract_statements(sql)
        except duckdb.Error as exc:
            raise SQLRejected("syntax_error", f"SQL syntax error: {_clean(exc)}") from exc
        if len(statements) != 1:
            raise SQLRejected("multiple_statements",
                              f"Send exactly one SQL statement; found {len(statements)}.")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise SQLRejected("not_select",
                              "Only a single SELECT statement is allowed (WITH ... SELECT is fine). "
                              "No DDL, DML, PRAGMA, SET, ATTACH, COPY or EXPLAIN.")
        tree = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    finally:
        con.close()
    if tree.get("error"):
        raise SQLRejected("not_select", f"Could not parse as a SELECT: {tree.get('error_message')}")
    return tree


def _clean(exc: Exception) -> str:
    return str(exc).split("\n")[0][:300]


def validate_sql(sql: str) -> ValidatedSQL:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SQLRejected("empty", "The SQL is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise SQLRejected("too_long", f"The SQL is longer than {MAX_SQL_CHARS} characters; simplify it.")
    tree = _parse(sql)

    cte_names: set[str] = set()
    for node in _walk(tree):
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            cte_names.update(str(e["key"]).lower() for e in cte_map.get("map", []))

    if TABLE in cte_names:  # would let a query "read sales" while actually reading invented rows
        raise SQLRejected("cte_shadows_table",
                          f"Do not name a CTE `{TABLE}`; that is the real table. Pick another name.")

    reads_sales = False
    for node in _walk(tree):
        kind = node.get("type")
        if kind == "TABLE_FUNCTION":
            name = (node.get("function") or {}).get("function_name", "?")
            raise SQLRejected("table_function",
                              f"Table function '{name}' is not allowed. Read only from `{TABLE}`.")
        if kind == "BASE_TABLE":
            name = str(node.get("table_name", "")).lower()
            if node.get("schema_name") or node.get("catalog_name"):
                raise SQLRejected("qualified_table",
                                  f"Do not qualify table names. Use `{TABLE}` or a CTE defined in the query.")
            if name == TABLE:
                reads_sales = True
            elif name not in cte_names:
                raise SQLRejected("unknown_table",
                                  f"Table '{node.get('table_name')}' does not exist. "
                                  f"The only table is `{TABLE}`.")
        if node.get("class") == "FUNCTION":
            name = str(node.get("function_name", "")).lower()
            if name in _BLOCKED_FUNCTIONS or name.startswith(_BLOCKED_FUNCTION_PREFIXES):
                raise SQLRejected("blocked_function", f"Function '{name}' is not allowed.")
        if node.get("class") == "PARAMETER" or kind == "VALUE_PARAMETER":
            raise SQLRejected("parameters_not_allowed",
                              "Do not use ? or $1 parameters; write literal values in the SQL.")
    if not reads_sales:
        raise SQLRejected("no_data_read", f"The query must read from `{TABLE}`.")
    return ValidatedSQL(sql=sql)
