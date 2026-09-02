"""Wire contract for the governance report-comparison analytics feature.

Two immutable regulatory runs of the SAME engine module are compared line item
by line item. ``version`` mode holds the reporting period fixed and compares two
run versions (an original filing vs a resubmission); ``period`` mode holds the
module fixed and compares the latest run of two reporting periods (e.g. Jun vs
Mar). Each comparable line carries an absolute delta, a percentage delta, a
direction, and — the substantive judgment layer — a favorability derived from a
governed favorable-direction registry (see ``app/services/report_comparison.py``).
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ComparisonMode = Literal["version", "period"]
#: The engine module a run belongs to (``RegulatoryRun.module``). This is the
#: "return family" the two sides must share to be comparable.
ComparisonModule = Literal["liquidity", "capital", "credit", "irr", "fx", "ftp", "forecast"]
#: How the UI should format a line's figures — and whether its delta is money.
LineUnit = Literal["ccy", "pct", "ratio", "count"]
LineDirection = Literal["up", "down", "flat"]
LineFavorability = Literal["favorable", "adverse", "neutral"]


class ReportComparisonRequest(ClosedModel):
    """Query parameters for ``GET /banks/{bank_id}/reports/comparison``.

    ``left`` and ``right`` are UUIDs whose meaning depends on ``mode``:

    * ``version`` — each is a ``RegulatoryRun`` id; both must be succeeded runs of
      the same module for the same reporting period.
    * ``period`` — each is a ``bank_reporting_periods`` id; the latest succeeded
      run of ``module``/``scenario_code`` in each period is compared.
    """

    mode: ComparisonMode = Field(
        description="'version' (same period, two run versions) or 'period' (two periods)."
    )
    module: ComparisonModule = Field(
        description="The engine module / return family to compare."
    )
    left: UUID = Field(
        description="Baseline side: a run id (version mode) or period id (period mode)."
    )
    right: UUID = Field(
        description="Comparison side: a run id (version mode) or period id (period mode)."
    )
    scenario_code: str = Field(
        default="baseline",
        min_length=1,
        max_length=40,
        description="Scenario whose run is compared in period mode (default 'baseline').",
    )


class ComparisonLineRead(ClosedModel):
    """One comparable numeric figure aligned across the two sides by ``key``."""

    key: str
    label: str
    unit: LineUnit
    #: Decimal strings preserve engine precision; ``None`` when a key exists on
    #: only one side.
    left_value: str | None
    right_value: str | None
    #: ``right - left`` as an absolute decimal string (the bank's currency for
    #: ``ccy`` lines, percentage points for ``pct`` lines). ``None`` when a side
    #: is missing.
    delta_ccy: str | None
    #: ``(right - left) / left * 100`` in percentage points. ``None`` when the
    #: base is zero or absent (see ``new``).
    delta_pct: str | None
    direction: LineDirection
    favorability: LineFavorability
    #: True when the base value is zero or absent, so ``delta_pct`` is undefined
    #: and the figure appears (or resumes from zero) on the comparison side.
    new: bool


class ComparisonGroupRead(ClosedModel):
    """Lines grouped by return section for the UI (e.g. 'Risk-weighted assets')."""

    title: str
    lines: list[ComparisonLineRead]


class ComparisonSideRead(ClosedModel):
    """Identity of one immutable run in the comparison."""

    run_id: UUID
    version: int
    label: str
    period_label: str
    reporting_date: date
    reporting_period_id: UUID
    scenario_code: str
    engine_version: str


class ReportComparisonRead(ClosedModel):
    mode: ComparisonMode
    module: ComparisonModule
    left: ComparisonSideRead
    right: ComparisonSideRead
    groups: list[ComparisonGroupRead]
    favorable_count: int
    adverse_count: int
    neutral_count: int
