"""Forward-curve construction service (spec forward_curve_construction.md §2, §3.A, FC-3).

Orchestration over the finished pure quant core (``app/domain/curves/``): resolve
a governed :class:`~app.models.market_desk_curves.DeskCurveDefinition` into an
instrument set, solve the (single or OIS-discounted multi-) curve simultaneously
from the cob's quotes, resample onto the tenor-adjusted Start/End/DF/Yield forward
grid, and run the hard pre-publish QA gates (reprice residuals, forward
positivity/oscillation via ``forwards.qa_forwards``, monotone DF, pillar coverage).

Determinism is the contract (spec §2.7): the construction is a pure function of
``(quotes snapshot x definition version x calendar-as-of)``, so the same three
inputs yield an identical value-based ``input_digest`` and an identical grid.
Nothing volatile (timestamps, row ids) ever enters the digest.

This module NEVER writes canonical market-data rows: publication goes through the
existing determination -> ``publication.publish`` fan-out (desk-as-vendor), so
bitemporal storage, lineage/raw-tier provenance and audit come for free.

The definition CRUD helpers here mirror ``register.py``'s Track-2 rules exactly:
an approved version is immutable, a change mints version+1 with a rationale, and
the approver must differ from the proposer (dual control).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import func, select

from app.db.base import utc_now
from app.domain.curves.bootstrap import ZeroCurve
from app.domain.curves.calendars import (
    GHANA,
    KENYA,
    NIGERIA,
    SOUTH_AFRICA,
    USA,
    BusinessDayConvention,
    Calendar,
    CalendarError,
    tenor_to_months,
)
from app.domain.curves.conventions import (
    ConventionError,
    DayCount,
    daycount_from_eikon_code,
    year_fraction,
)
from app.domain.curves.forwards import ForwardQaResult, QaError, qa_forwards
from app.domain.curves.instruments import (
    DEFAULT_FUTURES_VOL,
    DepositHelper,
    FraHelper,
    FuturesHelper,
    InstrumentSetError,
    OisHelper,
    RateHelper,
    SwapHelper,
)
from app.domain.curves.interpolation import INTERPOLATION_METHODS
from app.domain.curves.multicurve import (
    CurveSet,
    ForwardGrid,
    ForwardGridRow,
    MarketQuote,
    MultiCurveError,
    SolvedCurve,
    build_curve_set,
    forward_grid,
    forward_grid_for_frequency,
)
from app.domain.curves.objects import canonical_input_digest
from app.models import DeskCurveDefinition, DeskDetermination
from app.services.market_desk import determinations, publication, register

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import DeskPublication

__all__ = [
    "CurveConstructionError",
    "CurveConstructionPillar",
    "CurveConstructionQa",
    "CurveConstructionResult",
    "CurveDefinitionSpec",
    "approve_curve_determination",
    "approve_definition_version",
    "build_forward_curve",
    "create_definition",
    "get_active_definition",
    "get_definition_version",
    "list_definitions",
    "propose_definition_version",
    "publish_curve_determination",
    "resolve_calendar",
    "stage_curve_determination",
    "submit_curve_determination",
    "to_determination_derived_values",
]

# Named calendars the desk seeds (spec §2.5). Grows with expansion markets; an
# unknown name is a governed-definition error, never a silent default.
_CALENDARS: dict[str, Calendar] = {
    "USA": USA,
    "GHANA": GHANA,
    "NIGERIA": NIGERIA,
    "KENYA": KENYA,
    "SOUTH_AFRICA": SOUTH_AFRICA,
}

_VALID_EXTRAPOLATION: frozenset[str] = frozenset({"flat_forward", "flat_zero"})

# Deposit / bill / money-market synonyms accepted in the quote grid.
_DEPOSIT_KINDS: frozenset[str] = frozenset(
    {"deposit", "depo", "money_market", "mm", "bill", "tbill"}
)

# QA defaults (overridable per definition via ``params``).
_DEFAULT_GRID_PERIODS = 8
_DEFAULT_QA_GRID_POINTS = 40
_DEFAULT_OSCILLATION_TOLERANCE = 3.0
_DEFAULT_PILLAR_MIN_COUNT = 3
_REPRICE_TOLERANCE = 1e-8  # the acid test the quant lib pins (spec §2.3)

# Every approved construction publishes this full bank-selectable family. The
# periods are governed defaults and can be overridden by
# ``params.grid_periods_by_frequency`` on a definition.
_PUBLISHED_GRID_FREQUENCIES: tuple[str, ...] = ("1D", "1M", "3M", "6M", "1Y")
_DEFAULT_GRID_PERIODS_BY_FREQUENCY: dict[str, int] = {
    "1D": 22,
    "1M": 120,
    "3M": 120,
    "6M": 60,
    "1Y": 30,
}


class CurveConstructionError(ValueError):
    """A construction request cannot produce a well-formed curve.

    Wraps the quant core's domain errors (bad instrument, non-convergent solve,
    failed calendar/convention resolution) so the operator API maps a single
    exception type to HTTP 422.
    """


# ---------------------------------------------------------------------------
# Result value objects (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurveConstructionPillar:
    """One solved pillar node: instrument, adjusted date, DF, reprice residual."""

    instrument: str
    tenor: str
    leg: str
    pillar_date: date
    quote: float
    discount_factor: float
    reprice_residual: float


@dataclass(frozen=True)
class CurveConstructionQa:
    """The hard pre-publish QA verdict (spec §3.A step 4)."""

    reprice_max_residual: float
    reprice_tolerance: float
    reprice_pass: bool
    forward_qa: ForwardQaResult
    pillar_count: int
    pillar_min_count: int
    pillar_coverage_pass: bool
    monotone_df_pass: bool
    passed: bool


@dataclass(frozen=True)
class CurveConstructionResult:
    """One immutable construction: forward grid + pillars + QA + value digest."""

    curve_code: str
    definition_version: int
    as_of: date
    output_basis: DayCount
    grid: ForwardGrid
    grids_by_frequency: dict[str, tuple[ForwardGridRow, ...]]
    pillars: tuple[CurveConstructionPillar, ...]
    qa: CurveConstructionQa
    input_digest: str


# ---------------------------------------------------------------------------
# Parameter resolution (definition strings -> quant-core objects)
# ---------------------------------------------------------------------------


def resolve_calendar(name: str) -> Calendar:
    """Resolve a governed calendar name to a seeded :class:`Calendar`."""
    calendar = _CALENDARS.get(name.strip().upper())
    if calendar is None:
        raise CurveConstructionError(
            f"Unknown calendar {name!r}; seeded calendars: {sorted(_CALENDARS)}."
        )
    return calendar


def _resolve_daycount(code: str) -> DayCount:
    key = code.strip().upper()
    for day_count in DayCount:
        if day_count.name == key or day_count.value.upper() == key:
            return day_count
    try:
        return daycount_from_eikon_code(key)
    except ConventionError as exc:
        raise CurveConstructionError(
            f"Unknown day count {code!r}; expected a DayCount name/value or Eikon code."
        ) from exc


def _resolve_convention(code: str) -> BusinessDayConvention:
    key = code.strip().upper()
    for convention in BusinessDayConvention:
        if convention.name == key or convention.value.upper() == key:
            return convention
    raise CurveConstructionError(
        f"Unknown roll convention {code!r}; expected one of "
        f"{[c.value for c in BusinessDayConvention]}."
    )


def _resolve_interpolation(method: str) -> str:
    key = method.strip().lower()
    if key not in INTERPOLATION_METHODS:
        raise CurveConstructionError(
            f"Unknown interpolation method {method!r}; expected one of "
            f"{list(INTERPOLATION_METHODS)}."
        )
    return key


def _resolve_extrapolation(rule: str) -> str:
    key = rule.strip().lower()
    if key not in _VALID_EXTRAPOLATION:
        raise CurveConstructionError(
            f"Unknown extrapolation rule {rule!r}; expected one of "
            f"{sorted(_VALID_EXTRAPOLATION)}."
        )
    return key


def _normalise_fra_tenor(part: str) -> str:
    token = part.strip().upper()
    if token and token[-1].isdigit():
        return f"{token}M"  # bare "3" means 3 months
    return token


def _build_helper(  # noqa: PLR0913 - the helper's governed construction parameters
    instrument: str,
    tenor: str,
    *,
    calendar: Calendar,
    spot_lag: int,
    convention: BusinessDayConvention,
    float_frequency_months: int,
    on_projection: bool,
    forward_start: date | None = None,
    sigma: float | None = None,
) -> RateHelper:
    """Resolve one quote into a self-repricing helper.

    ``forward_start`` (FC-G3), when set, makes deposit/FRA/swap/OIS instruments
    accrue from that future/IMM date instead of the spot date. ``sigma`` (FC-6a)
    is the futures->forward convexity vol, read only for a ``futures`` instrument
    (which is inherently forward-starting over ``[forward_start, forward_start +
    tenor]`` and requires ``forward_start`` as its IMM period start).
    """
    kind = instrument.strip().lower()
    if kind in _DEPOSIT_KINDS:
        return DepositHelper(
            tenor,
            calendar,
            spot_lag=spot_lag,
            convention=convention,
            on_projection=on_projection,
            forward_start=forward_start,
        )
    if kind == "fra":
        start_token, sep, end_token = tenor.lower().partition("x")
        if not sep:
            raise CurveConstructionError(
                f"FRA tenor {tenor!r} must be '<start>x<end>' (e.g. '3x6' or '3Mx6M')."
            )
        return FraHelper(
            _normalise_fra_tenor(start_token),
            _normalise_fra_tenor(end_token),
            calendar,
            spot_lag=spot_lag,
            convention=convention,
            forward_start=forward_start,
        )
    if kind == "swap":
        return SwapHelper(
            tenor,
            calendar,
            float_frequency_months=float_frequency_months,
            spot_lag=spot_lag,
            forward_start=forward_start,
        )
    if kind == "ois":
        return OisHelper(tenor, calendar, spot_lag=spot_lag, forward_start=forward_start)
    if kind == "futures":
        if forward_start is None:
            raise CurveConstructionError(
                "A 'futures' quote needs a forward_start (the IMM period start date)."
            )
        end = calendar.add_tenor(forward_start, tenor, convention)
        return FuturesHelper(
            forward_start,
            end,
            calendar,
            sigma=DEFAULT_FUTURES_VOL if sigma is None else sigma,
        )
    raise CurveConstructionError(
        f"Unknown instrument {instrument!r}; expected deposit/fra/swap/ois/futures."
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _QuoteLeg:
    instrument: str
    tenor: str
    leg: str
    quote: float
    helper: RateHelper
    pillar_date: date
    forward_start: date | None = None
    sigma: float | None = None


def _read_int_param(params: Mapping[str, Any], key: str, default: int) -> int:
    raw = params.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise CurveConstructionError(f"params.{key} must be an integer, got {raw!r}.") from exc


def _read_float_param(params: Mapping[str, Any], key: str, default: float) -> float:
    raw = params.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise CurveConstructionError(f"params.{key} must be numeric, got {raw!r}.") from exc


def _parse_optional_date(value: Any) -> date | None:
    """Read a quote's ``forward_start``: a ``date``, an ISO string, or ``None``."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CurveConstructionError(
            f"forward_start {value!r} must be an ISO date (YYYY-MM-DD)."
        ) from exc


