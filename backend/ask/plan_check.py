"""Turn an LLM `AnalysisPlan` into checked engine parameters. Pure code, no LLM.

The planner is not trusted. This module:
  1. checks the plan is internally consistent for its route,
  2. maps the flat plan onto the exact params model of the chosen analysis
     (fields that analysis does not use are dropped and reported, never forwarded),
  3. lets Pydantic enforce every type, range and enum,
  4. resolves dimension names and filter values against the REAL dataset
     (case-insensitive match is corrected; anything else is rejected with the valid options).

Every rejection is a `PlanRejected` whose message is written for the planner to repair from.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from ai.schemas import AnalysisPlan
from analytics.analyses import REGISTRY
from analytics.params import Filter
from analytics.session import DatasetSchema
from datasets import duck

# plan field -> params-model field(s) it may feed. `n` feeds `n` or `top_n`, whichever exists.
_DIRECT_FIELDS = ("metric", "period", "comparison", "dimension", "dimensions", "order", "direction")


class PlanRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class CheckedPlan:
    analysis: str
    params: BaseModel
    notes: list[str] = field(default_factory=list)  # corrections made / fields ignored


def _errors_text(exc: ValidationError) -> str:
    items = json.loads(exc.json(include_url=False, include_context=False))
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'plan'}: {e['msg']}" for e in items)


def _resolve_dimension(name: str, schema: DatasetSchema, notes: list[str]) -> str:
    if name in schema.dimensions:
        return name
    matches = [d for d in schema.dimensions if d.lower() == name.strip().lower()]
    if len(matches) == 1:
        notes.append(f"Dimension '{name}' corrected to '{matches[0]}'.")
        return matches[0]
    raise PlanRejected("unknown_dimension",
                       f"'{name}' is not a dimension in this dataset. "
                       f"Available: {', '.join(schema.dimensions)}.")


def _resolve_filter(dataset, flt: Filter, schema: DatasetSchema, notes: list[str]) -> Filter:
    dimension = _resolve_dimension(flt.dimension, schema, notes)
    value = flt.value.strip()
    col = duck.qident(dimension)  # safe: `dimension` is one of the dataset's own column names
    found = [r[0] for r in duck.run_query(
        dataset,
        f"SELECT DISTINCT {col} FROM {duck.TABLE} WHERE lower({col}) = lower(?) LIMIT 5",
        [value], max_rows=5).rows]
    if value in found:
        return Filter(dimension=dimension, value=value)
    if len(found) == 1:
        notes.append(f"Filter value '{value}' corrected to '{found[0]}' (case).")
        return Filter(dimension=dimension, value=found[0])
    examples = [t["value"] for c in (dataset.profile or {}).get("columns", [])
                if c["name"] == dimension for t in c.get("top_values", [])]
    raise PlanRejected("unknown_filter_value",
                       f"No rows have {dimension} = '{flt.value}'. "
                       f"Most common values of {dimension}: {', '.join(map(str, examples))}.")


def check_plan(dataset, schema: DatasetSchema, plan: AnalysisPlan) -> CheckedPlan:
    """Only for route == 'analysis'. Raises PlanRejected."""
    if plan.route != "analysis" or plan.analysis is None:
        raise PlanRejected("missing_analysis",
                           "route 'analysis' requires the `analysis` field to be set.")
    spec = REGISTRY.get(plan.analysis)
    if spec is None:  # the schema enum prevents this; kept because this is the trust boundary
        raise PlanRejected("unknown_analysis", f"Unknown analysis '{plan.analysis}'.")

    allowed = set(spec.params_model.model_fields)
    notes: list[str] = []
    raw: dict = {}
    ignored: list[str] = []

    for name in _DIRECT_FIELDS:
        value = getattr(plan, name)
        if value is None:
            continue
        if name in allowed:
            raw[name] = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        else:
            ignored.append(name)
    if plan.n is not None:
        target = "n" if "n" in allowed else "top_n" if "top_n" in allowed else None
        if target:
            raw[target] = plan.n
        else:
            ignored.append("n")
    if ignored:
        notes.append(f"Ignored fields {', '.join(ignored)}: not used by {plan.analysis}.")

    if "dimension" in raw:
        raw["dimension"] = _resolve_dimension(raw["dimension"], schema, notes)
    if "dimensions" in raw:
        raw["dimensions"] = [_resolve_dimension(d, schema, notes) for d in raw["dimensions"]]
    if spec.name == "rank_by_dimension" and "dimension" not in raw:
        raise PlanRejected("missing_dimension", "rank_by_dimension requires `dimension`.")

    if "filters" in allowed:
        raw["filters"] = [_resolve_filter(dataset, f, schema, notes).model_dump() for f in plan.filters]

    try:
        params = spec.params_model.model_validate(raw)
    except ValidationError as exc:
        raise PlanRejected("invalid_params", f"Invalid parameters for {plan.analysis}: "
                                             f"{_errors_text(exc)}") from exc
    return CheckedPlan(analysis=plan.analysis, params=params, notes=notes)
