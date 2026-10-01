"""Validated inputs for each analysis.

These models are the contract between the request (HTTP today, the LLM planner in Phase 4)
and the engine. They contain no open-ended dicts and no free-form SQL, so they can be handed
to Groq strict-mode structured output unchanged. Dimension names are plain strings here and
are checked against the dataset's real schema at run time.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .metrics import AdditiveMetricName, MetricName
from .periods import ComparisonKind, PeriodSpec


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_Params):
    """Equality filter: dimension = value."""

    dimension: str
    value: str


def _last_month() -> PeriodSpec:
    return PeriodSpec(kind="last_month")


def _all_time() -> PeriodSpec:
    return PeriodSpec(kind="all")


class MetricTotalParams(_Params):
    metric: MetricName = "revenue"
    period: PeriodSpec = Field(default_factory=_all_time)
    filters: list[Filter] = Field(default_factory=list)


class RankByDimensionParams(_Params):
    metric: MetricName = "revenue"
    dimension: str
    period: PeriodSpec = Field(default_factory=_all_time)
    filters: list[Filter] = Field(default_factory=list)
    n: int = Field(default=5, ge=1, le=25)
    order: Literal["best", "worst"] = "best"


class PeriodOverPeriodParams(_Params):
    metric: MetricName = "revenue"
    period: PeriodSpec = Field(default_factory=_last_month)
    comparison: ComparisonKind = "previous_period"
    filters: list[Filter] = Field(default_factory=list)
    trend_months: int = Field(default=12, ge=1, le=36)


class MoversParams(_Params):
    metric: AdditiveMetricName = "revenue"
    dimension: str = "product"
    period: PeriodSpec = Field(default_factory=_last_month)
    comparison: ComparisonKind = "previous_period"
    direction: Literal["decline", "growth", "both"] = "decline"
    n: int = Field(default=10, ge=1, le=25)
    filters: list[Filter] = Field(default_factory=list)


class DriverAnalysisParams(_Params):
    metric: AdditiveMetricName = "revenue"
    period: PeriodSpec = Field(default_factory=_last_month)
    comparison: ComparisonKind = "previous_period"
    dimensions: list[str] | None = None  # default: every required dimension
    filters: list[Filter] = Field(default_factory=list)
    top_n: int = Field(default=8, ge=1, le=20)