def _parse_optional_float(value: Any, *, field: str) -> float | None:
    """Read a quote's optional numeric override (e.g. futures ``sigma``)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CurveConstructionError(f"{field} must be numeric, got {value!r}.") from exc


def _prepare_legs(  # noqa: PLR0913 - the governed construction parameters, spelled out
    quotes: Sequence[Mapping[str, Any]],
    *,
    calendar: Calendar,
    spot_lag: int,
    convention: BusinessDayConvention,
    float_frequency_months: int,
    as_of: date,
) -> tuple[tuple[_QuoteLeg, ...], tuple[_QuoteLeg, ...]]:
    """Resolve quotes into ascending-pillar discount and projection leg buckets."""
    discount: list[_QuoteLeg] = []
    projection: list[_QuoteLeg] = []
    for raw in quotes:
        instrument = str(raw.get("instrument", "")).strip()
        tenor = str(raw.get("tenor", "")).strip()
        leg = str(raw.get("leg", "discount")).strip().lower() or "discount"
        if not instrument or not tenor:
            raise CurveConstructionError("Each quote needs an instrument and a tenor.")
        if leg not in ("discount", "projection"):
            raise CurveConstructionError(f"quote leg {leg!r} must be 'discount' or 'projection'.")
        try:
            quote = float(raw["quote"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CurveConstructionError(
                f"quote for {instrument} {tenor} must be a number (decimal fraction)."
            ) from exc
        on_projection = leg == "projection"
        forward_start = _parse_optional_date(raw.get("forward_start"))
        sigma = _parse_optional_float(raw.get("sigma"), field=f"sigma for {instrument} {tenor}")
        try:
            helper = _build_helper(
                instrument,
                tenor,
                calendar=calendar,
                spot_lag=spot_lag,
                convention=convention,
                float_frequency_months=float_frequency_months,
                on_projection=on_projection,
                forward_start=forward_start,
                sigma=sigma,
            )
            pillar = helper.pillar_date(calendar, as_of)
        except (InstrumentSetError, ConventionError, CalendarError) as exc:
            raise CurveConstructionError(str(exc)) from exc
        entry = _QuoteLeg(
            instrument, tenor, leg, quote, helper, pillar, forward_start=forward_start, sigma=sigma
        )
        (projection if on_projection else discount).append(entry)

    discount.sort(key=lambda e: e.pillar_date)
    projection.sort(key=lambda e: e.pillar_date)
    return tuple(discount), tuple(projection)


def _zero_curve_of(curve: SolvedCurve) -> ZeroCurve:
    """Rebuild the underlying :class:`ZeroCurve` from a solved curve's public nodes."""
    times = tuple(
        year_fraction(curve.valuation_date, day, curve.day_count) for day in curve.node_dates
    )
    return ZeroCurve(
        valuation_date=curve.valuation_date,
        day_count=curve.day_count,
        times=times,
        zeros=curve.zeros,
        interpolation=curve.interpolation,
        extrapolation=curve.extrapolation,
    )


