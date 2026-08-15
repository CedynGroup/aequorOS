"""FX-forward (outright) curve construction service (spec FC-6b, §7, §1.2 item 10).

Orchestration over the pure CIP composition in ``app.domain.curves.fx_forward``:
resolve two single-currency discount curves, then compose them with an FX spot
under covered interest parity into an outright forward grid. Writes nothing — this
is a pure PREVIEW (the operator endpoint only reads), the same posture as the
curve-construction ``/construct`` seam.

Each leg is resolved one of two ways (spec FC-6b):

* ``"quotes"`` — explicit money-market / swap / OIS pillar quotes solved into a
  self-discounting :class:`~app.domain.curves.multicurve.SolvedCurve` via
  ``multicurve.build_curve_set``. This is the EXACT path (each currency's own
  discount curve, bootstrapped from its quotes).
* ``"published"`` — an already-published desk curve code, read back through the
  determinations READ layer (``determinations.list_determinations``) and
  reconstructed into a discount curve from its stored forward grid. See
  :func:`_leg_curve_from_published` for the documented front-stub approximation
  this entails; it is a convenience, not the reference path.

**Cross-currency basis honesty (spec §1.2 item 10).** ``basis_bps`` is a governed
additive spread on the base leg, ``0`` by default = textbook CIP. Calibrating a
REAL basis needs cross-currency basis-swap quotes that are not in this platform's
data scope today, so a non-zero value is an assumption, never a calibrated number
— ``basis_calibrated`` is always ``False`` and the response says so.

This module edits none of the read/publication/entitlement layers; it only calls
their public read functions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from app.domain.curves.calendars import (
    BusinessDayConvention,
    Calendar,
    CalendarError,
)
from app.domain.curves.conventions import ConventionError, DayCount, year_fraction
from app.domain.curves.fx_forward import FxForwardError, build_fx_forward_curve
from app.domain.curves.instruments import (
    DepositHelper,
    FraHelper,
    InstrumentSetError,
    OisHelper,
    RateHelper,
    SwapHelper,
)
from app.domain.curves.multicurve import (
    MarketQuote,
    MultiCurveError,
    SolvedCurve,
    build_curve_set,
)
from app.services.market_desk import curve_construction, determinations

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "FxForwardConstructionResult",
    "FxForwardRow",
    "FxForwardServiceError",
    "FxLegQuoteSpec",
    "FxLegSpec",
    "construct_fx_forward",
    "resolve_grid_dates",
]

_MF = BusinessDayConvention.MODIFIED_FOLLOWING
_INTERNAL_DAY_COUNT = DayCount.ACT_365F
_DEPOSIT_KINDS: frozenset[str] = frozenset({"deposit", "depo", "money_market", "mm"})


class FxForwardServiceError(ValueError):
    """An FX-forward construction request cannot produce a well-formed curve.

    Wraps the quant core's domain errors plus this service's own resolution
    failures (unknown calendar/day-count, missing published curve) so the operator
    API maps a single exception type to HTTP 422.
    """


# ---------------------------------------------------------------------------
# Input specs (what the endpoint hands the service)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FxLegQuoteSpec:
    """One rate-curve pillar quote for a leg (rate as a decimal fraction)."""

    instrument: str
    tenor: str
    quote: float
    forward_start: date | None = None


@dataclass(frozen=True)
class FxLegSpec:
    """One currency leg: solve from ``quotes`` or read a ``published`` curve code."""

    source: Literal["quotes", "published"]
    calendar_name: str | None = None
    spot_lag_days: int = 2
    interpolation: str = "log_linear_df"
    extrapolation: str = "flat_forward"
    float_frequency_months: int = 6
    quotes: tuple[FxLegQuoteSpec, ...] = ()
    curve_code: str | None = None

    @property
    def source_label(self) -> str:
        return self.curve_code if self.source == "published" and self.curve_code else "quotes"


# ---------------------------------------------------------------------------
# Result value objects (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FxForwardRow:
    """One outright-forward node: the CIP forward and its forward points at ``date``."""

    date: date
    year_fraction: float
    df_base: float
    df_quote: float
    forward_rate: float
    forward_points: float


@dataclass(frozen=True)
class FxForwardConstructionResult:
    """One immutable FX-forward construction: outright grid + value digest."""

    base_ccy: str
    quote_ccy: str
    spot: float
    as_of: date
    basis_bps: float
    day_count: DayCount
    base_source: str
    quote_source: str
    input_digest: str
    rows: tuple[FxForwardRow, ...]
    #: A real cross-currency basis needs basis-swap quotes (out of data scope);
    #: this is ALWAYS False so a non-zero ``basis_bps`` never reads as calibrated.
    basis_calibrated: bool = field(default=False)

    @property
    def pair(self) -> str:
        return f"{self.base_ccy}{self.quote_ccy}"


# ---------------------------------------------------------------------------
# Convention resolution (local; never edits curve_construction)
# ---------------------------------------------------------------------------


def _resolve_day_count(code: str) -> DayCount:
    key = code.strip().upper()
    for day_count in DayCount:
        if day_count.name == key or day_count.value.upper() == key:
            return day_count
    raise FxForwardServiceError(
        f"Unknown day count {code!r}; expected a DayCount name/value "
        f"(e.g. ACT_365F, ACT/360)."
    )


def _resolve_calendar(name: str) -> Calendar:
    try:
        return curve_construction.resolve_calendar(name)
    except curve_construction.CurveConstructionError as exc:
        raise FxForwardServiceError(str(exc)) from exc


def resolve_grid_dates(
    as_of: date, tenors: Sequence[str], *, calendar_name: str, spot_lag_days: int = 2
) -> tuple[date, ...]:
    """Resolve a tenor grid to strictly-increasing, calendar-adjusted forward dates.

    Each tenor rolls from the FX spot date (``spot_date(as_of, spot_lag_days)``) by
    the tenor and is Modified-Following adjusted — the standard outright-forward
    value dates. Duplicate/non-increasing resolved dates are a request error.
    """
    calendar = _resolve_calendar(calendar_name)
    spot = calendar.spot_date(as_of, spot_lag_days)
    dates: list[date] = []
    for tenor in tenors:
        try:
            dates.append(calendar.add_tenor(spot, tenor, _MF))
        except (CalendarError, ConventionError) as exc:
            raise FxForwardServiceError(f"Bad tenor {tenor!r}: {exc}") from exc
    for left, right in zip(dates, dates[1:], strict=False):
        if right <= left:
            raise FxForwardServiceError(
                "Tenor grid resolves to non-increasing dates; supply tenors in "
                "ascending order with no duplicates."
            )
    return tuple(dates)


# ---------------------------------------------------------------------------
# Leg resolution: quotes -> SolvedCurve
# ---------------------------------------------------------------------------


def _build_helper(
    spec: FxLegQuoteSpec, *, calendar: Calendar, spot_lag: int, float_frequency_months: int
) -> RateHelper:
    kind = spec.instrument.strip().lower()
    if kind in _DEPOSIT_KINDS:
        return DepositHelper(
            spec.tenor, calendar, spot_lag=spot_lag, forward_start=spec.forward_start
        )
    if kind == "swap":
        return SwapHelper(
            spec.tenor,
            calendar,
            float_frequency_months=float_frequency_months,
            spot_lag=spot_lag,
            forward_start=spec.forward_start,
        )
    if kind == "ois":
        return OisHelper(
            spec.tenor, calendar, spot_lag=spot_lag, forward_start=spec.forward_start
        )
    if kind == "fra":
        start_token, sep, end_token = spec.tenor.lower().partition("x")
        if not sep:
            raise FxForwardServiceError(
                f"FRA tenor {spec.tenor!r} must be '<start>x<end>' (e.g. '3x6')."
            )
        return FraHelper(
            _normalise_fra_tenor(start_token),
            _normalise_fra_tenor(end_token),
            calendar,
            spot_lag=spot_lag,
            forward_start=spec.forward_start,
        )
    raise FxForwardServiceError(
        f"Unknown instrument {spec.instrument!r}; expected deposit/swap/ois/fra."
    )


def _normalise_fra_tenor(part: str) -> str:
    token = part.strip().upper()
    if token and token[-1].isdigit():
        return f"{token}M"
    return token


def _leg_curve_from_quotes(leg: FxLegSpec, as_of: date) -> SolvedCurve:
    """Bootstrap a self-discounting discount curve from a leg's pillar quotes."""
    if not leg.quotes:
        raise FxForwardServiceError("A 'quotes' leg needs at least one pillar quote.")
    if not leg.calendar_name:
        raise FxForwardServiceError("A 'quotes' leg needs a calendar_name.")
    calendar = _resolve_calendar(leg.calendar_name)
    entries: list[tuple[date, MarketQuote]] = []
    for spec in leg.quotes:
        if not spec.instrument.strip() or not spec.tenor.strip():
            raise FxForwardServiceError("Each quote needs an instrument and a tenor.")
        try:
            helper = _build_helper(
                spec,
                calendar=calendar,
                spot_lag=leg.spot_lag_days,
                float_frequency_months=leg.float_frequency_months,
            )
            pillar = helper.pillar_date(calendar, as_of)
        except (InstrumentSetError, ConventionError, CalendarError) as exc:
            raise FxForwardServiceError(str(exc)) from exc
        entries.append((pillar, MarketQuote(helper, spec.quote)))
    entries.sort(key=lambda item: item[0])
    try:
        curve_set = build_curve_set(
            as_of=as_of,
            calendar=calendar,
            discount_quotes=[quote for _, quote in entries],
            interpolation=leg.interpolation,
            extrapolation=leg.extrapolation,  # type: ignore[arg-type]
        )
    except (MultiCurveError, InstrumentSetError, ConventionError, CalendarError) as exc:
        raise FxForwardServiceError(str(exc)) from exc
    return curve_set.discount


