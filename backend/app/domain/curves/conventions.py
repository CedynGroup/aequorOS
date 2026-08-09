"""Day-count and quote conventions (spec §5 step 3 — yield/price normalization).

Everything the curve layer needs to turn a raw Ghana quote into a common
representation before fitting:

- **Day counts.** ``ACT_364`` is the GFIM standard (actual days over a 364-day
  year — Ghana's T-bill market quotes on it), plus ``ACT_365F`` and ``ACT_360``.
- **T-bill discount-rate <-> true yield.** BoG publishes both a "discount rate"
  and an "interest rate" for every auction; they are linked by
  ``interest = discount / (1 - discount * t)`` with ``t = days / 364``.
- **Bond price <-> yield-to-maturity.** Street convention: periodic compounding
  at the coupon frequency, cash-flow times measured on the instrument day
  count, accrued interest proportioned over the current quasi-coupon period.
- **Compounding conversions.** Simple, annual and continuous zero rates, and
  the discount-factor <-> zero-rate maps for each.

All rates are decimals (0.0568 == 5.68%), never percent, and all prices are
per 100 face.
"""

from __future__ import annotations

import calendar
import enum
import math
from dataclasses import dataclass
from datetime import date

__all__ = [
    "Compounding",
    "ConventionError",
    "DayCount",
    "CashFlow",
    "accrued_interest",
    "bond_cashflows",
    "bond_price",
    "bond_ytm",
    "convert_zero_rate",
    "discount_factor_to_zero",
    "discount_to_yield",
    "year_fraction",
    "yield_to_discount",
    "zero_to_discount_factor",
]


class ConventionError(ValueError):
    """A quote or schedule violates its own convention (bad dates, bad rates)."""


class DayCount(enum.Enum):
    """Supported day-count conventions; the value is the year basis in days."""

    ACT_364 = "ACT/364"  # GFIM standard: actual days over a 364-day year
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"

    @property
    def basis(self) -> float:
        return _BASIS[self]


_BASIS: dict[DayCount, float] = {
    DayCount.ACT_364: 364.0,
    DayCount.ACT_365F: 365.0,
    DayCount.ACT_360: 360.0,
}


class Compounding(enum.Enum):
    SIMPLE = "simple"
    ANNUAL = "annual"
    CONTINUOUS = "continuous"


def year_fraction(start: date, end: date, day_count: DayCount) -> float:
    """Actual-days year fraction between two dates on the given basis."""
    if end < start:
        raise ConventionError(f"year_fraction requires start <= end, got {start} > {end}.")
    return (end - start).days / day_count.basis


# ---------------------------------------------------------------------------
# T-bill discount rate <-> true yield (BoG publishes both for every auction)
# ---------------------------------------------------------------------------


def discount_to_yield(
    discount_rate: float, days: int, day_count: DayCount = DayCount.ACT_364
) -> float:
    """True (interest) yield implied by a T-bill discount rate.

    ``interest = discount / (1 - discount * t)`` with ``t = days / basis``.
    On ACT/364 this reproduces BoG's published discount/interest pairs exactly
    (e.g. 03 Aug 2026: 91-day 5.6800 <-> 5.7618; the golden tests pin three
    published pairs to 4 dp).
    """
    if days <= 0:
        raise ConventionError("A T-bill tenor must be a positive number of days.")
    t = days / day_count.basis
    denominator = 1.0 - discount_rate * t
    if denominator <= 0.0:
        raise ConventionError(
            f"Discount rate {discount_rate} over {days} days implies a non-positive price."
        )
    return discount_rate / denominator


def yield_to_discount(
    yield_rate: float, days: int, day_count: DayCount = DayCount.ACT_364
) -> float:
    """Inverse of :func:`discount_to_yield`: ``discount = interest / (1 + interest * t)``."""
    if days <= 0:
        raise ConventionError("A T-bill tenor must be a positive number of days.")
    t = days / day_count.basis
    denominator = 1.0 + yield_rate * t
    if denominator <= 0.0:
        raise ConventionError(
            f"Yield {yield_rate} over {days} days implies a non-positive price."
        )
    return yield_rate / denominator


# ---------------------------------------------------------------------------
# Compounding conversions
# ---------------------------------------------------------------------------