def _run_qa(
    *,
    output_curve: SolvedCurve,
    grid: ForwardGrid,
    worst_residual: float,
    pillar_count: int,
    params: Mapping[str, Any],
) -> CurveConstructionQa:
    zero_curve = _zero_curve_of(output_curve)
    last_time = max(
        year_fraction(output_curve.valuation_date, day, output_curve.day_count)
        for day in output_curve.node_dates
    )
    grid_points = max(_read_int_param(params, "qa_grid_points", _DEFAULT_QA_GRID_POINTS), 3)
    qa_grid = tuple(last_time * (i + 1) / grid_points for i in range(grid_points))
    positivity = bool(params.get("enforce_positive_forwards", True))
    tolerance = _read_float_param(params, "oscillation_tolerance", _DEFAULT_OSCILLATION_TOLERANCE)
    try:
        forward_qa = qa_forwards(
            zero_curve, qa_grid, positivity=positivity, oscillation_tolerance=tolerance
        )
    except QaError as exc:
        raise CurveConstructionError(str(exc)) from exc

    forward_rows = grid.rows[1:]  # row 0 is the spot stub (DF=1, yield=0)
    monotone_df_pass = all(0.0 < row.discount_factor <= 1.0 + 1e-12 for row in forward_rows)

    pillar_min = max(_read_int_param(params, "pillar_min_count", _DEFAULT_PILLAR_MIN_COUNT), 1)
    pillar_coverage_pass = pillar_count >= pillar_min
    reprice_pass = worst_residual <= _REPRICE_TOLERANCE

    return CurveConstructionQa(
        reprice_max_residual=worst_residual,
        reprice_tolerance=_REPRICE_TOLERANCE,
        reprice_pass=reprice_pass,
        forward_qa=forward_qa,
        pillar_count=pillar_count,
        pillar_min_count=pillar_min,
        pillar_coverage_pass=pillar_coverage_pass,
        monotone_df_pass=monotone_df_pass,
        passed=reprice_pass
        and forward_qa.passed
        and monotone_df_pass
        and pillar_coverage_pass,
    )


