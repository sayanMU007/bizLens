"""Pydantic contracts for every structured LLM call.

  Phase 1: LLMSmokeTest
  Phase 4: AnalysisPlan, GeneratedSQL
  Phase 6: Narrative (findings / decision / actions, each carrying evidence_ids)

Rules for schemas sent to Groq strict mode:
  * no open-ended dicts (see strict_schema.py)
  * optional values are `X | None` (they are still *required* keys)
  * value constraints are fine to declare; Pydantic enforces them after the call
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analytics.metrics import MetricName
from analytics.params import Filter
from analytics.periods import ComparisonKind, PeriodSpec


class LLMSmokeTest(BaseModel):
    status: Literal["ok"]
    echo: str


AnalysisName = Literal[
    "metric_total", "rank_by_dimension", "period_over_period", "movers", "driver_analysis"
]


class AnalysisPlan(BaseModel):
    """What the planner may say. It is a REQUEST, never an answer: it carries no numbers.

    The shape is deliberately flat (one set of optional fields shared by all analyses) so a
    small model can fill it reliably. `ask.plan_check` maps it onto the exact params model of
    the chosen analysis, dropping fields that analysis does not use, and validates the result.
    """

    model_config = ConfigDict(extra="forbid")

    route: Literal["analysis", "custom_sql", "unanswerable"]
    reasoning: str = Field(description="One or two sentences: why this route and analysis.")

    # route == "analysis"
    analysis: AnalysisName | None
    metric: MetricName | None
    period: PeriodSpec | None
    comparison: ComparisonKind | None
    dimension: str | None  # rank_by_dimension / movers
    dimensions: list[str] | None  # driver_analysis
    filters: list[Filter]
    n: int | None
    order: Literal["best", "worst"] | None  # rank_by_dimension
    direction: Literal["decline", "growth", "both"] | None  # movers

    # route == "custom_sql"
    sql_goal: str | None  # what the query must compute, in plain English

    # route == "unanswerable"
    message: str | None  # what to tell the user


class GeneratedSQL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(description="One DuckDB SELECT statement over the `sales` table.")
    explanation: str = Field(description="One or two plain-English sentences: what it computes.")


class ReasoningStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    rationale: str
    evidence_ids: list[str]
    priority: Literal["high", "medium", "low"]


class Narrative(BaseModel):
    """Phase 6: wording only. Every claim cites evidence ids; code verifies ids and numbers."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence_ids: list[str]
    reasoning: list[ReasoningStep]
    actions: list[RecommendedAction]