def zero_to_discount_factor(zero_rate: float, t: float, compounding: Compounding) -> float:
    """Discount factor for a zero rate quoted under the given compounding."""
    if t < 0.0:
        raise ConventionError("Time to maturity must be non-negative.")
    if t == 0.0:
        return 1.0
    if compounding is Compounding.SIMPLE:
        denominator = 1.0 + zero_rate * t
        if denominator <= 0.0:
            raise ConventionError(f"Simple rate {zero_rate} at t={t} gives a non-positive price.")
        return 1.0 / denominator
    if compounding is Compounding.ANNUAL:
        base = 1.0 + zero_rate
        if base <= 0.0:
            raise ConventionError(f"Annual rate {zero_rate} is below -100%.")
        return base**-t
    return math.exp(-zero_rate * t)


def discount_factor_to_zero(df: float, t: float, compounding: Compounding) -> float:
    """Zero rate (under the given compounding) implied by a discount factor."""
    if df <= 0.0:
        raise ConventionError("A discount factor must be strictly positive.")
    if t <= 0.0:
        raise ConventionError("Time to maturity must be strictly positive to imply a zero rate.")
    if compounding is Compounding.SIMPLE:
        return (1.0 / df - 1.0) / t
    if compounding is Compounding.ANNUAL:
        return df ** (-1.0 / t) - 1.0
    return -math.log(df) / t


def convert_zero_rate(
    rate: float, t: float, from_compounding: Compounding, to_compounding: Compounding
) -> float:
    """Re-express a zero rate under another compounding via the shared discount factor."""
    if from_compounding is to_compounding:
        return rate
    return discount_factor_to_zero(
        zero_to_discount_factor(rate, t, from_compounding), t, to_compounding
    )


# ---------------------------------------------------------------------------
# Fixed-coupon bond: schedule, accrued interest, price <-> YTM
# ---------------------------------------------------------------------------

_VALID_FREQUENCIES = (1, 2)


@dataclass(frozen=True)
class CashFlow:
    """One bond cash flow: payment date, time from settlement (instrument day
    count) and amount per 100 face."""

    payment_date: date
    time: float
    amount: float


