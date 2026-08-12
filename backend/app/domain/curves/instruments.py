"""Rate helpers and the instrument-set catalog (spec §2.1, FC-1).

The Eikon forward-curve template is fundamentally a swap/OIS bootstrap, and the
existing sovereign bootstrap (bills + bonds) has no helper for the money-market,
FRA, par-swap or OIS-swap instruments the template is built from. This module
adds that layer: each instrument is a *self-repricing helper* (the QuantLib/ORE
pattern) that can

- resolve its **pillar date** against a calendar and as-of date,
- state the **par quote implied** by a discount curve (+ projection curve), and
- return a **reprice residual** for a given quote — the objective the multi-curve
  solver drives to zero.

Curves are consumed structurally through :class:`DiscountCurve` (anything with a
``valuation_date`` and ``discount(date)``), so a helper never depends on how the
curve is stored. Multi-curve is explicit: an instrument prices its floating leg
off the **projection** curve and discounts every cash flow on the **discount**
curve. For a single self-discounting curve the caller passes the same object for
both — par swaps then reduce to the classic one-curve bootstrap.

Conventions follow the template's day-count codes (`conventions.EIKON_DAYCOUNT_CODES`):
money-market deposits and OIS on Act/360, FRAs on Act/360, par IRS fixed legs on
30/360 (US bond) vs a floating IBOR-tenor leg on Act/360.

**Forward start (FC-G3).** The deposit, swap and OIS helpers take a first-class
optional ``forward_start`` date. When ``None`` the instrument starts at the spot
date (``spot_date(as_of, spot_lag)``) — the classic case. When set, the
instrument accrues over ``[forward_start, forward_start + tenor]`` where
``forward_start`` is a future (typically IMM or otherwise calendar-adjusted)
date; the reprice residual then discounts *both* the forward start and the
maturity off the curve. This closes the real ~97 bps error that arises when a
forward-starting pillar (e.g. the Eikon v3 pillar tab, whose short pillars roll
from the March-2024 IMM, not from the as-of spot) is mis-modelled as a spot
deposit. :class:`FuturesHelper` (FC-6a) is inherently forward-starting: it prices
a money-market futures contract over two IMM dates, converting the quoted futures
rate to the equivalent forward via a Ho-Lee / Hull-White convexity adjustment
before it enters the bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from app.domain.curves.calendars import (
    BusinessDayConvention,
    Calendar,
    add_calendar_months,
    tenor_to_months,
)
from app.domain.curves.conventions import DayCount, year_fraction

__all__ = [
    "DEFAULT_FUTURES_VOL",
    "DepositHelper",
    "DiscountCurve",
    "FraHelper",
    "FuturesHelper",
    "InstrumentSet",
    "InstrumentSetError",
    "OisHelper",
    "RateHelper",
    "SwapHelper",
    "convexity_adjustment",
    "usd_depo_irs_vs_6m",
    "usd_sofr_ois",
    "usd_swap_vs_3m",
]

_MF = BusinessDayConvention.MODIFIED_FOLLOWING

# Governed default short-rate volatility for the futures->forward convexity
# adjustment (FC-6a). 0.01 == 100 bps annualised absolute (normal / Ho-Lee) vol,
# a deliberately conservative rates level (short-rate normal vols have sat
# broadly in the 0.7%-1.5% range across recent USD regimes); it is a methodology
# parameter and every helper accepts an override. See :func:`convexity_adjustment`.
DEFAULT_FUTURES_VOL = 0.01


class InstrumentSetError(ValueError):
    """An instrument set or helper definition violates its contract."""


@runtime_checkable
class DiscountCurve(Protocol):
    """Structural curve interface: a valuation date and a discount factor."""

    @property
    def valuation_date(self) -> date: ...

    def discount(self, day: date) -> float: ...


class RateHelper(Protocol):
    """A self-repricing curve-building instrument (spec §2.1)."""

    def pillar_date(self, calendar: Calendar, as_of: date) -> date: ...

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float: ...

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float: ...


def _leg_schedule(
    calendar: Calendar, spot: date, total_months: int, frequency_months: int
) -> tuple[date, ...]:
    """Payment boundaries ``[spot, ...]`` rolled forward from spot to maturity."""
    if total_months % frequency_months != 0:
        raise InstrumentSetError(
            f"Tenor of {total_months}M is not a whole multiple of the {frequency_months}M leg."
        )
    periods = total_months // frequency_months
    boundaries = [spot]
    for k in range(1, periods + 1):
        raw = add_calendar_months(spot, k * frequency_months)
        boundaries.append(calendar.adjust(raw, _MF))
    return tuple(boundaries)


# ---------------------------------------------------------------------------
# Deposit (money-market)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepositHelper:
    """A money-market deposit over ``[spot, spot+tenor]`` (spec §2.1 short end).

    Its discount factor is ``DF = 1 / (1 + rate * tau)`` on the deposit day count
    (Act/360). ``on_projection`` pins the projection (index) curve instead of the
    discount curve — an IBOR-tenor deposit anchoring the short end of a
    projection curve. ``forward_start`` (FC-G3) makes it a **forward-start**
    deposit accruing over ``[forward_start, forward_start + tenor]``; when
    ``None`` the deposit starts at the spot date.
    """

    tenor: str
    calendar: Calendar
    day_count: DayCount = DayCount.ACT_360
    spot_lag: int = 2
    convention: BusinessDayConvention = _MF
    on_projection: bool = False
    forward_start: date | None = None

    def _start(self, calendar: Calendar, as_of: date) -> date:
        if self.forward_start is not None:
            return self.forward_start
        return calendar.spot_date(as_of, self.spot_lag)

    def _dates(self, as_of: date) -> tuple[date, date]:
        start = self._start(self.calendar, as_of)
        end = self.calendar.add_tenor(start, self.tenor, self.convention)
        return start, end

    def pillar_date(self, calendar: Calendar, as_of: date) -> date:
        start = self._start(calendar, as_of)
        return calendar.add_tenor(start, self.tenor, self.convention)

    def _curve(self, discount: DiscountCurve, projection: DiscountCurve) -> DiscountCurve:
        return projection if self.on_projection else discount

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float:
        curve = self._curve(discount, projection)
        start, end = self._dates(curve.valuation_date)
        tau = year_fraction(start, end, self.day_count)
        return (curve.discount(start) / curve.discount(end) - 1.0) / tau

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float:
        curve = self._curve(discount, projection)
        start, end = self._dates(curve.valuation_date)
        tau = year_fraction(start, end, self.day_count)
        # Model DF at maturity minus the DF the quote implies (both to spot scale).
        return curve.discount(end) - curve.discount(start) / (1.0 + quote * tau)


# ---------------------------------------------------------------------------
# FRA / forward-rate agreement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FraHelper:
    """A forward-rate agreement fixing the index forward over ``[start, end]``.

    Prices on the projection curve: ``f = (P(start)/P(end) - 1) / tau``. Start and
    end are ``start_tenor`` / ``end_tenor`` measured from the spot date, or from an
    explicit ``forward_start`` anchor (FC-G3) when the FRA is dated off a fixed
    forward/IMM base rather than today's spot (e.g. 3x6 is ``3M``x``6M``).
    """

    start_tenor: str
    end_tenor: str
    calendar: Calendar
    day_count: DayCount = DayCount.ACT_360
    spot_lag: int = 2
    convention: BusinessDayConvention = _MF
    forward_start: date | None = None

    def _anchor(self, calendar: Calendar, as_of: date) -> date:
        if self.forward_start is not None:
            return self.forward_start
        return calendar.spot_date(as_of, self.spot_lag)

    def _dates(self, as_of: date) -> tuple[date, date]:
        anchor = self._anchor(self.calendar, as_of)
        start = self.calendar.add_tenor(anchor, self.start_tenor, self.convention)
        end = self.calendar.add_tenor(anchor, self.end_tenor, self.convention)
        if end <= start:
            raise InstrumentSetError("FRA end tenor must fall after the start tenor.")
        return start, end

    def pillar_date(self, calendar: Calendar, as_of: date) -> date:
        anchor = self._anchor(calendar, as_of)
        return calendar.add_tenor(anchor, self.end_tenor, self.convention)

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float:
        _ = discount
        start, end = self._dates(projection.valuation_date)
        tau = year_fraction(start, end, self.day_count)
        return (projection.discount(start) / projection.discount(end) - 1.0) / tau

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float:
        return self.implied_quote(discount, projection) - quote


# ---------------------------------------------------------------------------
# Par interest-rate swap (fixed vs projected IBOR)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwapHelper:
    """A par interest-rate swap: fixed leg vs a projected IBOR-tenor floating leg.

    The par rate solves ``PV_fixed = PV_float`` with every cash flow discounted on
    the discount curve (OIS discounting when the caller passes an OIS curve as
    ``discount``); the floating leg projects the IBOR forward off the projection
    curve. ``reprice_residual`` is the swap PV at the quoted rate — zero at par.
    ``forward_start`` (FC-G3) makes it a **forward-start** swap whose schedules
    roll from that future effective date instead of the spot date.
    """

    tenor: str
    calendar: Calendar
    fixed_frequency_months: int = 12
    float_frequency_months: int = 6
    fixed_day_count: DayCount = DayCount.THIRTY_360
    float_day_count: DayCount = DayCount.ACT_360
    spot_lag: int = 2
    forward_start: date | None = None

    def _spot(self, as_of: date) -> date:
        if self.forward_start is not None:
            return self.forward_start
        return self.calendar.spot_date(as_of, self.spot_lag)

    def _schedules(self, as_of: date) -> tuple[date, tuple[date, ...], tuple[date, ...]]:
        spot = self._spot(as_of)
        total = tenor_to_months(self.tenor)
        fixed = _leg_schedule(self.calendar, spot, total, self.fixed_frequency_months)
        floating = _leg_schedule(self.calendar, spot, total, self.float_frequency_months)
        return spot, fixed, floating

    def pillar_date(self, calendar: Calendar, as_of: date) -> date:
        start = self._spot(as_of) if self.forward_start is not None else calendar.spot_date(
            as_of, self.spot_lag
        )
        return calendar.add_tenor(start, self.tenor, _MF)

    def _annuity(self, discount: DiscountCurve, fixed: tuple[date, ...]) -> float:
        total = 0.0
        for start, pay in zip(fixed, fixed[1:], strict=False):
            tau = year_fraction(start, pay, self.fixed_day_count)
            total += tau * discount.discount(pay)
        return total

    def _float_pv(
        self, discount: DiscountCurve, projection: DiscountCurve, floating: tuple[date, ...]
    ) -> float:
        total = 0.0
        for start, pay in zip(floating, floating[1:], strict=False):
            tau = year_fraction(start, pay, self.float_day_count)
            forward = (projection.discount(start) / projection.discount(pay) - 1.0) / tau
            total += tau * forward * discount.discount(pay)
        return total

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float:
        _, fixed, floating = self._schedules(discount.valuation_date)
        annuity = self._annuity(discount, fixed)
        if annuity <= 0.0:
            raise InstrumentSetError("Swap fixed-leg annuity is non-positive.")
        return self._float_pv(discount, projection, floating) / annuity

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float:
        _, fixed, floating = self._schedules(discount.valuation_date)
        return self._float_pv(discount, projection, floating) - quote * self._annuity(
            discount, fixed
        )


# ---------------------------------------------------------------------------
# OIS swap (fixed vs compounded overnight, self-discounting)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OisHelper:
    """An OIS swap: fixed vs compounded overnight, discounted on its own curve.

    The compounded-overnight floating leg, discounted on the same (OIS) curve,
    telescopes exactly to ``DF(spot) - DF(maturity)`` — so the par rate is
    ``(DF(spot) - DF(maturity)) / annuity``. Tenors up to the fixed frequency are
    a single period; longer tenors pay the fixed leg on ``fixed_frequency_months``.
    ``forward_start`` (FC-G3) makes it a **forward-start** OIS whose effective date
    is that future date rather than the spot date.
    """

    tenor: str
    calendar: Calendar
    fixed_frequency_months: int = 12
    fixed_day_count: DayCount = DayCount.ACT_360
    spot_lag: int = 2
    forward_start: date | None = None

    def _effective(self, calendar: Calendar, as_of: date) -> date:
        if self.forward_start is not None:
            return self.forward_start
        return calendar.spot_date(as_of, self.spot_lag)

    def _dates(self, as_of: date) -> tuple[date, tuple[date, ...]]:
        spot = self._effective(self.calendar, as_of)
        total = tenor_to_months(self.tenor)
        if total <= self.fixed_frequency_months:
            maturity = self.calendar.add_tenor(spot, self.tenor, _MF)
            return spot, (spot, maturity)
        return spot, _leg_schedule(self.calendar, spot, total, self.fixed_frequency_months)

    def pillar_date(self, calendar: Calendar, as_of: date) -> date:
        start = self._effective(calendar, as_of)
        return calendar.add_tenor(start, self.tenor, _MF)

    def _annuity(self, discount: DiscountCurve, fixed: tuple[date, ...]) -> float:
        total = 0.0
        for start, pay in zip(fixed, fixed[1:], strict=False):
            tau = year_fraction(start, pay, self.fixed_day_count)
            total += tau * discount.discount(pay)
        return total

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float:
        _ = projection
        spot, fixed = self._dates(discount.valuation_date)
        annuity = self._annuity(discount, fixed)
        if annuity <= 0.0:
            raise InstrumentSetError("OIS fixed-leg annuity is non-positive.")
        float_pv = discount.discount(spot) - discount.discount(fixed[-1])
        return float_pv / annuity

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float:
        _ = projection
        spot, fixed = self._dates(discount.valuation_date)
        float_pv = discount.discount(spot) - discount.discount(fixed[-1])
        return float_pv - quote * self._annuity(discount, fixed)


# ---------------------------------------------------------------------------
# Money-market futures (convexity-adjusted) — FC-6a
# ---------------------------------------------------------------------------


def convexity_adjustment(t1: float, t2: float, sigma: float) -> float:
    """Futures -> forward convexity adjustment under a Ho-Lee / Hull-White short rate.

    A money-market futures contract is margined daily, so its quoted rate exceeds
    the equivalent forward rate: ``forward = futures - 0.5 * sigma^2 * t1 * t2``,
    with ``t1`` / ``t2`` the year fractions from the valuation date to the futures
    period start / end and ``sigma`` the absolute (normal) short-rate volatility.
    This is the standard one-factor (Ho-Lee, a Hull-White special case) result —
    see Hull, *Options, Futures, and Other Derivatives*, the Eurodollar-futures
    convexity section. The value returned is the amount **subtracted** from the
    futures rate; it is non-negative and grows with maturity.
    """
    if t1 < 0.0 or t2 < t1:
        raise InstrumentSetError("Futures convexity needs 0 <= t1 <= t2.")
    if sigma < 0.0:
        raise InstrumentSetError("Convexity volatility must be non-negative.")
    return 0.5 * sigma * sigma * t1 * t2


@dataclass(frozen=True)
class FuturesHelper:
    """A money-market futures over two IMM dates, convexity-adjusted (FC-6a).

    A futures contract fixes an index over ``[start, end]`` (typically consecutive
    IMM dates). The **quoted futures rate overstates the equivalent forward rate**;
    the helper converts it via :func:`convexity_adjustment` and then prices exactly
    like a forward-rate agreement on the projection curve — so a futures strip and
    an FRA strip bootstrap through the same seam, differing only by the (governed,
    ``sigma``-parameterised) adjustment. ``time_day_count`` measures the convexity
    times ``t1`` / ``t2`` from the valuation date; ``day_count`` is the accrual
    basis of the underlying deposit (Act/360).
    """

    start: date
    end: date
    calendar: Calendar
    day_count: DayCount = DayCount.ACT_360
    sigma: float = DEFAULT_FUTURES_VOL
    time_day_count: DayCount = DayCount.ACT_365F

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise InstrumentSetError("Futures end date must fall after the start date.")

    def pillar_date(self, calendar: Calendar, as_of: date) -> date:
        _ = (calendar, as_of)
        return self.end

    def _adjustment(self, as_of: date) -> float:
        t1 = year_fraction(as_of, self.start, self.time_day_count)
        t2 = year_fraction(as_of, self.end, self.time_day_count)
        return convexity_adjustment(t1, t2, self.sigma)

    def forward_from_futures(self, futures_rate: float, as_of: date) -> float:
        """The equivalent forward rate for a quoted futures rate (public helper)."""
        return futures_rate - self._adjustment(as_of)

    def _curve_forward(self, projection: DiscountCurve) -> float:
        tau = year_fraction(self.start, self.end, self.day_count)
        return (projection.discount(self.start) / projection.discount(self.end) - 1.0) / tau

    def implied_quote(self, discount: DiscountCurve, projection: DiscountCurve) -> float:
        """The futures rate implied by the curve = curve forward + convexity."""
        _ = discount
        return self._curve_forward(projection) + self._adjustment(projection.valuation_date)

    def reprice_residual(
        self, quote: float, discount: DiscountCurve, projection: DiscountCurve
    ) -> float:
        _ = discount
        forward_target = quote - self._adjustment(projection.valuation_date)
        return self._curve_forward(projection) - forward_target


# ---------------------------------------------------------------------------
# Instrument-set catalog (the Assumptions RIC-chain analogue)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentSet:
    """A governed curve-building catalog entry (spec §2.1, the Eikon "Swap style").

    Holds the family name, the Refinitiv RIC chain (from the fixture Assumptions
    tab) and the ordered helpers (the tenor grid). ``on_projection`` marks a set
    that builds a projection curve (its instruments pin the index curve and
    discount on a separately supplied OIS curve).
    """

    name: str
    ric_chain: str
    helpers: tuple[RateHelper, ...]
    on_projection: bool = False

    def pillar_dates(self, as_of: date, calendar: Calendar) -> tuple[date, ...]:
        return tuple(helper.pillar_date(calendar, as_of) for helper in self.helpers)


def usd_depo_irs_vs_6m(calendar: Calendar) -> InstrumentSet:
    """USD - Depo, IRS vs 6M (RIC ``0#USDZ=R``): deposits + semi-annual 6M-float IRS."""
    depos = tuple(
        DepositHelper(tenor, calendar) for tenor in ("1W", "1M", "2M", "3M", "6M")
    )
    swaps = tuple(
        SwapHelper(tenor, calendar, fixed_frequency_months=12, float_frequency_months=6)
        for tenor in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
    )
    return InstrumentSet("USD - Depo, IRS vs 6M", "0#USDZ=R", (*depos, *swaps))


def usd_swap_vs_3m(calendar: Calendar) -> InstrumentSet:
    """USD - Swap vs 3M (RIC ``0#USDSBQLZ=R``): quarterly 3M-float par swaps (projection)."""
    swaps = tuple(
        SwapHelper(tenor, calendar, fixed_frequency_months=12, float_frequency_months=3)
        for tenor in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
    )
    return InstrumentSet("USD - Swap vs 3M", "0#USDSBQLZ=R", swaps, on_projection=True)


def usd_sofr_ois(calendar: Calendar) -> InstrumentSet:
    """USD - Swap SOFR OIS (RIC ``0#USDSROISZ=R``): deposits + OIS swaps (discount)."""
    depos = tuple(DepositHelper(tenor, calendar) for tenor in ("1W", "1M", "3M", "6M"))
    ois = tuple(
        OisHelper(tenor, calendar)
        for tenor in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
    )
    return InstrumentSet("USD - Swap SOFR OIS", "0#USDSROISZ=R", (*depos, *ois))