def _construction_digest(
    definition: DeskCurveDefinition,
    *,
    as_of: date,
    calendar: Calendar,
    legs: Sequence[_QuoteLeg],
) -> str:
    calendar_fingerprint = canonical_input_digest(
        {
            "name": calendar.name,
            "weekend": sorted(calendar.weekend),
            "holidays": sorted(day.isoformat() for day in calendar.holidays),
        }
    )
    payload: dict[str, Any] = {
        "curve_code": definition.curve_code,
        "definition_version": definition.version,
        "as_of": as_of.isoformat(),
        "calendar": calendar_fingerprint,
        "definition": {
            "currency": definition.currency,
            "curve_kind": definition.curve_kind,
            "interpolation_method": definition.interpolation_method,
            "output_daycount": definition.output_daycount,
            "curve_frequency": definition.curve_frequency,
            "payment_interval_months": definition.payment_interval_months,
            "spot_lag_days": definition.spot_lag_days,
            "roll_convention": definition.roll_convention,
            "extrapolation_rule": definition.extrapolation_rule,
            "projection_index": definition.projection_index,
            "discount_curve_code": definition.discount_curve_code,
            "params": definition.params,
        },
        "quotes": sorted(
            (
                {
                    "instrument": leg.instrument,
                    "tenor": leg.tenor,
                    "leg": leg.leg,
                    "quote": leg.quote,
                    # FC-G3/FC-6a: forward-start + futures vol are part of the
                    # construction inputs, so they MUST enter the value-based digest
                    # (spec §2.7) — else two curves differing only in forward-start
                    # would replay identically. ``None`` for a plain spot instrument.
                    "forward_start": leg.forward_start.isoformat() if leg.forward_start else None,
                    "sigma": leg.sigma,
                }
                for leg in legs
            ),
            key=lambda entry: (entry["leg"], entry["instrument"], entry["tenor"]),
        ),
    }
    return canonical_input_digest(payload)