def _add_months(day: date, months: int) -> date:
    """Shift a date by whole months, clamping the day-of-month to the target month."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _coupon_schedule(settlement: date, maturity: date, frequency: int) -> tuple[list[date], date]:
    """Coupon dates strictly after settlement (ascending), plus the quasi-coupon
    date on or before settlement that anchors accrued interest.

    Dates are rolled backwards from maturity in steps of ``12 / frequency``
    months (unadjusted — the library is calendar-agnostic; holiday adjustment
    is an ingestion concern, not a curve-math concern).
    """
    if frequency not in _VALID_FREQUENCIES:
        raise ConventionError(f"Coupon frequency must be one of {_VALID_FREQUENCIES}.")
    if maturity <= settlement:
        raise ConventionError("Bond maturity must fall strictly after settlement.")
    step = 12 // frequency
    dates: list[date] = []
    current = maturity
    while current > settlement:
        dates.append(current)
        current = _add_months(maturity, -step * (len(dates)))
    dates.reverse()
    previous = _add_months(maturity, -step * len(dates))
    return dates, previous


def bond_cashflows(
    settlement: date,
    maturity: date,
    coupon_rate: float,
    frequency: int,
    day_count: DayCount,
) -> tuple[CashFlow, ...]:
    """Remaining cash flows per 100 face (coupons after settlement + redemption)."""
    coupon_dates, _ = _coupon_schedule(settlement, maturity, frequency)
    coupon_amount = coupon_rate / frequency * 100.0
    flows: list[CashFlow] = []
    for payment_date in coupon_dates:
        amount = coupon_amount
        if payment_date == maturity:
            amount += 100.0
        flows.append(
            CashFlow(
                payment_date=payment_date,
                time=year_fraction(settlement, payment_date, day_count),
                amount=amount,
            )
        )
    return tuple(flows)


def accrued_interest(
    settlement: date,
    maturity: date,
    coupon_rate: float,
    frequency: int,
    day_count: DayCount,
) -> float:
    """Accrued interest per 100 face, proportioned over the current quasi-coupon
    period on the instrument day count (numerator and denominator alike, the
    ACT/ACT-ICMA-style ratio)."""
    coupon_dates, previous = _coupon_schedule(settlement, maturity, frequency)
    next_coupon = coupon_dates[0]
    period = year_fraction(previous, next_coupon, day_count)
    if period <= 0.0:
        raise ConventionError("Degenerate quasi-coupon period.")
    fraction = year_fraction(previous, settlement, day_count) / period
    return coupon_rate / frequency * 100.0 * fraction


def bond_price(  # noqa: PLR0913 - a bond quote is irreducibly six-dimensional
    ytm: float,
    settlement: date,
    maturity: date,
    coupon_rate: float,
    frequency: int,
    day_count: DayCount,
    clean: bool = True,
) -> float:
    """Price per 100 face at a yield, street convention.

    ``P_dirty = sum CF_k * (1 + y/f)^(-f * t_k)`` with ``t_k`` on the
    instrument day count; the clean price subtracts accrued interest.
    """
    base = 1.0 + ytm / frequency
    if base <= 0.0:
        raise ConventionError(f"Yield {ytm} is below the -{frequency * 100}% compounding floor.")
    dirty = sum(
        flow.amount * base ** (-frequency * flow.time)
        for flow in bond_cashflows(settlement, maturity, coupon_rate, frequency, day_count)
    )
    if not clean:
        return dirty
    return dirty - accrued_interest(settlement, maturity, coupon_rate, frequency, day_count)


def _bond_price_and_slope(  # noqa: PLR0913
    ytm: float,
    flows: tuple[CashFlow, ...],
    frequency: int,
    accrued: float,
    clean: bool,
) -> tuple[float, float]:
    base = 1.0 + ytm / frequency
    price = 0.0
    slope = 0.0
    for flow in flows:
        price += flow.amount * base ** (-frequency * flow.time)
        # d/dy (1 + y/f)^(-f t) = -t (1 + y/f)^(-f t - 1)
        slope += flow.amount * (-flow.time) * base ** (-frequency * flow.time - 1.0)
    if clean:
        price -= accrued
    return price, slope


def bond_ytm(  # noqa: PLR0913
    price: float,
    settlement: date,
    maturity: date,
    coupon_rate: float,
    frequency: int,
    day_count: DayCount,
    clean: bool = True,
    tolerance: float = 1e-12,
    max_iterations: int = 100,
) -> float:
    """Yield-to-maturity from a price: Newton's method with a bisection fallback.

    Newton converges in a handful of iterations from the coupon-rate start for
    every realistic quote; if it strays (non-finite step, out of the bracket,
    or slow convergence) the solver falls back to bisection over
    ``y in [-0.99, 10.0]``, which is guaranteed because price is strictly
    decreasing in yield.
    """
    if price <= 0.0:
        raise ConventionError("A bond price must be strictly positive.")
    flows = bond_cashflows(settlement, maturity, coupon_rate, frequency, day_count)
    accrued = accrued_interest(settlement, maturity, coupon_rate, frequency, day_count)
    lower, upper = -0.99, 10.0

    def objective(ytm: float) -> tuple[float, float]:
        value, slope = _bond_price_and_slope(ytm, flows, frequency, accrued, clean)
        return value - price, slope

    ytm = max(coupon_rate, 1e-4)
    for _ in range(max_iterations):
        error, slope = objective(ytm)
        if abs(error) < tolerance:
            return ytm
        if slope == 0.0 or not math.isfinite(slope):
            break
        candidate = ytm - error / slope
        if not math.isfinite(candidate) or candidate <= lower or candidate >= upper:
            break
        ytm = candidate
    # Bisection fallback: price(y) is strictly decreasing, so the root is bracketed.
    low_error, _ = objective(lower)
    high_error, _ = objective(upper)
    if low_error < 0.0 or high_error > 0.0:
        raise ConventionError(
            f"Price {price} is outside the attainable range for this bond "
            f"(no yield in [{lower}, {upper}])."
        )
    for _ in range(200):
        midpoint = (lower + upper) / 2.0
        error, _ = objective(midpoint)
        if abs(error) < tolerance or (upper - lower) / 2.0 < 1e-14:
            return midpoint
        if error > 0.0:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0
