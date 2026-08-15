"""Multi-curve simultaneous solve and the forward-grid output (spec §2.3-§2.4, FC-2).

The two deliverables the Eikon template ultimately produces:

1. **The simultaneous multi-curve bootstrap.** Modern par swaps are quoted on OIS
   discounting, so discount (OIS) and projection (IBOR) curves must be solved
   *together* — a sequential bootstrap is internally inconsistent. :func:`build_curve_set`
   solves the node zero rates of both curves at once (`scipy.optimize.least_squares`)
   so every helper reprices to its quote. For a single self-discounting curve the
   projection leg is omitted and the projection curve *is* the discount curve.

2. **The tenor-adjusted forward grid.** :func:`forward_grid` resamples a solved
   discount curve onto a regular forward grid at the chosen Curve Frequency,
   emitting the template's ``Start | End | Discount Factor | Yield`` block: row 0
   is the spot stub (``DF=1``, ``Yield=0``); each later row is a forward period
   whose discount factor is ``DF(End)/DF(Start)`` and whose yield is the simple
   forward rate over ``[Start, End]`` in the selectable output basis (the
   "Convert to" contract, money-market Act/360 by default). :func:`convert_basis`
   renders that forward rate in any supported basis from the (basis-invariant)
   discount factors.

Interpolation reuses ``interpolation.py`` (continuously-compounded zeros on
log-discount-factors by default); the curve object reuses ``bootstrap.ZeroCurve``
for evaluation. The result :class:`ForwardGrid` is immutable and carries a
value-based canonical-JSON digest, exactly like ``objects.CurveBuildResult``.
"""

from __future__ import annotations

import calendar as _calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property

import numpy as np
from scipy.optimize import least_squares

from app.domain.curves.bootstrap import ZeroCurve
from app.domain.curves.calendars import (
    BusinessDayConvention,
    Calendar,
    add_calendar_months,
)
from app.domain.curves.conventions import (
    DayCount,
    simple_rate_from_discounts,
    year_fraction,
)
from app.domain.curves.instruments import DiscountCurve, RateHelper
from app.domain.curves.interpolation import Extrapolation
from app.domain.curves.objects import canonical_input_digest

__all__ = [
    "CurveSet",
    "ForwardGrid",
    "ForwardGridRow",
    "MarketQuote",
    "MultiCurveError",
    "SolvedCurve",
    "build_curve_set",
    "convert_basis",
    "forward_grid",
    "forward_grid_for_frequency",
]

_MF = BusinessDayConvention.MODIFIED_FOLLOWING
_INTERNAL_DAY_COUNT = DayCount.ACT_365F
_SOLVER_TOLERANCE = 1e-11  # the acid test asserts 1e-8


class MultiCurveError(ValueError):
    """The instrument set / grid request cannot produce a well-formed result."""


# ---------------------------------------------------------------------------
# Solved curve (a DiscountCurve backed by a ZeroCurve)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolvedCurve:
    """A dated discount curve: node zeros interpolated, evaluated by date.

    Reuses :class:`bootstrap.ZeroCurve` for interpolation and discounting; node
    zeros are continuously compounded on the internal day count (Act/365F). Node
    dates must be strictly after the valuation date and strictly increasing.
    """

    valuation_date: date
    node_dates: tuple[date, ...]
    zeros: tuple[float, ...]
    interpolation: str = "log_linear_df"
    extrapolation: Extrapolation = "flat_forward"
    day_count: DayCount = _INTERNAL_DAY_COUNT

    def __post_init__(self) -> None:
        if len(self.node_dates) != len(self.zeros):
            raise MultiCurveError("node_dates and zeros must be equally long.")
        if not self.node_dates:
            raise MultiCurveError("A curve needs at least one node.")
        for left, right in zip(self.node_dates, self.node_dates[1:], strict=False):
            if right <= left:
                raise MultiCurveError("node_dates must be strictly increasing.")
        if self.node_dates[0] <= self.valuation_date:
            raise MultiCurveError("All node dates must fall after the valuation date.")

    @cached_property
    def _zero_curve(self) -> ZeroCurve:
        times = tuple(
            year_fraction(self.valuation_date, day, self.day_count) for day in self.node_dates
        )
        return ZeroCurve(
            valuation_date=self.valuation_date,
            day_count=self.day_count,
            times=times,
            zeros=self.zeros,
            interpolation=self.interpolation,
            extrapolation=self.extrapolation,
        )

    def discount(self, day: date) -> float:
        if day == self.valuation_date:
            return 1.0
        if day < self.valuation_date:
            raise MultiCurveError(f"{day} precedes the valuation date {self.valuation_date}.")
        return self._zero_curve.df(self._zero_curve.time_of(day))

    def zero_rate(self, day: date) -> float:
        """Continuously-compounded zero rate to ``day`` on the internal day count."""
        return self._zero_curve.zero(self._zero_curve.time_of(day))


# ---------------------------------------------------------------------------
# Simultaneous solve
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketQuote:
    """A helper plus its market quote — one row of an instrument grid."""

    helper: RateHelper
    quote: float