def build_forward_curve(
    definition: DeskCurveDefinition,
    as_of: date,
    quotes: Sequence[Mapping[str, Any]],
    *,
    calendar: Calendar,
) -> CurveConstructionResult:
    """Construct the tenor-adjusted forward grid for a definition and a cob's quotes.

    Pure orchestration over the quant core: resolve helpers, solve the (single or
    OIS-discounted multi-) curve simultaneously, resample onto the Start/End/DF/Yield
    grid at the definition's curve frequency, and run the hard QA gates. Reproducible:
    same ``(quotes, definition version, calendar)`` -> identical grid and digest.
    """
    if not quotes:
        raise CurveConstructionError("At least one quote is required to construct a curve.")

    interpolation = _resolve_interpolation(definition.interpolation_method)
    extrapolation = _resolve_extrapolation(definition.extrapolation_rule)
    output_basis = _resolve_daycount(definition.output_daycount)
    convention = _resolve_convention(definition.roll_convention)
    freq_months = tenor_to_months(definition.curve_frequency)
    params = definition.params or {}
    periods = max(_read_int_param(params, "grid_periods", _DEFAULT_GRID_PERIODS), 1)
    end_of_month = bool(params.get("end_of_month", True))

    discount_legs, projection_legs = _prepare_legs(
        quotes,
        calendar=calendar,
        spot_lag=definition.spot_lag_days,
        convention=convention,
        float_frequency_months=definition.payment_interval_months,
        as_of=as_of,
    )
    if not discount_legs:
        raise CurveConstructionError(
            "At least one discount-leg quote is required (a self-discounting curve "
            "tags every quote as 'discount')."
        )

    discount_quotes = [MarketQuote(leg.helper, leg.quote) for leg in discount_legs]
    projection_quotes = (
        [MarketQuote(leg.helper, leg.quote) for leg in projection_legs] or None
    )
    try:
        curve_set: CurveSet = build_curve_set(
            as_of=as_of,
            calendar=calendar,
            discount_quotes=discount_quotes,
            projection_quotes=projection_quotes,
            interpolation=interpolation,
            extrapolation=extrapolation,  # type: ignore[arg-type]
        )
    except (MultiCurveError, InstrumentSetError, ConventionError, CalendarError) as exc:
        raise CurveConstructionError(str(exc)) from exc

    # The forward curve IS the projection index's forwards; for a self-discounting
    # single curve the solver returns projection == discount, so this is correct
    # in both regimes (spec §2.3).
    output_curve = curve_set.projection

    try:
        grid = forward_grid(
            output_curve,
            as_of=as_of,
            curve_frequency_months=freq_months,
            calendar=calendar,
            periods=periods,
            convention=convention,
            output_basis=output_basis,
            end_of_month=end_of_month,
        )
    except (MultiCurveError, ConventionError, CalendarError) as exc:
        raise CurveConstructionError(str(exc)) from exc

    configured_periods = params.get("grid_periods_by_frequency", {})
    if not isinstance(configured_periods, Mapping):
        raise CurveConstructionError(
            "grid_periods_by_frequency must be a mapping of frequency to count."
        )
    grids_by_frequency: dict[str, tuple[ForwardGridRow, ...]] = {}
    for frequency in _PUBLISHED_GRID_FREQUENCIES:
        default_periods = _DEFAULT_GRID_PERIODS_BY_FREQUENCY[frequency]
        frequency_periods = max(
            _read_int_param(configured_periods, frequency, default_periods), 1
        )
        try:
            grids_by_frequency[frequency] = forward_grid_for_frequency(
                output_curve,
                as_of=as_of,
                frequency=frequency,
                calendar=calendar,
                periods=frequency_periods,
                convention=convention,
                output_basis=output_basis,
                end_of_month=end_of_month,
            )
        except (MultiCurveError, ConventionError, CalendarError) as exc:
            raise CurveConstructionError(str(exc)) from exc

    all_legs = (*discount_legs, *projection_legs)
    pillars: list[CurveConstructionPillar] = []
    worst_residual = 0.0
    for leg in all_legs:
        residual = leg.helper.reprice_residual(
            leg.quote, curve_set.discount, curve_set.projection
        )
        leg_curve = curve_set.projection if leg.leg == "projection" else curve_set.discount
        pillars.append(
            CurveConstructionPillar(
                instrument=leg.instrument,
                tenor=leg.tenor,
                leg=leg.leg,
                pillar_date=leg.pillar_date,
                quote=leg.quote,
                discount_factor=leg_curve.discount(leg.pillar_date),
                reprice_residual=residual,
            )
        )
        worst_residual = max(worst_residual, abs(residual))

    qa = _run_qa(
        output_curve=output_curve,
        grid=grid,
        worst_residual=worst_residual,
        pillar_count=len(all_legs),
        params=params,
    )
    digest = _construction_digest(definition, as_of=as_of, calendar=calendar, legs=all_legs)

    return CurveConstructionResult(
        curve_code=definition.curve_code,
        definition_version=definition.version,
        as_of=as_of,
        output_basis=output_basis,
        grid=grid,
        grids_by_frequency=grids_by_frequency,
        pillars=tuple(pillars),
        qa=qa,
        input_digest=digest,
    )


# ---------------------------------------------------------------------------
# Publication translation (the aequor_desk adapter's derived_values contract)
# ---------------------------------------------------------------------------