# ---------------------------------------------------------------------------
# Leg resolution: published curve code -> SolvedCurve
# ---------------------------------------------------------------------------


def _leg_curve_from_published(db: Session, curve_code: str, as_of: date) -> SolvedCurve:
    """Reconstruct a discount curve from a published desk curve's forward grid.

    Reads the read layer only (``determinations.list_determinations`` for the
    ``as_of`` cob in the ``published`` state) and chains the grid's per-period
    discount factors into cumulative discount factors, then continuously-compounded
    Act/365F zeros feeding a :class:`SolvedCurve`.

    FRONT-STUB APPROXIMATION (documented, honest): the published forward grid's
    row 0 is a spot stub whose discount factor is hardcoded to ``1.0`` (it carries
    the anchor date, not the true ``DF(as_of -> anchor)``). We therefore anchor the
    reconstructed curve at ``DF(anchor) = 1.0``; discounting over the short
    ``[as_of, anchor]`` stub is flattened. For an outright FX-forward PREVIEW this
    front-end effect is small and bounded; the exact path is the ``quotes`` leg.
    """
    determination = _latest_published_curve(db, curve_code, as_of)
    grids = determination.derived_values.get("forward_grids")
    if not isinstance(grids, dict) or curve_code not in grids:
        raise FxForwardServiceError(
            f"Published determination for {curve_code!r} on {as_of.isoformat()} "
            "carries no forward grid to reconstruct a discount curve from."
        )
    rows = grids[curve_code].get("rows")
    if not isinstance(rows, list) or len(rows) < 2:
        raise FxForwardServiceError(
            f"Published forward grid for {curve_code!r} is too short to reconstruct."
        )
    node_dates: list[date] = []
    discount_factors: list[float] = []
    cumulative = 1.0
    try:
        anchor = date.fromisoformat(str(rows[0]["end"]))
    except (KeyError, ValueError) as exc:
        raise FxForwardServiceError(
            f"Published forward grid for {curve_code!r} has a malformed spot-stub row."
        ) from exc
    node_dates.append(anchor)
    discount_factors.append(cumulative)  # anchor stub: DF = 1.0 (documented)
    for row in rows[1:]:
        try:
            end = date.fromisoformat(str(row["end"]))
            period_df = float(row["discount_factor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FxForwardServiceError(
                f"Published forward grid for {curve_code!r} has a malformed row."
            ) from exc
        if period_df <= 0.0:
            raise FxForwardServiceError(
                f"Published forward grid for {curve_code!r} has a non-positive period DF."
            )
        cumulative *= period_df
        node_dates.append(end)
        discount_factors.append(cumulative)

    zeros: list[float] = []
    for day, df in zip(node_dates, discount_factors, strict=True):
        tau = year_fraction(as_of, day, _INTERNAL_DAY_COUNT)
        zeros.append(0.0 if tau <= 0.0 else -math.log(df) / tau)
    try:
        return SolvedCurve(as_of, tuple(node_dates), tuple(zeros))
    except MultiCurveError as exc:
        raise FxForwardServiceError(str(exc)) from exc


def _latest_published_curve(db: Session, curve_code: str, as_of: date) -> Any:
    """The published determination carrying ``curve_code`` on the ``as_of`` cob."""
    published = determinations.list_determinations(
        db, cob_date=as_of, status_filter="published"
    )
    for determination in published:
        curves = determination.derived_values.get("curves")
        if isinstance(curves, dict) and curve_code in curves:
            return determination
    raise FxForwardServiceError(
        f"No published desk curve {curve_code!r} on cob {as_of.isoformat()}; "
        "publish it first or supply pillar quotes for this leg."
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _resolve_leg(db: Session | None, leg: FxLegSpec, as_of: date) -> SolvedCurve:
    if leg.source == "quotes":
        return _leg_curve_from_quotes(leg, as_of)
    if leg.source == "published":
        if db is None:
            raise FxForwardServiceError(
                "A 'published' leg needs a database session to read the curve."
            )
        if not leg.curve_code:
            raise FxForwardServiceError("A 'published' leg needs a curve_code.")
        return _leg_curve_from_published(db, leg.curve_code, as_of)
    raise FxForwardServiceError(f"Unknown leg source {leg.source!r}.")


def construct_fx_forward(  # noqa: PLR0913 - the governed construction parameters, spelled out
    db: Session | None,
    *,
    as_of: date,
    base_ccy: str,
    quote_ccy: str,
    spot: float,
    dates: Sequence[date],
    base_leg: FxLegSpec,
    quote_leg: FxLegSpec,
    basis_bps: float = 0.0,
    day_count: str = "ACT_365F",
) -> FxForwardConstructionResult:
    """Build the outright FX-forward curve by covered interest parity (preview).

    Resolves each leg to a discount curve (quotes -> bootstrap, or a published
    code -> reconstruction), then composes them with ``spot`` and the optional
    ``basis_bps`` through the pure ``build_fx_forward_curve``. Reproducible: the
    same inputs yield an identical value-based ``input_digest`` and grid.
    """
    if base_ccy.strip().upper() == quote_ccy.strip().upper():
        raise FxForwardServiceError("base_ccy and quote_ccy must differ.")
    if not dates:
        raise FxForwardServiceError("At least one forward date is required.")
    resolved_day_count = _resolve_day_count(day_count)

    base_curve = _resolve_leg(db, base_leg, as_of)
    quote_curve = _resolve_leg(db, quote_leg, as_of)
    if base_curve.valuation_date != as_of or quote_curve.valuation_date != as_of:
        raise FxForwardServiceError(
            "Both leg curves must be struck on the FX as-of date."
        )

    try:
        curve = build_fx_forward_curve(
            spot,
            base_curve,
            quote_curve,
            tuple(dates),
            basis_bps=basis_bps,
            day_count=resolved_day_count,
            base_ccy=base_ccy.strip().upper(),
            quote_ccy=quote_ccy.strip().upper(),
        )
    except (FxForwardError, MultiCurveError, ConventionError) as exc:
        raise FxForwardServiceError(str(exc)) from exc

    rows = tuple(
        FxForwardRow(
            date=point.date,
            year_fraction=point.year_fraction,
            df_base=point.df_base,
            df_quote=point.df_quote,
            forward_rate=point.forward_rate,
            forward_points=point.forward_points,
        )
        for point in curve.points
    )
    return FxForwardConstructionResult(
        base_ccy=curve.base_ccy,
        quote_ccy=curve.quote_ccy,
        spot=spot,
        as_of=as_of,
        basis_bps=basis_bps,
        day_count=resolved_day_count,
        base_source=base_leg.source_label,
        quote_source=quote_leg.source_label,
        input_digest=curve.input_digest,
        rows=rows,
    )
