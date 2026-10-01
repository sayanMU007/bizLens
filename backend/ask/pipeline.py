"""Question -> plan -> (vetted analysis | validated custom SQL) -> traced result.

The LLM is called for exactly two things, both returning schema-constrained JSON:
  1. `AnalysisPlan`  (always)
  2. `GeneratedSQL`  (only when the plan routes to custom SQL)
Everything it returns is checked by code before anything runs, and a rejection is fed back to
the model ONCE (plans) or TWICE (SQL) so it can repair its own output. After that the pipeline
stops with a clear error instead of guessing. The model never sees query results and never
produces a number: every figure in the response came out of DuckDB.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ai.factory import get_llm_provider
from ai.prompts import load_prompt
from ai.provider import LLMError, LLMProvider, ModelTier
from ai.schemas import AnalysisPlan, GeneratedSQL, Narrative
from analytics.analyses import run_analysis
from analytics.errors import AnalysisError
from analytics.session import load_schema
from datasets import duck

from .context import build_context
from .custom_sql import execute_custom_sql
from .narrative import Evidence, audit, fallback
from .plan_check import PlanRejected, check_plan
from .results import AskResponse, TraceStep
from .sql_guard import SQLRejected, validate_sql

MIN_QUESTION_CHARS, MAX_QUESTION_CHARS = 3, 500
MAX_PLAN_REPAIRS = 1
MAX_SQL_REPAIRS = 2
# Engine errors the planner can fix by changing its request. Anything else (e.g. the data has no
# complete month) is a fact about the data, and re-asking the model would not change it.
REPAIRABLE_ANALYSIS_CODES = {"unknown_dimension", "invalid_params", "period_outside_data",
                             "no_comparison_data", "comparison_unavailable"}
DEFAULT_UNANSWERABLE = ("BizLens can't answer that from this dataset. Try asking about revenue, "
                        "cost, units, profit or margin by region, product, category, customer, "
                        "salesperson or month.")


class AskError(Exception):
    """The pipeline could not produce an answer. `trace` shows exactly how far it got."""

    def __init__(self, code: str, message: str, trace: list[TraceStep] | None = None):
        super().__init__(message)
        self.code, self.message, self.trace = code, message, trace or []


def normalize_question(question) -> str:
    text = re.sub(r"\s+", " ", question).strip() if isinstance(question, str) else ""
    if not (MIN_QUESTION_CHARS <= len(text) <= MAX_QUESTION_CHARS):
        raise AskError("invalid_question",
                       f"Ask a question between {MIN_QUESTION_CHARS} and {MAX_QUESTION_CHARS} characters.")
    return text


@dataclass
class _Run:
    dataset: object
    provider: LLMProvider
    narrate: bool = True
    trace: list[TraceStep] = field(default_factory=list)

    def step(self, stage: str, status: str = "ok", detail: str | None = None, **extra) -> None:
        self.trace.append(TraceStep(stage=stage, status=status, detail=detail, **extra))

    def call(self, stage: str, *, system: str, user: str, schema):
        """One LLM call. LLMError subclasses propagate (the view maps them to HTTP errors)."""
        started = time.perf_counter()
        result = self.provider.generate_structured(
            system=system, user=user, schema=schema, tier=ModelTier.SMART,
            temperature=0.0, max_output_tokens=4096)
        self.step(stage, model=result.model, total_tokens=result.usage.total_tokens,
                  latency_ms=int((time.perf_counter() - started) * 1000))
        return result.data


def _repair_block(previous: str, reason: str, what: str) -> str:
    return (f"\n\n## REPAIR\nYour previous {what} was rejected by validation.\n"
            f"Previous {what}: {previous}\nReason: {reason}\nReturn a corrected {what}.")


def ask(dataset, question: str, provider: LLMProvider | None = None, *,
        narrate: bool = True) -> AskResponse:
    question = normalize_question(question)
    run = _Run(dataset=dataset, provider=provider or get_llm_provider(), narrate=narrate)
    schema = load_schema(dataset)
    context = build_context(dataset)
    system = load_prompt("plan_question")
    base_user = f"{context}\n\n## QUESTION\n<question>\n{question}\n</question>"

    user, plan, last_error = base_user, None, ""
    for attempt in range(MAX_PLAN_REPAIRS + 1):
        plan = run.call("plan", system=system, user=user, schema=AnalysisPlan)

        if plan.route == "unanswerable":
            return AskResponse(question=question, status="unanswerable", route="none", plan=plan,
                               resolved_params=None, analysis=None, sql_explanation=None,
                               message=(plan.message or "").strip() or DEFAULT_UNANSWERABLE,
                               trace=run.trace)

        if plan.route == "custom_sql":
            if plan.sql_goal and plan.sql_goal.strip():
                return _answer_with_custom_sql(run, question, plan, plan.sql_goal.strip())
            last_error = "route 'custom_sql' requires `sql_goal`."
            run.step("plan_check", "rejected", last_error)
        else:
            try:
                checked = check_plan(dataset, schema, plan)
            except PlanRejected as exc:
                last_error = exc.message
                run.step("plan_check", "rejected", exc.message)
            else:
                run.step("plan_check", detail=" ".join(checked.notes) or None)
                try:
                    result = run_analysis(dataset, checked.analysis, checked.params.model_dump())
                except AnalysisError as exc:
                    if exc.code not in REPAIRABLE_ANALYSIS_CODES:
                        run.step("run_analysis", "rejected", exc.message)
                        raise AskError(exc.code, exc.message, run.trace) from exc
                    last_error = exc.message
                    run.step("run_analysis", "rejected", exc.message)
                else:
                    run.step("run_analysis")
                    return _finish(run, AskResponse(
                        question=question, status="answered", route="analysis", plan=plan,
                        resolved_params=checked.params.model_dump(mode="json"), analysis=result,
                        sql_explanation=None, message=None, trace=[]))

        if attempt < MAX_PLAN_REPAIRS:
            user = base_user + _repair_block(plan.model_dump_json(), last_error, "plan")

    raise AskError("could_not_plan",
                   f"Could not turn that question into a valid analysis: {last_error} "
                   "Try rephrasing it.", run.trace)


def _answer_with_custom_sql(run: _Run, question: str, plan: AnalysisPlan, goal: str) -> AskResponse:
    system = load_prompt("generate_sql")
    base_user = f"{build_context(run.dataset)}\n\n## GOAL\n<goal>\n{goal}\n</goal>"
    user, last_error = base_user, ""
    with duck.open_dataset(run.dataset) as con:
        for attempt in range(MAX_SQL_REPAIRS + 1):
            generated = run.call("sql_generate", system=system, user=user, schema=GeneratedSQL)
            try:
                validated = validate_sql(generated.sql)
            except SQLRejected as exc:
                last_error = exc.message
                run.step("sql_validate", "rejected", exc.message)
            else:
                run.step("sql_validate")
                try:
                    result = execute_custom_sql(con, run.dataset, validated.sql, goal)
                except duck.QueryTimeout as exc:
                    run.step("sql_execute", "rejected", str(exc))
                    raise
                except duck.QueryError as exc:
                    last_error = str(exc).split("\n")[0][:300]
                    run.step("sql_execute", "rejected", last_error)
                else:
                    run.step("sql_execute")
                    return _finish(run, AskResponse(
                        question=question, status="answered", route="custom_sql", plan=plan,
                        resolved_params=None, analysis=result,
                        sql_explanation=generated.explanation.strip() or None, message=None,
                        trace=[]))
            if attempt < MAX_SQL_REPAIRS:
                user = base_user + _repair_block(generated.sql, last_error, "SQL")
    raise AskError("could_not_generate_sql",
                   f"Could not produce a valid query for that question: {last_error} "
                   "Try rephrasing it.", run.trace)


def _finish(run: _Run, response: AskResponse) -> AskResponse:
    """Attach the audited narrative. A narration failure never costs the user the answer."""
    if run.narrate:
        _narrate(run, response)
    response.trace = list(run.trace)
    return response


def _narrate(run: _Run, response: AskResponse) -> None:
    ev = Evidence(response.analysis)
    system = load_prompt("narrate")
    base_user = (f"## QUESTION\n<question>\n{response.question}\n</question>\n\n{ev.digest}")
    user, problems = base_user, []
    try:
        for attempt in range(2):
            narrative = run.call("narrate", system=system, user=user, schema=Narrative)
            problems = audit(narrative, ev)
            if not problems:
                run.step("narrative_audit")
                response.narrative, response.narrative_status = narrative, "grounded"
                return
            run.step("narrative_audit", "rejected", " ".join(problems))
            user = base_user + _repair_block(narrative.model_dump_json(), " ".join(problems),
                                             "explanation")
    except LLMError as exc:
        run.step("narrate", "rejected", exc.__class__.__name__)
    response.narrative, response.narrative_status = fallback(response.analysis, ev), "fallback"