def to_determination_derived_values(
    result: CurveConstructionResult,
    curve_code: str,
    definition: DeskCurveDefinition | None = None,
) -> dict[str, Any]:
    """Shape a construction into the ``derived_values`` the aequor_desk adapter reads.

    Conforms EXACTLY to the adapter contract (``aequor_desk/adapter.py``): a
    ``curves`` map keyed by AEQ.* code, each ``{"curve_type": "forward", "points":
    [{"tenor_months": int, "rate_pct": float}, ...]}`` with rates in PERCENT (the
    adapter divides by 100 to the canonical decimal-fraction store). The spot stub
    (row 0) is dropped — it is not a forward pillar. ``curves_qa_passed`` gates the
    curve scope in ``determination_scopes``. ``forward_grids`` carries the
    exact calendar-adjusted construction output for bank-side reproduction;
    canonical curve points remain the compact fan-out representation consumed
    by the engines.
    """
    freq_months = result.grid.curve_frequency_months
    points = [
        {
            "tenor_months": (index + 1) * freq_months,
            "rate_pct": row.yield_ * 100.0,
        }
        for index, row in enumerate(result.grid.rows[1:])
    ]
    forward_grid: dict[str, Any] = {
        "rows": [
            {
                "start": row.start.isoformat(),
                "end": row.end.isoformat(),
                "discount_factor": format(row.discount_factor, ".12f"),
                "forward_yield": format(row.yield_, ".12f"),
            }
            for row in result.grid.rows
        ],
        "grids": {
            frequency: {
                "rows": [
                    {
                        "start": row.start.isoformat(),
                        "end": row.end.isoformat(),
                        "discount_factor": format(row.discount_factor, ".12f"),
                        "forward_yield": format(row.yield_, ".12f"),
                    }
                    for row in rows
                ]
            }
            for frequency, rows in result.grids_by_frequency.items()
        },
    }
    if definition is not None:
        forward_grid["definition"] = {
            "version": definition.version,
            "calendar_name": definition.calendar_name,
            "instrument_set_ref": definition.instrument_set_ref,
            "projection_index": definition.projection_index,
            "discount_curve_code": definition.discount_curve_code,
            "interpolation_method": definition.interpolation_method,
            "output_daycount": definition.output_daycount,
            "payment_frequency": definition.payment_frequency,
            "payment_interval_months": definition.payment_interval_months,
            "curve_frequency": definition.curve_frequency,
            "spot_lag_days": definition.spot_lag_days,
            "roll_convention": definition.roll_convention,
            "extrapolation_rule": definition.extrapolation_rule,
        }
    return {
        "curves": {
            curve_code: {"curve_type": "forward", "points": points},
        },
        "forward_grids": {curve_code: forward_grid},
        "curves_qa_passed": result.qa.passed,
    }


# ---------------------------------------------------------------------------
# Definition governance (Track-2, mirrors register.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurveDefinitionSpec:
    """The full parameter surface of a curve definition version (draft input)."""

    curve_code: str
    currency: str
    calendar_name: str
    curve_kind: str
    projection_index: str | None
    discount_curve_code: str | None
    instrument_set_ref: str
    interpolation_method: str
    output_daycount: str
    payment_frequency: str | None
    payment_interval_months: int
    curve_frequency: str
    spot_lag_days: int
    roll_convention: str
    extrapolation_rule: str
    params: dict[str, Any]
    # FC-6d distribution tier (core < standard < premium); trailing default so
    # the platform-wide grandfather tier applies when a caller omits it.
    entitlement_tier: str = "standard"


def _apply_spec(row: DeskCurveDefinition, spec: CurveDefinitionSpec) -> None:
    row.currency = spec.currency
    row.calendar_name = spec.calendar_name
    row.curve_kind = spec.curve_kind
    row.projection_index = spec.projection_index
    row.discount_curve_code = spec.discount_curve_code
    row.instrument_set_ref = spec.instrument_set_ref
    row.interpolation_method = spec.interpolation_method
    row.output_daycount = spec.output_daycount
    row.payment_frequency = spec.payment_frequency
    row.payment_interval_months = spec.payment_interval_months
    row.curve_frequency = spec.curve_frequency
    row.spot_lag_days = spec.spot_lag_days
    row.roll_convention = spec.roll_convention
    row.extrapolation_rule = spec.extrapolation_rule
    row.params = spec.params
    row.entitlement_tier = spec.entitlement_tier


def _validate_spec(spec: CurveDefinitionSpec) -> None:
    """Fail fast on unconstructable knobs so a bad definition never approves."""
    resolve_calendar(spec.calendar_name)
    _resolve_interpolation(spec.interpolation_method)
    _resolve_extrapolation(spec.extrapolation_rule)
    _resolve_daycount(spec.output_daycount)
    _resolve_convention(spec.roll_convention)
    try:
        tenor_to_months(spec.curve_frequency)
    except CalendarError as exc:
        raise CurveConstructionError(
            f"curve_frequency {spec.curve_frequency!r} must be a whole-month tenor."
        ) from exc
    if spec.payment_interval_months <= 0:
        raise CurveConstructionError("payment_interval_months must be a positive integer.")
    if spec.spot_lag_days < 0:
        raise CurveConstructionError("spot_lag_days must be non-negative.")


