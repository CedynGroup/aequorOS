from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

type WindowRatio = Literal["lcr_pct", "nsfr_pct", "car_pct", "cet1_ratio_pct"]
type WindowRatioModule = Literal["liquidity", "capital"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowRatioPointRead(ClosedModel):
    """One reporting period's resolved ratio value inside the window."""

    period_end: date
    value: Decimal
    stored: bool


class WindowRatioStatRead(ClosedModel):
    """A ratio's per-period series plus its window statistics.

    Only ratios that resolved at least one point appear in the payload, so
    every statistic is always computable (no nullable stats).
    """

    ratio: WindowRatio
    module: WindowRatioModule
    points: list[WindowRatioPointRead]
    start_value: Decimal
    end_value: Decimal
    change: Decimal
    avg: Decimal
    min: Decimal
    max: Decimal


class WindowDailyStatRead(ClosedModel):
    """Daily-snapshot aggregate for one module's primary metric."""

    module: str
    metric_key: str
    day_count: int
    min: Decimal
    avg: Decimal
    max: Decimal


class WindowAnalyticsRead(ClosedModel):
    bank_id: str
    start_date: date
    end_date: date
    period_count: int
    ratios: list[WindowRatioStatRead]
    daily: list[WindowDailyStatRead]
