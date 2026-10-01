"""Evidence-grounded narration. The LLM writes words; code audits them.

Audit (all enforced in code, not trusted to the model):
  * every cited id exists in the evidence digest the model was shown
  * answer, each reasoning step and each action cite at least one id
  * every number in the text appears in the evidence (to the precision it is written at).
    Derived numbers (sums, differences, new percentages) therefore fail: the model may not
    calculate, because SQL already did.
A failed audit is sent back once; if it fails again we return a plain, deterministic fallback
(the engine's own headline) instead of unaudited prose.
"""
from __future__ import annotations

import json
import re

from ai.provider import LLMProvider
from ai.schemas import Narrative
from analytics.results import AnalysisResult

MAX_ROWS_PER_TABLE = 6
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
# "12K", "1.2M", "3 million", "2 lakh": abbreviated magnitudes can't be matched to evidence
_SCALE = re.compile(r"\s?(?:[KMBkmb]\b|bn\b|thousand|million|billion|lakh|crore)")


def _rnd(v):
    return round(v, 2) if isinstance(v, float) else v


class Evidence:
    def __init__(self, result: AnalysisResult):
        self.ids: set[str] = set()
        self.numbers: list[float] = []
        lines: list[str] = []

        def note(value):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.numbers.append(abs(float(_rnd(value))))

        for m in result.measurements:
            self.ids.add(m.id)
            note(m.value)
            lines.append(f"{m.id}: {m.label} = {_rnd(m.value)} [{m.unit}]"
                         + (f" (period {m.period})" if m.period else ""))
        for t in result.tables:
            lines.append(f"-- table {t.id}: {t.title}")
            for i, row in enumerate(t.rows[:MAX_ROWS_PER_TABLE], start=1):
                rid = f"{t.id}#{i}"
                self.ids.add(rid)
                for v in row.values():
                    note(v)
                lines.append(f"{rid}: " + ", ".join(f"{k}={_rnd(v)}" for k, v in row.items()
                                                    if v is not None and k != "is_other"))
        for q in result.queries:
            self.ids.add(q.id)
            lines.append(f"{q.id}: query \"{q.purpose}\" ({q.row_count} rows)")
        for kind, items in (("assumption", result.assumptions), ("warning", result.warnings)):
            lines += [f"[{kind}] {x}" for x in items]
        self.digest = "## EVIDENCE\n" + "\n".join(lines)


def _texts(n: Narrative):
    yield n.answer
    for s in n.reasoning:
        yield s.text
    for a in n.actions:
        yield a.title
        yield a.rationale


def unsupported_numbers(text: str, allowed: list[float]) -> list[str]:
    bad = []
    text_clean = _DATE.sub(" ", text)
    for m in _NUM.finditer(text_clean):
        raw = m.group().replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        x = float(raw)
        if decimals == 0 and x <= 10:  # small counts like "top 3" or "two"
            continue
        tol = 0.5 * 10 ** -decimals + 1e-9
        if _SCALE.match(text_clean, m.end()):
            bad.append(m.group() + "(abbreviated)")
            continue
        if not any(abs(x - a) <= tol for a in allowed):
            bad.append(m.group())
    return bad


def audit(n: Narrative, ev: Evidence) -> list[str]:
    problems: list[str] = []
    cited = [("answer", n.evidence_ids)] + [(f"reasoning step {i}", s.evidence_ids)
                                            for i, s in enumerate(n.reasoning, 1)] + \
            [(f"action '{a.title}'", a.evidence_ids) for a in n.actions]
    for where, ids in cited:
        if not ids:
            problems.append(f"{where} cites no evidence id.")
        unknown = [i for i in ids if i not in ev.ids]
        if unknown:
            problems.append(f"{where} cites unknown ids {unknown}.")
    if not n.reasoning:
        problems.append("reasoning is empty.")
    if not n.actions:
        problems.append("actions is empty.")
    bad = sorted({b for t in _texts(n) for b in unsupported_numbers(t, ev.numbers)})
    if bad:
        problems.append(f"these numbers are not in the evidence: {', '.join(bad)}. "
                        "Use only numbers exactly as given; do not calculate.")
    return problems


def fallback(result: AnalysisResult, ev: Evidence) -> Narrative:
    first = next(iter(sorted(ev.ids)), "q1")
    cite = result.measurements[0].id if result.measurements else first
    return Narrative(answer=result.headline, evidence_ids=[cite], reasoning=[], actions=[])