def create_definition(
    db: Session, *, spec: CurveDefinitionSpec, change_rationale: str, proposed_by: str
) -> DeskCurveDefinition:
    """Register a NEW curve code at version 1 (draft)."""
    if not change_rationale.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A curve definition requires a non-empty rationale (Track 2).",
        )
    _validate_spec(spec)
    exists = db.scalar(
        select(func.count())
        .select_from(DeskCurveDefinition)
        .where(DeskCurveDefinition.curve_code == spec.curve_code)
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Curve definition {spec.curve_code!r} already exists; propose a new version.",
        )
    row = DeskCurveDefinition(
        curve_code=spec.curve_code,
        version=1,
        status="draft",
        change_rationale=change_rationale,
        proposed_by=proposed_by,
    )
    _apply_spec(row, spec)
    db.add(row)
    db.flush()
    return row


def list_definitions(
    db: Session, *, curve_code: str | None = None
) -> list[DeskCurveDefinition]:
    query = select(DeskCurveDefinition).order_by(
        DeskCurveDefinition.curve_code, DeskCurveDefinition.version
    )
    if curve_code is not None:
        query = query.where(DeskCurveDefinition.curve_code == curve_code)
    return list(db.scalars(query))


def get_definition_version(
    db: Session, curve_code: str, version: int
) -> DeskCurveDefinition:
    row = db.scalar(
        select(DeskCurveDefinition).where(
            DeskCurveDefinition.curve_code == curve_code,
            DeskCurveDefinition.version == version,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curve definition {curve_code!r} version {version} does not exist.",
        )
    return row


def propose_definition_version(
    db: Session, *, spec: CurveDefinitionSpec, change_rationale: str, proposed_by: str
) -> DeskCurveDefinition:
    """Track 2: draft version+1 with a documented rationale (one open draft per code)."""
    if not change_rationale.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A curve definition change requires a non-empty rationale (Track 2).",
        )
    _validate_spec(spec)
    latest = db.scalar(
        select(DeskCurveDefinition)
        .where(DeskCurveDefinition.curve_code == spec.curve_code)
        .order_by(DeskCurveDefinition.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Curve definition {spec.curve_code!r} does not exist; create it first.",
        )
    if latest.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Curve definition {spec.curve_code!r} v{latest.version} is still a draft; "
                "approve or supersede it before proposing another version."
            ),
        )
    row = DeskCurveDefinition(
        curve_code=spec.curve_code,
        version=latest.version + 1,
        status="draft",
        change_rationale=change_rationale,
        proposed_by=proposed_by,
    )
    _apply_spec(row, spec)
    db.add(row)
    db.flush()
    return row


def approve_definition_version(
    db: Session, *, curve_code: str, version: int, approved_by: str, effective_from: date
) -> DeskCurveDefinition:
    """Track-2 approval: dual control (approver != proposer), effective-dated, append-only."""
    row = get_definition_version(db, curve_code, version)
    if row.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Curve definition {curve_code!r} v{version} is already {row.status}.",
        )
    if approved_by.strip().lower() == row.proposed_by.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A curve definition cannot be approved by its proposer "
                "(dual control, spec §2.6)."
            ),
        )
    row.status = "approved"
    row.approved_by = approved_by
    row.approved_at = utc_now()
    row.effective_from = effective_from
    db.flush()
    return row


