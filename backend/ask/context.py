"""The DATASET section sent to the LLM: schema and coverage only, never raw rows.

Built entirely from the stored profile and the metric/analysis registries, so it is
deterministic and costs no queries. The only data values that leave the server are the top
labels of each text column (e.g. region names), which the planner needs to write filters.
Labels are sanitized first: an uploaded CSV is untrusted and could contain text that looks like
instructions, so values are truncated and stripped of control characters and newlines.
"""
from __future__ import annotations

import datetime
import re

from analytics.analyses import REGISTRY
from analytics.errors import AnalysisError
from analytics.metrics import METRICS
from analytics.periods import DataBounds, PeriodSpec, resolve_comparison, resolve_period

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
MAX_LABEL_CHARS = 40


def clean_label(value) -> str:
    return _CONTROL.sub(" ", str(value)).strip()[:MAX_LABEL_CHARS]


def _date_anchors(profile: dict) -> list[str]:
    rng = profile.get("date_range") or {}
    try:
        bounds = DataBounds(datetime.date.fromisoformat(rng["min"]),
                            datetime.date.fromisoformat(rng["max"]))
        current, _ = resolve_period(PeriodSpec(kind="last_month"), bounds)
    except (KeyError, ValueError, TypeError, AnalysisError):
        return [f"Date coverage: {rng.get('min')} to {rng.get('max')}.",
                "No complete calendar month exists in the data."]
    lines = [f"Date coverage: {bounds.min} to {bounds.max}.",
             f"'Last month' = {current.label} ({current.start} to {current.end}), "
             "the last complete month in the data."]
    try:
        prior = resolve_comparison(current, "previous_period", bounds)
        lines.append(f"The month before it = {prior.label} ({prior.start} to {prior.end}).")
    except AnalysisError:
        lines.append("There is no earlier month in the data to compare against.")
    return lines


def build_context(dataset) -> str:
    profile = dataset.profile or {}
    by_name = {c["name"]: c for c in profile.get("columns", [])}
    lines = ["## DATASET",
             f"Table `sales` ({dataset.row_count:,} rows; each row is one transaction line).",
             *_date_anchors(profile), "", "Columns:"]
    for col in dataset.columns.all():
        stats = by_name.get(col.name, {})
        text = f"- {col.name} ({col.dtype}, {col.role})"
        if col.role == "dimension" and stats.get("top_values"):
            labels = ", ".join(clean_label(t["value"]) for t in stats["top_values"])
            text += f"; {stats.get('distinct', '?')} distinct values; most common: {labels}"
        elif col.role == "measure" and "min" in stats:
            text += f"; range {stats['min']} to {stats['max']}"
        lines.append(text)
    lines += ["", "Metrics (name: SQL definition):"]
    lines += [f"- {m.name}: {m.expr} ({'additive' if m.additive else 'ratio, not additive'})"
              for m in METRICS.values()]
    lines += ["", "Vetted analyses:"]
    lines += [f"- {a.name}: {a.description}" for a in REGISTRY.values()]
    return "\n".join(lines)
