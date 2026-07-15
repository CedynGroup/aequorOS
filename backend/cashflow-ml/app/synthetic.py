"""Deterministic synthetic daily cash-flow series for Sample Bank Ltd (GHS millions).

The series is regenerated on demand from a fixed seed; nothing is persisted.
Composition per day:

- Business days draw base inflow ~ N(9.5, 0.8) and base outflow ~ N(9.0, 0.9);
  weekends carry 12% of business volume.
- Payroll: a GHS 11M salary run lands on each of the last two business days of
  the month (the run is spread across those two days), plus a GHS 5M corporate
  collections inflow bump on the 25th.
- Mid-month (14th-16th): GHS 3M of government-securities coupon inflows spread
  evenly across the three days.
- Seasonality: +/-6% annual sine on base volumes, peaking mid-December
  (harvest/holiday deposits).
- Trend: +8%/year linear growth on base volumes.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass

import numpy as np

from app.features import is_mid_month, is_month_end_window, is_payday

DEMO_AS_OF_DATE = datetime.date(2026, 3, 31)
DEFAULT_DAYS = 730
DEFAULT_SEED = 42

BASE_INFLOW_MEAN = 9.5
BASE_INFLOW_STD = 0.8
BASE_OUTFLOW_MEAN = 9.0
BASE_OUTFLOW_STD = 0.9
WEEKEND_VOLUME_RATIO = 0.12
PAYROLL_OUTFLOW_PER_DAY = 11.0
PAYDAY_INFLOW_BUMP = 5.0
COUPON_INFLOW_TOTAL = 3.0
MID_MONTH_DAYS = 3
SEASONAL_AMPLITUDE = 0.06
SEASONAL_PEAK_DAY_OF_YEAR = 349  # mid-December
ANNUAL_GROWTH = 0.08
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True, slots=True)
class DailyFlow:
    """One day of aggregate flows, in GHS millions."""

    date: datetime.date
    inflow: float
    outflow: float
    net: float


def _seasonal_factor(day: datetime.date) -> float:
    phase = 2.0 * math.pi * (day.timetuple().tm_yday - SEASONAL_PEAK_DAY_OF_YEAR) / DAYS_PER_YEAR
    return 1.0 + SEASONAL_AMPLITUDE * math.cos(phase)


def generate_daily_series(
    end_date: datetime.date | None = None,
    days: int = DEFAULT_DAYS,
    seed: int = DEFAULT_SEED,
) -> list[DailyFlow]:
    """Generate ``days`` of daily flows ending on ``end_date`` (deterministic per seed)."""
    end = end_date or DEMO_AS_OF_DATE
    start = end - datetime.timedelta(days=days - 1)
    rng = np.random.Generator(np.random.PCG64(seed))
    base_inflows = rng.normal(BASE_INFLOW_MEAN, BASE_INFLOW_STD, size=days)
    base_outflows = rng.normal(BASE_OUTFLOW_MEAN, BASE_OUTFLOW_STD, size=days)

    flows: list[DailyFlow] = []
    for i in range(days):
        day = start + datetime.timedelta(days=i)
        scale = _seasonal_factor(day) * (1.0 + ANNUAL_GROWTH * (i / DAYS_PER_YEAR))
        inflow = float(base_inflows[i]) * scale
        outflow = float(base_outflows[i]) * scale
        if day.weekday() >= 5:
            inflow *= WEEKEND_VOLUME_RATIO
            outflow *= WEEKEND_VOLUME_RATIO
        if is_month_end_window(day):
            outflow += PAYROLL_OUTFLOW_PER_DAY
        if is_payday(day):
            inflow += PAYDAY_INFLOW_BUMP
        if is_mid_month(day):
            inflow += COUPON_INFLOW_TOTAL / MID_MONTH_DAYS
        inflow = round(max(inflow, 0.0), 6)
        outflow = round(max(outflow, 0.0), 6)
        flows.append(
            DailyFlow(
                date=day,
                inflow=inflow,
                outflow=outflow,
                net=round(inflow - outflow, 6),
            )
        )
    return flows