def get_active_definition(
    db: Session, curve_code: str, as_of: date
) -> DeskCurveDefinition | None:
    """The version construction applies: latest APPROVED whose ``effective_from``
    is on or before the as-of date."""
    return db.scalar(
        select(DeskCurveDefinition)
        .where(
            DeskCurveDefinition.curve_code == curve_code,
            DeskCurveDefinition.status == "approved",
            DeskCurveDefinition.effective_from <= as_of,
        )
        .order_by(DeskCurveDefinition.version.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# Per-cob maker-checker lifecycle (FC-G2)
#
# A constructed weekly curve is a Track-1 determination that walks the SAME
# maker-checker state machine the rates flow uses (spec §3.A steps 5-8): the
# analyst stages a DRAFT (never approved, never published), submits it, a
# distinct supervisor approves it (four-eyes, ``determinations._require_checker``),
# and only an APPROVED determination fans out. The definition-level dual control
# (FC-3, approver != proposer on the DEFINITION) is preserved AND this per-cob
# determination maker-checker rides on top — two independent control gates.
#
# The draft row is materialised here rather than via ``determinations.create_draft``
# because a curve's input snapshot is the cob's instrument-grid QUOTES, not the
# desk-observation series ``create_draft`` snapshots; the state transitions
# (submit / approve / publish) reuse ``determinations`` and ``publication``
# unchanged.
# ---------------------------------------------------------------------------


def _qa_payload(qa: CurveConstructionQa) -> dict[str, Any]:
    forward = qa.forward_qa
    return {
        "passed": qa.passed,
        "reprice_max_residual": qa.reprice_max_residual,
        "reprice_pass": qa.reprice_pass,
        "monotone_df_pass": qa.monotone_df_pass,
        "pillar_count": qa.pillar_count,
        "pillar_min_count": qa.pillar_min_count,
        "pillar_coverage_pass": qa.pillar_coverage_pass,
        "forward_positivity_pass": forward.positivity_pass,
        "forward_oscillation_pass": forward.oscillation_pass,
        "forward_min": forward.min_forward,
        "forward_total_variation_ratio": forward.total_variation_ratio,
    }


def _curve_input_snapshot(result: CurveConstructionResult) -> list[dict[str, str]]:
    """The value-based, id-free quote snapshot a curve determination records."""
    return sorted(
        (
            {"instrument": leg.instrument, "tenor": leg.tenor, "quote": str(leg.quote)}
            for leg in result.pillars
        ),
        key=lambda entry: (entry["instrument"], entry["tenor"]),
    )


def _ensure_curve_qa(determination: DeskDetermination) -> None:
    """Hard QA gate for a constructed-curve determination (spec §3.A step 4).

    The determinations state machine is deliberately QA-agnostic, so — exactly
    like the rates flow's ``calculation.ensure_approvable`` — the curve's hard
    gate is enforced at THIS service layer before submit / approve. A curve
    determination stores its QA verdict under ``qa_results['passed']``.
    """
    qa = determination.qa_results or {}
    if qa.get("passed") is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Curve construction failed a hard QA gate; this action is blocked.",
        )


def stage_curve_determination(  # noqa: PLR0913 - the construction + governance parameters
    db: Session,
    *,
    definition: DeskCurveDefinition,
    as_of: date,
    quotes: Sequence[Mapping[str, Any]],
    calendar: Calendar,
    prepared_by: str,
    methodology_code: str = register.DEFAULT_METHODOLOGY_CODE,
    research_adjustments: Sequence[Mapping[str, Any]] | None = None,
) -> DeskDetermination:
    """Stage a DRAFT determination for a cob's constructed curve (FC-G2 step 1).

    Constructs the curve (running the hard QA gate), resolves the active desk
    methodology (spec §6 "the weekly build is a Track-1 determination stamped
    with the version"), and writes a determination in the ``draft`` state
    carrying the construction's derived values, value-based ``input_digest`` and
    quote snapshot. NEVER approved, never published — that is the whole point of
    the split. The QA verdict is recorded (so the analyst can inspect a failure)
    but does not block staging; ``submit`` / ``approve`` enforce it. Bounded
    ``research_adjustments`` (Option B) are persisted via
    ``determinations.set_research_adjustments`` — audited, digest-eligible, and
    never a rewrite of the methodology register.
    """
    if definition.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Curve definition {definition.curve_code!r} v{definition.version} is "
            f"{definition.status!r}; only an approved definition can be staged.",
        )
    result = build_forward_curve(definition, as_of, quotes, calendar=calendar)
    methodology = register.get_active(db, methodology_code, as_of)
    if methodology is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No approved methodology version of {methodology_code!r} is effective on "
                f"{as_of.isoformat()}; approve one before staging a constructed curve."
            ),
        )
    determination = DeskDetermination(
        cob_date=as_of,
        methodology_code=methodology.methodology_code,
        methodology_version=methodology.version,
        input_snapshot=_curve_input_snapshot(result),
        input_digest=result.input_digest,
        derived_values=to_determination_derived_values(
            result, definition.curve_code, definition
        ),
        qa_results=_qa_payload(result.qa),
        status="draft",
        prepared_by=prepared_by,
    )
    db.add(determination)
    db.flush()
    if research_adjustments:
        determinations.set_research_adjustments(
            db,
            determination.id,
            adjustments=[dict(entry) for entry in research_adjustments],
            applied_by=prepared_by,
        )
    return determination


def submit_curve_determination(db: Session, determination_id: Any) -> DeskDetermination:
    """Maker step (FC-G2 step 2): 'definition vX applied to this cob, QA green'.

    Enforces the hard QA gate, then reuses ``determinations.submit_for_review``
    (which refuses anything not in ``draft``).
    """
    _ensure_curve_qa(determinations.get(db, determination_id))
    return determinations.submit_for_review(db, determination_id)


def approve_curve_determination(
    db: Session, determination_id: Any, *, reviewed_by: str
) -> DeskDetermination:
    """Checker step (FC-G2 step 3): four-eyes approval.

    Enforces the hard QA gate, then reuses ``determinations.approve`` whose
    ``_require_checker`` rejects ``reviewed_by == prepared_by`` (422) and
    refuses anything not in ``pending_review`` (409).
    """
    _ensure_curve_qa(determinations.get(db, determination_id))
    return determinations.approve(db, determination_id, reviewed_by=reviewed_by)


def publish_curve_determination(
    db: Session, determination_id: Any, *, actor: str
) -> DeskPublication:
    """Publish step (FC-G2 step 4): only an APPROVED determination fans out.

    Pure reuse of ``publication.publish`` (which refuses anything not
    ``approved``/``published`` with 409), so bitemporal storage,
    lineage/raw-tier provenance and per-bank audit come for free.
    """
    return publication.publish(db, determination_id, actor=actor)