@dataclass(frozen=True)
class CurveSet:
    """The solved discount curve and its projection curve (identical if single-curve)."""

    discount: SolvedCurve
    projection: SolvedCurve

    @property
    def is_single_curve(self) -> bool:
        return self.projection is self.discount


def _node_dates(quotes: Sequence[MarketQuote], calendar: Calendar, as_of: date) -> tuple[date, ...]:
    dates = tuple(quote.helper.pillar_date(calendar, as_of) for quote in quotes)
    ordered = tuple(sorted(dates))
    if ordered != dates:
        raise MultiCurveError("Instrument quotes must be supplied in ascending pillar-date order.")
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right <= left:
            raise MultiCurveError("Two instruments share a pillar date; collapse them first.")
    return ordered


def build_curve_set(  # noqa: PLR0913 - the governed build parameters, spelled out
    *,
    as_of: date,
    calendar: Calendar,
    discount_quotes: Sequence[MarketQuote],
    projection_quotes: Sequence[MarketQuote] | None = None,
    interpolation: str = "log_linear_df",
    extrapolation: Extrapolation = "flat_forward",
    max_iterations: int = 200,
) -> CurveSet:
    """Solve discount and (optionally) projection curves simultaneously.

    The unknowns are the node zero rates of both curves; the residuals are every
    helper's reprice residual (discount helpers on the discount curve, projection
    helpers projecting on the projection curve while discounting on the discount
    curve). ``least_squares`` drives them to zero — the modern OIS-discounted
    multi-curve bootstrap. Raises if the worst residual exceeds the solver
    tolerance (an inconsistent / arbitrageable instrument set).
    """
    if not discount_quotes:
        raise MultiCurveError("At least one discount instrument is required.")
    disc_dates = _node_dates(discount_quotes, calendar, as_of)
    proj_dates = (
        _node_dates(projection_quotes, calendar, as_of) if projection_quotes else ()
    )
    n_disc = len(disc_dates)

    disc_seed = [_seed_zero(q.quote) for q in discount_quotes]
    proj_seed = [_seed_zero(q.quote) for q in (projection_quotes or ())]
    x0 = np.array([*disc_seed, *proj_seed], dtype=float)

    def make_curves(x: np.ndarray) -> tuple[SolvedCurve, SolvedCurve]:
        disc = SolvedCurve(
            as_of, disc_dates, tuple(float(v) for v in x[:n_disc]), interpolation, extrapolation
        )
        if not projection_quotes:
            return disc, disc
        proj = SolvedCurve(
            as_of, proj_dates, tuple(float(v) for v in x[n_disc:]), interpolation, extrapolation
        )
        return disc, proj

    def residuals(x: np.ndarray) -> np.ndarray:
        disc, proj = make_curves(x)
        out = [q.helper.reprice_residual(q.quote, disc, proj) for q in discount_quotes]
        out.extend(
            q.helper.reprice_residual(q.quote, disc, proj) for q in (projection_quotes or ())
        )
        return np.array(out, dtype=float)

    result = least_squares(residuals, x0, method="lm", max_nfev=max_iterations * (len(x0) + 1))
    worst = float(np.max(np.abs(result.fun))) if result.fun.size else 0.0
    if worst > _SOLVER_TOLERANCE:
        raise MultiCurveError(
            f"Multi-curve solve did not converge: worst reprice residual {worst:.3e} "
            f"exceeds tolerance {_SOLVER_TOLERANCE:.0e}."
        )
    disc, proj = make_curves(result.x)
    return CurveSet(discount=disc, projection=proj)


def _seed_zero(quote: float) -> float:
    """A robust zero-rate seed from a quote (rates seed near themselves)."""
    return min(max(quote, -0.25), 0.75)


# ---------------------------------------------------------------------------
# Forward grid (the Start/End/DF/Yield deliverable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForwardGridRow:
    """One forward-grid period: adjusted boundaries, period DF and output-basis yield."""

    start: date
    end: date
    discount_factor: float
    yield_: float


@dataclass(frozen=True)
class ForwardGrid:
    """The immutable tenor-adjusted forward grid (Eikon M:P block) with a digest."""

    as_of: date
    curve_frequency_months: int
    output_basis: DayCount
    rows: tuple[ForwardGridRow, ...]
    input_digest: str = field(default="")

    @classmethod
    def create(
        cls,
        as_of: date,
        curve_frequency_months: int,
        output_basis: DayCount,
        rows: Sequence[ForwardGridRow],
    ) -> ForwardGrid:
        payload: dict[str, object] = {
            "as_of": as_of.isoformat(),
            "curve_frequency_months": curve_frequency_months,
            "output_basis": output_basis.value,
            "rows": [
                {
                    "start": row.start.isoformat(),
                    "end": row.end.isoformat(),
                    "discount_factor": row.discount_factor,
                    "yield": row.yield_,
                }
                for row in rows
            ],
        }
        return cls(
            as_of=as_of,
            curve_frequency_months=curve_frequency_months,
            output_basis=output_basis,
            rows=tuple(rows),
            input_digest=canonical_input_digest(payload),
        )


