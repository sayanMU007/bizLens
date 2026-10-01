"""The response contract of POST /api/datasets/<id>/ask/."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from ai.schemas import AnalysisPlan, Narrative
from analytics.results import AnalysisResult


class TraceStep(BaseModel):
    """One step of the pipeline, in order. No prompt or response text is ever stored here."""

    stage: str  # plan | plan_check | run_analysis | sql_generate | sql_validate | sql_execute
    status: Literal["ok", "rejected"]
    detail: str | None = None  # why it was rejected, or what was corrected
    model: str | None = None
    latency_ms: int | None = None
    total_tokens: int | None = None


class AskResponse(BaseModel):
    question: str
    status: Literal["answered", "unanswerable"]
    route: Literal["analysis", "custom_sql", "none"]
    plan: AnalysisPlan  # what the LLM asked for (a request, never an answer)
    resolved_params: dict[str, Any] | None  # what was actually run, after code checks
    analysis: AnalysisResult | None  # same contract for vetted and custom SQL results
    sql_explanation: str | None  # custom SQL only: the model's own description (not verified)
    message: str | None  # unanswerable only
    narrative: Narrative | None = None  # wording only; audited against the evidence
    narrative_status: Literal["grounded", "fallback", "skipped"] = "skipped"
    trace: list[TraceStep]
