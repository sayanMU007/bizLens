"""Time periods. Relative phrases are resolved against the DATA, not today's date.

"Last month" means the last COMPLETE calendar month present in the dataset. A trailing
partial month is excluded (and reported), so a full month is never compared with a partial one.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .errors import AnalysisError

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
ComparisonKind = Literal["previous_period", "same_period_last_year"]


class PeriodSpec(BaseModel):
    """What the caller (or, in Phase 4, the LLM planner) asks for."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["all", "last_month", "month", "range"] = "last_month"
    month: str | None = None  # "YYYY-MM" when kind == "month"
    start: str | None = None  # "YYYY-MM-DD", inclusive, when kind == "range"
    end: str | None = None  # "YYYY-MM-DD", inclusive, when kind == "range"

    @model_validator(mode="after")
    def _consistent(self):
        if self.kind == "month":
            if not self.month or not _MONTH_RE.match(self.month):
                raise ValueError("month must look like 'YYYY-MM' when kind is 'month'")
        elif self.kind == "range":
            try:
                start, end = date.fromisoformat(self.start or ""), date.fromisoformat(self.end or "")
            except ValueError:
                raise ValueError("start and end must be ISO dates (YYYY-MM-DD) when kind is 'range'")
            if start > end:
                raise ValueError("start must not be after end")
        return self


@dataclass(frozen=True)
class DataBounds:
    min: date
    max: date


@dataclass(frozen=True)
class Period:
    start: date  # inclusive
    end: date  # inclusive
    label: str
    kind: str  # "all" | "last_month" | "month" | "range"
    complete: bool  # does the data cover the whole period?


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 Feb -> 28 Feb
        return d.replace(year=d.year + years, day=28)


def _covers(b: DataBounds, start: date, end: date) -> bool:
    return b.min <= start and b.max >= end


def _overlaps(b: DataBounds, start: date, end: date) -> bool:
    return start <= b.max and end >= b.min


def _outside(label: str, b: DataBounds, code: str = "period_outside_data") -> AnalysisError:
    return AnalysisError(code, f"The data has no rows for {label}. It covers {b.min} to {b.max}.",
                         {"data_min": str(b.min), "data_max": str(b.max)})


def resolve_period(spec: PeriodSpec, b: DataBounds) -> tuple[Period, list[str]]:
    """Return the concrete period and any assumptions made while resolving it."""
    assumptions: list[str] = []
    if spec.kind == "all":
        return Period(b.min, b.max, f"{b.min} to {b.max}", "all", True), assumptions

    if spec.kind == "last_month":
        ends_on_month_end = b.max == month_bounds(b.max.year, b.max.month)[1]
        year, month = (b.max.year, b.max.month) if ends_on_month_end else _prev_month(
            b.max.year, b.max.month)
        start, end = month_bounds(year, month)
        if start < b.min:
            raise AnalysisError(
                "no_complete_month",
                f"The data ({b.min} to {b.max}) does not contain a complete calendar month.",
            )
        label = f"{year}-{month:02d}"
        text = (f"'Last month' means {label}, the last complete month in the data "
                f"(data runs {b.min} to {b.max}), not the current calendar month.")
        if not ends_on_month_end:
            text += (f" {b.max:%Y-%m} was excluded because the data stops on {b.max}, "
                     "so that month is incomplete.")
        assumptions.append(text)
        return Period(start, end, label, "last_month", True), assumptions

    if spec.kind == "month":
        year, month = (int(x) for x in _MONTH_RE.match(spec.month).groups())
        start, end = month_bounds(year, month)
        label = f"{year}-{month:02d}"
        if not _overlaps(b, start, end):
            raise _outside(label, b)
        return Period(start, end, label, "month", _covers(b, start, end)), assumptions

    start, end = date.fromisoformat(spec.start), date.fromisoformat(spec.end)
    label = f"{start} to {end}"
    if not _overlaps(b, start, end):
        raise _outside(label, b)
    return Period(start, end, label, "range", _covers(b, start, end)), assumptions


def resolve_comparison(current: Period, kind: ComparisonKind, b: DataBounds) -> Period:
    if current.kind == "all":
        raise AnalysisError(
            "comparison_unavailable",
            "'All data' has nothing to compare against. Choose a month or a date range.",
        )
    if current.kind in ("month", "last_month"):
        year, month = current.start.year, current.start.month
        year, month = _prev_month(year, month) if kind == "previous_period" else (year - 1, month)
        start, end = month_bounds(year, month)
        label = f"{year}-{month:02d}"
    elif kind == "previous_period":
        length = (current.end - current.start).days + 1
        end = current.start - timedelta(days=1)
        start = end - timedelta(days=length - 1)
        label = f"{start} to {end}"
    else:
        start, end = _shift_year(current.start, -1), _shift_year(current.end, -1)
        label = f"{start} to {end}"
    if not _overlaps(b, start, end):
        raise _outside(label, b, "no_comparison_data")
    return Period(start, end, label, current.kind if current.kind != "last_month" else "month",
                  _covers(b, start, end))