def _period_anchor(as_of: date, interval_months: int) -> date:
    """The frequency-aligned month-end on or after ``as_of``.

    For a frequency that divides 12 (monthly, quarterly, semi-annual) the anchor
    is the end of the next period-aligned month (Mar/Jun/Sep/Dec for quarterly);
    otherwise it is the end of the as-of month stepping by the interval.
    """
    if interval_months <= 0:
        raise MultiCurveError("Curve frequency must be a positive number of months.")
    month = as_of.month
    if 12 % interval_months == 0:
        while month % interval_months != 0:
            month += 1
    year = as_of.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, _calendar.monthrange(year, month)[1])


def forward_grid(  # noqa: PLR0913 - the template's grid parameters, spelled out
    curve: DiscountCurve,
    *,
    as_of: date,
    curve_frequency_months: int,
    calendar: Calendar,
    periods: int,
    convention: BusinessDayConvention = _MF,
    output_basis: DayCount = DayCount.ACT_360,
    end_of_month: bool = True,
) -> ForwardGrid:
    """Resample a discount curve onto the tenor-adjusted forward grid.

    Row 0 is the spot stub ``[as_of, anchor]`` with ``DF=1``, ``Yield=0``. Each of
    the ``periods`` following rows spans ``[boundary_{k-1}, boundary_k]`` where the
    boundaries roll from the frequency-aligned anchor by ``curve_frequency_months``
    and are business-day adjusted (``convention``); the anchor itself is kept raw
    (matching Eikon). ``discount_factor`` is the period DF ``DF(End)/DF(Start)`` and
    ``yield`` is the simple forward over the period in ``output_basis``.
    """
    if periods < 0:
        raise MultiCurveError("periods must be non-negative.")
    anchor = _period_anchor(as_of, curve_frequency_months)
    boundaries: list[date] = [anchor]
    for k in range(1, periods + 1):
        raw = add_calendar_months(anchor, k * curve_frequency_months, end_of_month=end_of_month)
        boundaries.append(calendar.adjust(raw, convention))

    rows: list[ForwardGridRow] = [
        ForwardGridRow(start=as_of, end=anchor, discount_factor=1.0, yield_=0.0)
    ]
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        df_start = curve.discount(start)
        df_end = curve.discount(end)
        rows.append(
            ForwardGridRow(
                start=start,
                end=end,
                discount_factor=df_end / df_start,
                yield_=simple_rate_from_discounts(df_start, df_end, start, end, output_basis),
            )
        )
    return ForwardGrid.create(as_of, curve_frequency_months, output_basis, rows)


def forward_grid_for_frequency(  # noqa: PLR0913 - mirrors forward_grid's governed inputs
    curve: DiscountCurve,
    *,
    as_of: date,
    frequency: str,
    calendar: Calendar,
    periods: int,
    convention: BusinessDayConvention = _MF,
    output_basis: DayCount = DayCount.ACT_360,
    end_of_month: bool = True,
) -> tuple[ForwardGridRow, ...]:
    """Resample a solved curve at a day/week/month/year frequency.

    This is the dense published-grid counterpart to :func:`forward_grid`.
    ``Calendar.add_tenor`` supplies business-day-aware ``1D``/``1W`` behavior
    as well as calendar month/year rolls; each output row remains a *period*
    forward with ``DF(End) / DF(Start)``. The row-zero spot stub is retained so
    all published frequencies share the same evidence shape.
    """
    if periods < 0:
        raise MultiCurveError("A forward grid needs a non-negative period count.")
    token = frequency.strip().upper()
    if not token:
        raise MultiCurveError("A forward-grid frequency is required.")

    rows: list[ForwardGridRow] = [
        ForwardGridRow(start=as_of, end=as_of, discount_factor=1.0, yield_=0.0)
    ]
    start = as_of
    for _ in range(periods):
        end = (
            calendar.advance(start, 1)
            if token == "1D"
            else calendar.add_tenor(start, token, convention, end_of_month=end_of_month)
        )
        if end <= start:
            raise MultiCurveError(f"Frequency {frequency!r} did not advance the grid date.")
        df_start = curve.discount(start)
        df_end = curve.discount(end)
        rows.append(
            ForwardGridRow(
                start=start,
                end=end,
                discount_factor=df_end / df_start,
                yield_=simple_rate_from_discounts(df_start, df_end, start, end, output_basis),
            )
        )
        start = end
    return tuple(rows)


def convert_basis(
    curve: DiscountCurve, start: date, end: date, output_basis: DayCount
) -> float:
    """The simple forward rate over ``[start, end]`` rendered in ``output_basis``.

    Discount factors are basis-invariant, so the output basis enters only through
    the year fraction — the Eikon "Convert to" contract. Supports money-market
    Act/360 / Act/365 / Act/364, plus Act/Act and 30/360 for bond-style output.
    """
    return simple_rate_from_discounts(
        curve.discount(start), curve.discount(end), start, end, output_basis
    )
