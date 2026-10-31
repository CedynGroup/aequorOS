"""MPR-anchored meeting-date step curve (spec §5 step 7, §6.2 short end).

Ghana has overnight *reference* rates but no OIS instruments, so the short end
of the synthetic discounting curve follows the Clarke (2010) meeting-date
construction: the overnight forward is piecewise flat between scheduled MPC
dates and jumps **only** on those dates. The level in each window is
``MPR + calibrated overnight spread + cumulative expected policy moves`` —
BoG's +/-100 bps corridor tethers the interbank overnight rate to the MPR, and
the spread (e.g. -170 bps in late-2025 excess-liquidity conditions) is a
calibrated, dynamic input, never a constant assumption.

Compounding convention (documented choice): **daily compounding on ACT/365F**
— ``DF(horizon) = prod over calendar days (1 + r_day / 365)^-1`` — the plain
OIS-style compounded-overnight formula. Ghana's unsecured overnight market
quotes ACT/365-style simple interest per day; using the fixed 365 basis keeps
the step curve consistent with the ACT_365F day count available to the rest of
the library. A rate change takes effect on the meeting date itself, i.e. the
overnight accrual for the night beginning on the meeting date already carries
the new level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

__all__ = ["MeetingDateStepCurve", "StepCurveError"]

_DAYS_PER_YEAR = 365.0  # ACT/365F daily compounding basis
_BPS = 10_000.0


class StepCurveError(ValueError):
    """The meeting schedule or query violates the step-curve contract."""


@dataclass(frozen=True)
class MeetingDateStepCurve:
    """Piecewise-flat overnight forward curve anchored on scheduled MPC dates.

    ``mpr`` is the policy rate as a decimal; ``spread_bps`` is the calibrated
    overnight-to-MPR spread; ``expected_moves_bps`` (optional, aligned with
    ``meeting_dates``) carries the desk's expected policy move at each meeting
    — cumulative, so a -100 at meeting two shifts every window from that
    meeting onward. Only dates strictly after ``anchor_date`` create steps.
    """

    anchor_date: date
    mpr: float
    meeting_dates: tuple[date, ...]
    spread_bps: float = 0.0
    expected_moves_bps: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        dates = tuple(self.meeting_dates)
        for left, right in zip(dates, dates[1:], strict=False):
            if right <= left:
                raise StepCurveError("Meeting dates must be strictly increasing.")
        if self.expected_moves_bps is not None and len(self.expected_moves_bps) != len(dates):
            raise StepCurveError(
                "expected_moves_bps must align one-to-one with meeting_dates."
            )
        object.__setattr__(self, "meeting_dates", dates)

    def overnight_rate(self, day: date) -> float:
        """The overnight (one-night) rate accruing on the night that begins on ``day``."""
        if day < self.anchor_date:
            raise StepCurveError(f"{day} predates the curve anchor {self.anchor_date}.")
        level = self.mpr + self.spread_bps / _BPS
        if self.expected_moves_bps is not None:
            for meeting, move in zip(self.meeting_dates, self.expected_moves_bps, strict=True):
                if self.anchor_date < meeting <= day:
                    level += move / _BPS
        return level

    def df(self, to: date) -> float:
        """Discount factor from the anchor to ``to`` by daily ACT/365F compounding.

        Computed segment-wise in closed form:
        ``DF = prod over windows (1 + r_window / 365)^-n_days`` — identical to
        the day-by-day product because the rate is constant inside a window.
        """
        if to < self.anchor_date:
            raise StepCurveError(f"{to} predates the curve anchor {self.anchor_date}.")
        if to == self.anchor_date:
            return 1.0
        log_growth = 0.0
        segment_start = self.anchor_date
        boundaries = [d for d in self.meeting_dates if self.anchor_date < d < to]
        for boundary in [*boundaries, to]:
            nights = (boundary - segment_start).days
            rate = self.overnight_rate(segment_start)
            log_growth += nights * math.log1p(rate / _DAYS_PER_YEAR)
            segment_start = boundary
        return math.exp(-log_growth)

    def df_between(self, start: date, end: date) -> float:
        """Discount factor between two dates on the curve: DF(end) / DF(start)."""
        if end < start:
            raise StepCurveError("df_between requires start <= end.")
        return self.df(end) / self.df(start)

    def compounded_rate(self, start: date, end: date) -> float:
        """Annualized simple rate of the compounded overnight leg over [start, end).

        ``((prod (1 + r/365)) - 1) * 365 / n`` — the standard OIS floating-leg
        payoff convention on ACT/365F.
        """
        nights = (end - start).days
        if nights <= 0:
            raise StepCurveError("compounded_rate requires start < end.")
        growth = 1.0 / self.df_between(start, end)
        return (growth - 1.0) * _DAYS_PER_YEAR / nights
