"""The metric registry: the ONLY place business metrics are defined.

Each metric is a fixed SQL aggregate over the `sales` view. No metric expression is ever
built from user input. `additive` metrics can be split across dimensions (their parts sum to
the whole); ratio metrics (margin, average price) cannot.

Units: percentages are stored in percent points (12.5 means 12.5%), so numbers in results,
evidence and narration all use one convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MetricName = Literal["revenue", "cost", "units", "profit", "transactions", "margin", "avg_unit_price"]
AdditiveMetricName = Literal["revenue", "cost", "units", "profit", "transactions"]


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    expr: str  # SQL aggregate expression
    unit: str  # "currency" | "units" | "transactions" | "%"
    additive: bool
    description: str


METRICS: dict[str, Metric] = {m.name: m for m in (
    Metric("revenue", "Revenue", 'coalesce(sum("revenue"), 0)', "currency", True,
           "Sum of the revenue column."),
    Metric("cost", "Cost", 'coalesce(sum("cost"), 0)', "currency", True,
           "Sum of the cost column."),
    Metric("units", "Units sold", 'coalesce(sum("units"), 0)', "units", True,
           "Sum of the units column."),
    Metric("profit", "Profit", 'coalesce(sum("revenue") - sum("cost"), 0)', "currency", True,
           "Revenue minus cost."),
    Metric("transactions", "Transactions", "count(*)", "transactions", True,
           "Number of sales rows (each row is one transaction line)."),
    Metric("margin", "Profit margin",
           '100.0 * (sum("revenue") - sum("cost")) / nullif(sum("revenue"), 0)', "%", False,
           "Profit as a percentage of revenue."),
    Metric("avg_unit_price", "Average unit price",
           'sum("revenue") / nullif(sum("units"), 0)', "currency", False,
           "Revenue divided by units sold."),
)}


def format_value(value: float | int | None, unit: str, *, signed: bool = False) -> str:
    """Human formatting for headlines. Display only: measurements keep full precision."""
    if value is None:
        return "n/a"
    sign = "+" if signed and value > 0 else ""
    if unit == "%":
        return f"{sign}{value:.1f}%"
    if unit in ("units", "transactions", "count", "rows"):
        return f"{sign}{value:,.0f}"
    return f"{sign}{value:,.0f}" if abs(value) >= 1000 else f"{sign}{value:,.2f}"
