"""The desk determination calculation pipeline (spec §5 steps 1-9, §6).

The deterministic middle of the desk: the transformation from a draft
determination's captured observations into published curves and rates, driven
ENTIRELY by ``(input_snapshot) x (approved methodology parameters)``.

Reproducibility invariant (spec §5 "Recorded per publication"): the pure core
:func:`run_pipeline` reads only the snapshot and the parameter set — no DB, no
clock, no randomness — so given a determination's stored ``input_snapshot``
and its methodology version, every derived value and curve digest is exactly
reproducible, byte for byte, forever.

Capture vs computation split: :func:`compute_determination` first FINALIZES
the draft's input snapshot (:func:`build_calculation_snapshot` — the only
DB-reading step, spec §5 step 1: the default point-in-time snapshot plus the
windowed history the methodology needs — the interbank/T-bill trailing
windows, the MPR path, the GRR reference-month inputs, per-bank APRs and GFIM
bond quotes), re-digests it, then runs the pure pipeline and attaches results
via ``determinations.set_results``. Only DRAFTS are computable; once a
determination is submitted, what the checker reviews is what was computed.

Methodology v1 substance (calibrated 2026-08-09, evidence:
``scripts/desk_calibration_ghana.py``):

- **AGD (AEQ.GHS.OIS) short end** — a :class:`MeetingDateStepCurve` anchored
  at ``MPR + rolling mean of (interbank - MPR)`` over
  ``overnight_spread_window_bdays``. Every input directly observable; NO
  cointegration in the level.
- **AGD longer end** — sovereign zero + a governed discounting-basis curve.
  The v1 default rule is a DISCLOSED assumption: the basis at the shortest
  liquid tenor is the observed (step-curve-implied short zero - sovereign
  short zero), tapering linearly to zero by ``basis_taper_tenor_y``; a
  Track-2 governed ``explicit_bps_by_tenor_y`` map overrides it when set.
- **Cointegration is a weekly DIAGNOSTIC only** — Engle-Granger on the
  trailing window, recorded in ``qa_results`` with
  ``significance_disclosed: true``; it never sets a published level
  (rejected at the 5% bar in every regime window at calibration). Promotion
  to level-setter is a documented future Track-2 event.
- **Per-series treatments** — every series entering or leaving the pipeline
  must carry a declared treatment in the methodology's
  ``series_methodologies`` map; an undeclared series is a hard error, never a
  silent pass-through.

Float/Decimal boundary: numerics are float64 through ``app/domain/curves``;
every number leaving the pipeline into ``derived_values``/``qa_results`` is a
fixed-precision decimal STRING (canonical-JSON-stable, the platform's
value-based hashing idiom).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select

from app.domain.curves.bootstrap import (
    BillQuote,
    BondQuote,
    BootstrapError,
    ZeroCurve,
    bootstrap_zero_curve,
)
from app.domain.curves.cointegration import (
    ADF_CRITICAL_VALUES,
    CointegrationError,
    engle_granger,
)
from app.domain.curves.conventions import DayCount, discount_to_yield
from app.domain.curves.forwards import ForwardQaResult, qa_forwards
from app.domain.curves.nss import fit_nss, nss_zero
from app.domain.curves.objects import CurveBuildResult, CurveDefinition, CurveNodes
from app.domain.curves.ois_step import MeetingDateStepCurve
from app.models import DeskObservation
from app.services.market_desk import determinations

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from app.models import DeskDetermination, DeskMethodology

__all__ = [
    "EMITTED_RATE_SERIES",
    "CalculationError",
    "build_calculation_snapshot",
    "compute_determination",
    "ensure_approvable",
    "resolve_treatment",
    "run_pipeline",
]

# Spec §8 curve codes.
AGS_CODE = "AEQ.GHS.SOV.ZERO"
FWD_CODE = "AEQ.GHS.SOV.FWD"
AGD_CODE = "AEQ.GHS.OIS"

# Input series grammar (desk observation codes).
MPR_SERIES = "GHS.MPR"
INTERBANK_SERIES = "GHS.INTERBANK.ON"
GRR_SERIES = "GHS.GRR"
USDGHS_MID_SERIES = "GHS.USDGHS.MID"
USDGHS_REF_SERIES = "GHS.FX.USDGHS.REF"
APR_PREFIX = "GHS.APR."
BOND_PREFIX = "GHS.GOG.BOND."

# Emitted (published) rate series.
LENDING_INDICATOR_SERIES = "GHS.LENDING.INDICATOR"
GRR_BASE_SERIES = "GHS.BASE.GRR_CONSISTENT"

TBILL_TENOR_DAYS: tuple[int, ...] = (91, 182, 364)

#: Every rate series the pipeline can emit into ``derived_values['rates']`` —
#: the treatment-map completeness contract: each must carry a declared
#: treatment in the methodology's ``series_methodologies``.
EMITTED_RATE_SERIES: tuple[str, ...] = (
    MPR_SERIES,
    GRR_SERIES,
    INTERBANK_SERIES,
    "GHS.TBILL.91.YIELD",
    "GHS.TBILL.182.YIELD",
    "GHS.TBILL.364.YIELD",
    LENDING_INDICATOR_SERIES,
    GRR_BASE_SERIES,
)

_ACT364 = 364.0
_MONTHS_PER_YEAR = 12.0
# engle_granger's own floor: maxlag + 12 observations with the default cap 8.
_MIN_EG_PAIRS = 20
# Max staleness of the interbank print paired with a weekly auction print in
# the cointegration diagnostic (auction-date alignment; calendar days).
_EG_PAIR_MAX_GAP_DAYS = 7
# Derived-vs-observed T-bill yield reconstruction tolerance (pp): BoG rounds
# both published legs to 4 dp, so an honest pair reconstructs within ~1 bp.
_TBILL_RECON_TOLERANCE_PP = 0.02


class CalculationError(ValueError):
    """The snapshot or parameter set cannot produce a determination.

    Raised for structural refusals (undeclared series, missing required
    inputs, malformed bond grammar) — never for QA-gate failures, which are
    RECORDED in ``qa_results`` with ``qa_passed=False`` instead.
    """


# ---------------------------------------------------------------------------
# Formatting: every published number is a fixed-precision decimal string.
# ---------------------------------------------------------------------------


def _fmt(value: float, places: int = 6) -> str:
    text = f"{value:.{places}f}"
    if text.startswith("-") and float(text) == 0.0:
        return f"{0.0:.{places}f}"  # normalize -0.000000
    return text


def _canonical_json(value: Any) -> str:
    """Canonical JSON — the snapshot sort/dedup key (the input_hash idiom)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Treatments (spec §5 step 1: which rule governs each series).
# ---------------------------------------------------------------------------


def resolve_treatment(series_code: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """The declared treatment for a series — exact match first, then the
    longest matching ``PREFIX.*`` pattern. An undeclared series is a hard
    error: the pipeline never silently passes unknown data through."""
    table = parameters.get("series_methodologies")
    if not isinstance(table, dict) or not table:
        raise CalculationError(
            "methodology parameters carry no 'series_methodologies' map; "
            "every series must have a declared treatment (spec §5)."
        )
    exact = table.get(series_code)
    if isinstance(exact, dict):
        return exact
    patterns = sorted(
        (key for key in table if key.endswith(".*")), key=lambda key: (-len(key), key)
    )
    for pattern in patterns:
        if series_code.startswith(pattern[:-1]):
            entry = table[pattern]
            if isinstance(entry, dict):
                return entry
    raise CalculationError(
        f"series {series_code!r} has no declared treatment in the methodology's "
        "series_methodologies map; declare one (Track 2) before it can be computed."
    )


# ---------------------------------------------------------------------------
# Snapshot parsing and cleaning (steps 1-2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Obs:
    as_of: date
    value: float


@dataclass
class _Ctx:
    """Cleaned per-series observations plus the QA flag ledger."""

    cob: date
    params: dict[str, Any]
    series: dict[str, list[_Obs]]
    flags: list[dict[str, str]] = field(default_factory=list)

    def flag(self, series_code: str, flag: str, detail: str = "") -> None:
        self.flags.append({"series": series_code, "flag": flag, "detail": detail})

    def latest(self, series_code: str) -> _Obs | None:
        observations = self.series.get(series_code)
        return observations[-1] if observations else None

    def prevailing(self, series_code: str, day: date) -> _Obs | None:
        for observation in reversed(self.series.get(series_code, [])):
            if observation.as_of <= day:
                return observation
        return None

    def treatment(self, series_code: str) -> dict[str, Any]:
        return resolve_treatment(series_code, self.params)

    def is_stale(self, series_code: str, observation: _Obs) -> bool:
        """Staleness against the series' declared limit; stale observations
        still publish, carrying a QA flag (carry-forward policy)."""
        limit = self.treatment(series_code).get(
            "max_staleness_days", self.params.get("max_staleness_days", 5)
        )
        stale = (self.cob - observation.as_of).days > int(limit)
        if stale:
            self.flag(
                series_code,
                "stale_carry_forward",
                f"as_of {observation.as_of.isoformat()} exceeds max_staleness_days={limit}",
            )
        return stale


def _collapse_same_date(
    series_code: str,
    treatment: dict[str, Any],
    values: list[Decimal],
    flags: list[dict[str, str]],
) -> float:
    """Spec §5 step 2 duplicate rule, applied deterministically.

    GFIM ``bond_quote`` duplicates collapse by volume-weighted mean — with no
    volumes in the value-only snapshot the weights degenerate to equal. Every
    other (BoG) series is last-wins; the snapshot is canonically sorted and
    carries no capture-time order, so "last" is defined canonically as the
    greatest value in sorted order.
    """
    unique = sorted(set(values))
    if len(unique) == 1:
        return float(unique[0])
    if treatment.get("treatment") == "bond_quote":
        resolved = float(sum(unique) / len(unique))
        rule = "volume_weight_equal"
    else:
        resolved = float(unique[-1])
        rule = "last_wins_canonical"
    flags.append(
        {
            "series": series_code,
            "flag": "same_date_conflict_resolved",
            "detail": f"{len(values)} conflicting values collapsed by {rule}",
        }
    )
    return resolved


def _parse_snapshot(
    snapshot: Sequence[dict[str, str]],
    parameters: dict[str, Any],
    flags: list[dict[str, str]],
) -> dict[str, list[_Obs]]:
    grouped: dict[str, dict[date, list[Decimal]]] = {}
    for entry in snapshot:
        series_code = str(entry["series_code"])
        resolve_treatment(series_code, parameters)  # refuse undeclared series here
        as_of = date.fromisoformat(str(entry["as_of_date"]))
        grouped.setdefault(series_code, {}).setdefault(as_of, []).append(
            Decimal(str(entry["value"]))
        )
    series: dict[str, list[_Obs]] = {}
    for series_code in sorted(grouped):
        treatment = resolve_treatment(series_code, parameters)
        observations = [
            _Obs(as_of=day, value=_collapse_same_date(series_code, treatment, values, flags))
            for day, values in sorted(grouped[series_code].items())
        ]
        series[series_code] = observations
    return series


# ---------------------------------------------------------------------------
# Instruments (step 3 normalization + step 4 admission).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BillInput:
    tenor_days: int
    discount_pct: float
    as_of: date
    stale: bool

    @property
    def yield_pct(self) -> float:
        return discount_to_yield(self.discount_pct / 100.0, self.tenor_days) * 100.0


@dataclass(frozen=True)
class _BondInput:
    series_code: str
    maturity: date
    coupon_rate: float
    clean_price: float
    frequency: int
    as_of: date
    stale: bool


def _bill_inputs(ctx: _Ctx) -> list[_BillInput]:
    bills: list[_BillInput] = []
    for tenor_days in TBILL_TENOR_DAYS:
        code = f"GHS.TBILL.{tenor_days}.DISCOUNT"
        latest = ctx.latest(code)
        if latest is None:
            ctx.flag(code, "missing_observation", "no current-generation observation on file")
            continue
        bills.append(
            _BillInput(
                tenor_days=tenor_days,
                discount_pct=latest.value,
                as_of=latest.as_of,
                stale=ctx.is_stale(code, latest),
            )
        )
    return bills


def _parse_bond_code(series_code: str) -> tuple[date, float]:
    """``GHS.GOG.BOND.<YYYYMMDD>.<COUPONBPS>.CLEAN`` -> (maturity, coupon)."""
    parts = series_code[len(BOND_PREFIX) :].split(".")
    grammar = f"{BOND_PREFIX}<YYYYMMDD>.<COUPONBPS>.CLEAN"
    if len(parts) != 3 or parts[2] != "CLEAN":
        raise CalculationError(f"bond series {series_code!r} does not follow {grammar}")
    try:
        maturity = date(int(parts[0][0:4]), int(parts[0][4:6]), int(parts[0][6:8]))
        coupon_bps = int(parts[1])
    except (ValueError, IndexError) as exc:
        raise CalculationError(
            f"bond series {series_code!r} does not follow {grammar}"
        ) from exc
    return maturity, coupon_bps / 10_000.0


def _bond_inputs(ctx: _Ctx) -> list[_BondInput]:
    bonds: list[_BondInput] = []
    for series_code in sorted(ctx.series):
        if not series_code.startswith(BOND_PREFIX):
            continue
        latest = ctx.latest(series_code)
        if latest is None:  # pragma: no cover - parsed series always non-empty
            continue
        maturity, coupon = _parse_bond_code(series_code)
        if maturity <= ctx.cob:
            ctx.flag(series_code, "bond_matured", "maturity on or before the COB date")
            continue
        treatment = ctx.treatment(series_code)
        bonds.append(
            _BondInput(
                series_code=series_code,
                maturity=maturity,
                coupon_rate=coupon,
                clean_price=latest.value,
                frequency=int(treatment.get("coupon_frequency", 2)),
                as_of=latest.as_of,
                stale=ctx.is_stale(series_code, latest),
            )
        )
    return _admit_bonds(ctx, bonds)


def _admit_bonds(ctx: _Ctx, bonds: list[_BondInput]) -> list[_BondInput]:
    """Admission: unique maturities (deterministic keep-first by series code),
    strictly beyond the bill span — the bootstrap's structural requirements,
    enforced with flags instead of hard failures."""
    last_bill_time = max(
        ((ctx.cob + timedelta(days=t)) for t in TBILL_TENOR_DAYS), default=ctx.cob
    )
    admitted: dict[date, _BondInput] = {}
    for bond in sorted(bonds, key=lambda b: (b.maturity, b.series_code)):
        if bond.maturity <= last_bill_time:
            ctx.flag(bond.series_code, "bond_inside_bill_span", "does not extend the curve")
            continue
        if bond.maturity in admitted:
            ctx.flag(bond.series_code, "duplicate_maturity_dropped", "keep-first by series code")
            continue
        admitted[bond.maturity] = bond
    return [admitted[maturity] for maturity in sorted(admitted)]


# ---------------------------------------------------------------------------
# Sovereign curve (steps 4-6): bootstrap, fallback, extrapolation, QA gate.
# ---------------------------------------------------------------------------


@dataclass
class _SovereignBuild:
    curve: ZeroCurve | None
    qa: ForwardQaResult | None
    nss_used: bool
    error: str | None
    bill_inputs: list[_BillInput]
    bond_inputs: list[_BondInput]


def _qa_grid(parameters: dict[str, Any]) -> tuple[float, ...]:
    grid = parameters.get("forward_qa_grid", {"start_y": 0.1, "stop_y": 10.0, "step_y": 0.1})
    start, stop, step = float(grid["start_y"]), float(grid["stop_y"]), float(grid["step_y"])
    count = int(round((stop - start) / step)) + 1
    return tuple(round(start + index * step, 10) for index in range(count))


def _duplicate_policy(parameters: dict[str, Any]) -> str:
    scheme = str(parameters.get("weighting_scheme", "volume_weighted_mean"))
    return "last_wins" if scheme == "last_wins" else "volume_weight"


def _nss_smooth(curve: ZeroCurve, parameters: dict[str, Any]) -> ZeroCurve:
    """The parametric fallback: fit NSS to the bootstrap curve on an enriched
    grid and republish the smoothed zeros as the curve nodes (the spec's
    robust-to-sparse-data trade: parametric stability over point accuracy)."""
    grid = sorted({*curve.times, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0})
    fit = fit_nss(grid, [curve.zero(t) for t in grid])
    zeros = tuple(float(nss_zero(t, *fit.parameters.as_tuple())) for t in grid)
    return ZeroCurve(
        valuation_date=curve.valuation_date,
        day_count=curve.day_count,
        times=tuple(grid),
        zeros=zeros,
        interpolation=str(parameters.get("interpolation_method", "monotone_convex")),
        extrapolation="flat_forward"
        if parameters.get("extrapolation_rule", "flat_forward") == "flat_forward"
        else "flat_zero",
    )


def _build_sovereign(ctx: _Ctx) -> _SovereignBuild:
    bills = _bill_inputs(ctx)
    bonds = _bond_inputs(ctx)
    if not bills and not bonds:
        raise CalculationError(
            "no liquid instruments: at least one T-bill discount observation is "
            "required to build the sovereign curve."
        )
    bill_quotes = [
        BillQuote(
            settlement=ctx.cob,
            maturity=ctx.cob + timedelta(days=bill.tenor_days),
            discount_rate=bill.discount_pct / 100.0,
        )
        for bill in bills
    ]
    bond_quotes = [
        BondQuote(
            settlement=ctx.cob,
            maturity=bond.maturity,
            coupon_rate=bond.coupon_rate,
            frequency=bond.frequency,
            price=bond.clean_price,
            day_count=DayCount.ACT_365F,
        )
        for bond in bonds
    ]
    interpolation = str(ctx.params.get("interpolation_method", "monotone_convex"))
    extrapolation = (
        "flat_forward"
        if ctx.params.get("extrapolation_rule", "flat_forward") == "flat_forward"
        else "flat_zero"
    )
    try:
        curve = bootstrap_zero_curve(
            bill_quotes,
            bond_quotes,
            interpolation=interpolation,
            extrapolation=extrapolation,  # type: ignore[arg-type]
            curve_day_count=DayCount.ACT_364,
            duplicate_policy=_duplicate_policy(ctx.params),  # type: ignore[arg-type]
        )
    except BootstrapError as exc:
        ctx.flag(AGS_CODE, "curve_build_failed", str(exc))
        return _SovereignBuild(None, None, False, str(exc), bills, bonds)
    liquid_points = len(bill_quotes) + len(bond_quotes)
    nss_used = liquid_points < int(ctx.params.get("nss_fallback_min_liquid_points", 3))
    if nss_used:
        ctx.flag(AGS_CODE, "nss_fallback_used", f"{liquid_points} liquid points")
        curve = _nss_smooth(curve, ctx.params)
    qa = qa_forwards(
        curve,
        _qa_grid(ctx.params),
        positivity=bool(ctx.params.get("enforce_positive_forwards", True)),
        oscillation_tolerance=float(ctx.params.get("oscillation_tolerance", 3.0)),
    )
    if not qa.passed:
        ctx.flag(AGS_CODE, "forward_qa_failed", "hard pre-publish gate (spec §5 step 6)")
    return _SovereignBuild(curve, qa, nss_used, None, bills, bonds)


# ---------------------------------------------------------------------------
# AGD — the synthetic discounting curve (step 7).
# ---------------------------------------------------------------------------


@dataclass
class _AgdBuild:
    step: MeetingDateStepCurve | None
    diagnostics: dict[str, Any]
    error: str | None


def _meeting_dates(parameters: dict[str, Any]) -> tuple[date, ...]:
    entries = parameters.get("mpc_meeting_dates", [])
    return tuple(sorted(date.fromisoformat(str(entry["date"])) for entry in entries))


def _overnight_window(ctx: _Ctx) -> tuple[list[tuple[_Obs, float]], int]:
    """The cleaned trailing window of (interbank observation, spread-to-MPR)
    pairs: last ``overnight_spread_window_bdays`` prints, outlier-filtered by
    the methodology's z-bound."""
    window_size = int(ctx.params.get("overnight_spread_window_bdays", 20))
    eligible = [obs for obs in ctx.series.get(INTERBANK_SERIES, []) if obs.as_of <= ctx.cob]
    window = eligible[-window_size:]
    dropped = 0
    if len(window) >= 3:
        mean = statistics.mean(observation.value for observation in window)
        std = statistics.pstdev(observation.value for observation in window)
        bound = float(ctx.params.get("outlier_zscore_bound", 4.0))
        if std > 0.0:
            kept = [obs for obs in window if abs(obs.value - mean) / std <= bound]
            dropped = len(window) - len(kept)
            if dropped:
                ctx.flag(INTERBANK_SERIES, "outliers_dropped", f"{dropped} beyond z={bound}")
            window = kept
    pairs: list[tuple[_Obs, float]] = []
    for observation in window:
        mpr = ctx.prevailing(MPR_SERIES, observation.as_of)
        if mpr is None:
            ctx.flag(MPR_SERIES, "no_prevailing_mpr", observation.as_of.isoformat())
            continue
        pairs.append((observation, observation.value - mpr.value))
    if len(pairs) < window_size:
        ctx.flag(INTERBANK_SERIES, "overnight_window_thin", f"{len(pairs)}/{window_size}")
    return pairs, dropped


def _build_agd(ctx: _Ctx) -> _AgdBuild:
    latest_mpr = ctx.latest(MPR_SERIES)
    if latest_mpr is None:
        raise CalculationError("GHS.MPR is required: the AGD short end anchors on the MPR.")
    pairs, dropped = _overnight_window(ctx)
    if not pairs:
        raise CalculationError(
            "no usable GHS.INTERBANK.ON observations in the overnight spread window; "
            "the AGD short-end anchor cannot be computed."
        )
    spreads = [spread for _, spread in pairs]
    mean_spread_pp = statistics.mean(spreads)
    std_spread_pp = statistics.pstdev(spreads) if len(spreads) > 1 else 0.0
    breach_share = sum(1 for spread in spreads if abs(spread) > 1.0) / len(spreads)
    if breach_share > 0.0:
        ctx.flag(
            INTERBANK_SERIES,
            "interbank_outside_policy_corridor",
            f"{breach_share:.0%} of window prints beyond ±100bp of the MPR (disclosed)",
        )
    moves = ctx.params.get("expected_policy_moves_bps")
    meeting_dates = _meeting_dates(ctx.params)
    step = MeetingDateStepCurve(
        anchor_date=ctx.cob,
        mpr=latest_mpr.value / 100.0,
        meeting_dates=meeting_dates,
        spread_bps=mean_spread_pp * 100.0,
        expected_moves_bps=tuple(float(m) for m in moves) if moves else None,
    )
    diagnostics = {
        "window_bdays": int(ctx.params.get("overnight_spread_window_bdays", 20)),
        "observations_used": len(pairs),
        "outliers_dropped": dropped,
        "mean_spread_pp": _fmt(mean_spread_pp),
        "std_spread_pp": _fmt(std_spread_pp),
        "corridor_breach_share": _fmt(breach_share, 4),
        "mpr_pct": _fmt(latest_mpr.value),
        "anchor_rate_pct": _fmt(latest_mpr.value + mean_spread_pp),
    }
    return _AgdBuild(step=step, diagnostics=diagnostics, error=None)


def _explicit_basis(parameters: dict[str, Any]) -> list[tuple[float, float]] | None:
    basis_cfg = parameters.get("discount_basis", {})
    explicit = basis_cfg.get("explicit_bps_by_tenor_y")
    if not explicit:
        return None
    return sorted((float(tenor), float(bps)) for tenor, bps in explicit.items())


def _basis_bps_at(
    t: float,
    t0: float,
    basis0_bps: float,
    taper_y: float,
    explicit: list[tuple[float, float]] | None,
) -> float:
    if explicit is not None:
        return _piecewise_linear(explicit, t)
    if t <= t0 or taper_y <= t0:
        return basis0_bps
    return basis0_bps * max(0.0, (taper_y - t) / (taper_y - t0))


def _piecewise_linear(points: list[tuple[float, float]], t: float) -> float:
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= t <= x1:
            return y0 + (y1 - y0) * (t - x0) / (x1 - x0)
    return points[-1][1]  # pragma: no cover - covered by the boundary checks


def _agd_zero_nodes(
    ctx: _Ctx, agd: _AgdBuild, sovereign: ZeroCurve
) -> tuple[list[tuple[int, float]], dict[str, Any]]:
    """AGD node zeros (continuously compounded, ACT/364) on the month grid:
    the step curve inside the shortest liquid sovereign tenor, sovereign +
    disclosed basis beyond it."""
    assert agd.step is not None
    t0 = sovereign.times[0]
    days0 = round(t0 * _ACT364)
    z_step0 = -math.log(agd.step.df(ctx.cob + timedelta(days=days0))) / t0
    basis0_bps = (z_step0 - sovereign.zero(t0)) * 10_000.0
    basis_cfg = ctx.params.get("discount_basis", {})
    taper_y = float(basis_cfg.get("basis_taper_tenor_y", 5.0))
    explicit = _explicit_basis(ctx.params)
    nodes: list[tuple[int, float]] = []
    for months in ctx.params.get("agd_node_grid_months", [1, 3, 6, 12, 24, 36, 60, 84, 120]):
        days = round(int(months) * _ACT364 / _MONTHS_PER_YEAR)
        t = days / _ACT364
        if t <= t0 + 1e-12:
            zero = -math.log(agd.step.df(ctx.cob + timedelta(days=days))) / t
        else:
            zero = sovereign.zero(t) + _basis_bps_at(t, t0, basis0_bps, taper_y, explicit) / 1e4
        nodes.append((int(months), zero))
    basis_note = {
        "mode": "explicit_bps_by_tenor_y" if explicit else str(basis_cfg.get("mode", "")),
        "short_anchor_tenor_y": _fmt(t0),
        "observed_basis0_bps": _fmt(basis0_bps, 4),
        "basis_taper_tenor_y": _fmt(taper_y, 2),
        "disclosed_assumption": (
            "short basis = step-curve-implied short zero minus sovereign short zero "
            "at the shortest liquid tenor, tapering linearly to zero by the taper tenor"
        ),
    }
    return nodes, basis_note


# ---------------------------------------------------------------------------
# Curve block assembly (step 9 shape, adapter-conformant).
# ---------------------------------------------------------------------------


def _curve_points(nodes: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Adapter contract: ``points`` = [{tenor_months, rate_pct}] with unique
    integer months; rates are percent, stringified."""
    points: list[dict[str, Any]] = []
    seen: set[int] = set()
    for months, zero in nodes:
        if months in seen or months < 1:
            continue
        seen.add(months)
        points.append({"tenor_months": months, "rate_pct": _fmt(zero * 100.0)})
    return points


def _curve_block(  # noqa: PLR0913 - definition, nodes, QA and lineage are one unit
    definition: CurveDefinition,
    nodes: list[tuple[float, float]],
    month_nodes: list[tuple[int, float]],
    qa: ForwardQaResult | None,
    raw_inputs: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    build = CurveBuildResult.create(
        definition,
        CurveNodes(
            tenor_years=tuple(t for t, _ in nodes), values=tuple(v for _, v in nodes)
        ),
        qa,
        raw_inputs,
    )
    block: dict[str, Any] = {
        "curve_type": definition.curve_kind,
        "points": _curve_points(month_nodes),
        "nodes": [
            {"tenor_years": _fmt(t), "value_pct": _fmt(v * 100.0)} for t, v in nodes
        ],
        "definition": definition.as_payload(),
        "digest": build.input_digest,
    }
    if extra:
        block.update(extra)
    return block


def _sovereign_raw_inputs(ctx: _Ctx, sovereign: _SovereignBuild) -> dict[str, Any]:
    return {
        "as_of": ctx.cob.isoformat(),
        "bills": [
            {
                "tenor_days": bill.tenor_days,
                "discount_pct": _fmt(bill.discount_pct),
                "as_of": bill.as_of.isoformat(),
            }
            for bill in sovereign.bill_inputs
        ],
        "bonds": [
            {
                "series": bond.series_code,
                "maturity": bond.maturity.isoformat(),
                "coupon_rate": _fmt(bond.coupon_rate),
                "clean_price": _fmt(bond.clean_price, 4),
                "as_of": bond.as_of.isoformat(),
            }
            for bond in sovereign.bond_inputs
        ],
        "interpolation": str(ctx.params.get("interpolation_method", "monotone_convex")),
        "extrapolation": str(ctx.params.get("extrapolation_rule", "flat_forward")),
        "nss_fallback_used": sovereign.nss_used,
    }


def _sovereign_blocks(ctx: _Ctx, sovereign: _SovereignBuild) -> dict[str, dict[str, Any]]:
    raw_inputs = _sovereign_raw_inputs(ctx, sovereign)
    selection = (
        "min_trade_count",
        "max_staleness_days",
        "outlier_zscore_bound",
        "nss_fallback_min_liquid_points",
    )
    interpolation = str(ctx.params.get("interpolation_method", "monotone_convex"))
    if sovereign.nss_used:
        interpolation = f"nss_fallback+{interpolation}"
    extrapolation = str(ctx.params.get("extrapolation_rule", "flat_forward"))
    if sovereign.curve is None:
        error = {"build_error": sovereign.error or "curve build failed", "points": []}
        return {
            AGS_CODE: {"curve_type": "zero", **error},
            FWD_CODE: {"curve_type": "forward", **error},
        }
    curve = sovereign.curve
    zero_definition = CurveDefinition(
        curve_code=AGS_CODE,
        curve_kind="zero",
        interpolation=interpolation,
        day_count=DayCount.ACT_364,
        extrapolation=extrapolation,
        instrument_selection=selection,
    )
    zero_nodes = list(zip(curve.times, curve.zeros, strict=True))
    zero_months = [(round(t * _MONTHS_PER_YEAR), z) for t, z in zero_nodes]
    forward_definition = CurveDefinition(
        curve_code=FWD_CODE,
        curve_kind="forward",
        interpolation=interpolation,
        day_count=DayCount.ACT_364,
        extrapolation=extrapolation,
        instrument_selection=selection,
    )
    forward_grid = [
        int(m) for m in ctx.params.get("fwd_node_grid_months", [3, 6, 12, 24, 36, 60, 84, 120])
    ]
    forward_nodes = [
        (months / _MONTHS_PER_YEAR, curve.instantaneous_forward(months / _MONTHS_PER_YEAR))
        for months in forward_grid
    ]
    forward_months = list(zip(forward_grid, (v for _, v in forward_nodes), strict=True))
    return {
        AGS_CODE: _curve_block(zero_definition, zero_nodes, zero_months, sovereign.qa, raw_inputs),
        FWD_CODE: _curve_block(
            forward_definition, forward_nodes, forward_months, sovereign.qa, raw_inputs
        ),
    }


def _agd_block(
    ctx: _Ctx, agd: _AgdBuild, sovereign: _SovereignBuild
) -> dict[str, Any]:
    if sovereign.curve is None:
        return {
            "curve_type": "discount",
            "points": [],
            "build_error": "sovereign curve unavailable — the AGD long end needs it",
        }
    nodes, basis_note = _agd_zero_nodes(ctx, agd, sovereign.curve)
    definition = CurveDefinition(
        curve_code=AGD_CODE,
        curve_kind="discount",
        interpolation="meeting_date_step+sovereign_basis",
        day_count=DayCount.ACT_364,
        extrapolation=str(ctx.params.get("extrapolation_rule", "flat_forward")),
        instrument_selection=("overnight_spread_window_bdays", "discount_basis"),
    )
    raw_inputs = {
        "as_of": ctx.cob.isoformat(),
        "mpr_pct": agd.diagnostics["mpr_pct"],
        "mean_spread_pp": agd.diagnostics["mean_spread_pp"],
        "meeting_dates": [day.isoformat() for day in _meeting_dates(ctx.params)],
        "basis": basis_note,
        "grid_months": [
            int(m)
            for m in ctx.params.get("agd_node_grid_months", [1, 3, 6, 12, 24, 36, 60, 84, 120])
        ],
        "sovereign_inputs": _sovereign_raw_inputs(ctx, sovereign),
    }
    year_nodes = [(months / _MONTHS_PER_YEAR, zero) for months, zero in nodes]
    extra = {
        "quote_basis": "continuously_compounded_zero_act364",
        "overnight_anchor_pct": agd.diagnostics["anchor_rate_pct"],
        "basis": basis_note,
        "disclosure": (
            "synthetic discounting proxy, not a traded OIS curve: meeting-date step "
            "short end anchored at MPR + rolling interbank spread; long end = "
            "sovereign zero + disclosed basis (spec §6.2, §8 truthful naming)"
        ),
    }
    return _curve_block(definition, year_nodes, nodes, None, raw_inputs, extra)


# ---------------------------------------------------------------------------
# Derived rates (step 8).
# ---------------------------------------------------------------------------


def _rate_entry(  # noqa: PLR0913 - a published rate's provenance is irreducibly wide
    value_pct: float,
    *,
    treatment: str,
    unit: str,
    sources: list[str],
    as_of: date,
    stale: bool,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "value": _fmt(value_pct),
        "unit": unit,
        "treatment": treatment,
        "source_series": sources,
        "as_of": as_of.isoformat(),
        "staleness_flag": stale,
    }
    if detail:
        entry["detail"] = detail
    return entry


def _mpr_rate(ctx: _Ctx, rates: dict[str, Any]) -> None:
    latest = ctx.latest(MPR_SERIES)
    assert latest is not None  # required earlier by _build_agd
    meeting_dates = set(_meeting_dates(ctx.params))
    if meeting_dates and latest.as_of >= min(meeting_dates) and latest.as_of not in meeting_dates:
        ctx.flag(
            MPR_SERIES,
            "mpr_change_off_meeting_date",
            f"{latest.as_of.isoformat()} is not a scheduled MPC decision date",
        )
    rates[MPR_SERIES] = _rate_entry(
        latest.value,
        treatment="pass_through",
        unit="pct",
        sources=[MPR_SERIES],
        as_of=latest.as_of,
        stale=ctx.is_stale(MPR_SERIES, latest),
    )


def _interbank_rate(ctx: _Ctx, rates: dict[str, Any]) -> None:
    latest = ctx.latest(INTERBANK_SERIES)
    assert latest is not None  # required earlier by _build_agd
    rates[INTERBANK_SERIES] = _rate_entry(
        latest.value,
        treatment="windowed",
        unit="pct",
        sources=[INTERBANK_SERIES],
        as_of=latest.as_of,
        stale=ctx.is_stale(INTERBANK_SERIES, latest),
    )


def _tbill_rates(ctx: _Ctx, sovereign: _SovereignBuild, rates: dict[str, Any]) -> None:
    """Derived T-bill true yields from discount rates (ACT/364, spec §5 step
    3); a same-date observed yield is a reconstruction cross-check only."""
    for bill in sovereign.bill_inputs:
        yield_code = f"GHS.TBILL.{bill.tenor_days}.YIELD"
        derived_pct = bill.yield_pct
        observed = ctx.latest(yield_code)
        detail: dict[str, Any] = {"convention": "ACT/364 discount->yield"}
        if observed is not None and observed.as_of == bill.as_of:
            gap = abs(observed.value - derived_pct)
            detail["observed_pct"] = _fmt(observed.value)
            if gap > _TBILL_RECON_TOLERANCE_PP:
                ctx.flag(
                    yield_code,
                    "tbill_yield_reconstruction_mismatch",
                    f"derived {derived_pct:.4f} vs observed {observed.value:.4f}",
                )
        rates[yield_code] = _rate_entry(
            derived_pct,
            treatment="derived",
            unit="pct",
            sources=[f"GHS.TBILL.{bill.tenor_days}.DISCOUNT"],
            as_of=bill.as_of,
            stale=bill.stale,
            detail=detail,
        )


def _grr_check(ctx: _Ctx) -> dict[str, Any]:
    """The three-input GRR reconstruction gate: |published - reconstructed|
    beyond the tolerance raises a steward flag — never a silent block."""
    published = ctx.latest(GRR_SERIES)
    if published is None:
        return {"status": "no_observation"}
    month_end = published.as_of.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)
    month_prints = [
        obs
        for obs in ctx.series.get(INTERBANK_SERIES, [])
        if month_start <= obs.as_of <= month_end
    ]
    mpr = ctx.prevailing(MPR_SERIES, month_end)
    discount = ctx.prevailing("GHS.TBILL.91.DISCOUNT", month_end)
    base = {
        "reference_month": month_start.strftime("%Y-%m"),
        "published_pct": _fmt(published.value),
        "tolerance_pp": _fmt(float(ctx.params.get("grr_check_tolerance_pp", 1.0)), 2),
    }
    if not month_prints or mpr is None or discount is None:
        return {**base, "status": "insufficient_inputs"}
    monthly_avg = statistics.mean(obs.value for obs in month_prints)
    tbill_yield = discount_to_yield(discount.value / 100.0, 91) * 100.0
    formula = ctx.params.get("grr_formula", {})
    weights = [float(w) for w in formula.get("weights", [1 / 3, 1 / 3, 1 / 3])]
    reconstructed = (
        weights[0] * mpr.value + weights[1] * monthly_avg + weights[2] * tbill_yield
    )
    gap = published.value - reconstructed
    tolerance = float(ctx.params.get("grr_check_tolerance_pp", 1.0))
    verdict = "pass" if abs(gap) <= tolerance else "mismatch_flagged"
    if verdict == "mismatch_flagged":
        ctx.flag(
            GRR_SERIES,
            "grr_reconstruction_mismatch",
            f"published {published.value:.2f} vs reconstructed {reconstructed:.2f} "
            "— for steward review, not a block",
        )
    return {
        **base,
        "status": verdict,
        "reconstructed_pct": _fmt(reconstructed),
        "gap_pp": _fmt(gap),
        "inputs": {
            "mpr_pct": _fmt(mpr.value),
            "monthly_avg_interbank_pct": _fmt(monthly_avg),
            "tbill_91_yield_pct": _fmt(tbill_yield),
            "interbank_prints": len(month_prints),
        },
    }


def _grr_rates(ctx: _Ctx, grr_check: dict[str, Any], rates: dict[str, Any]) -> None:
    published = ctx.latest(GRR_SERIES)
    if published is None:
        ctx.flag(GRR_SERIES, "missing_observation", "no GRR on file — rate omitted")
        return
    stale = ctx.is_stale(GRR_SERIES, published)
    rates[GRR_SERIES] = _rate_entry(
        published.value,
        treatment="pass_through",
        unit="pct",
        sources=[GRR_SERIES],
        as_of=published.as_of,
        stale=stale,
        detail={"cross_check": grr_check.get("status", "no_observation")},
    )
    rates[GRR_BASE_SERIES] = _rate_entry(
        published.value,
        treatment="derived",
        unit="pct",
        sources=[GRR_SERIES],
        as_of=published.as_of,
        stale=stale,
        detail={
            "method": "grr_pass_through_with_decomposition",
            "decomposition": grr_check,
            "note": (
                "GRR-consistent lending base = the published GRR passed through; the "
                "three-input decomposition is disclosed alongside for transparency"
            ),
        },
    )


def _lending_indicator(ctx: _Ctx, rates: dict[str, Any]) -> None:
    """Median + 20%-trimmed mean across the latest per-bank APR prints."""
    values: list[float] = []
    sources: list[str] = []
    latest_as_of: date | None = None
    any_stale = False
    for series_code in sorted(ctx.series):
        if not series_code.startswith(APR_PREFIX):
            continue
        latest = ctx.latest(series_code)
        if latest is None:  # pragma: no cover - parsed series always non-empty
            continue
        any_stale = ctx.is_stale(series_code, latest) or any_stale
        values.append(latest.value)
        sources.append(series_code)
        latest_as_of = latest.as_of if latest_as_of is None else max(latest_as_of, latest.as_of)
    if not values or latest_as_of is None:
        return
    ordered = sorted(values)
    treatment = resolve_treatment(LENDING_INDICATOR_SERIES, ctx.params)
    trim_fraction = float(treatment.get("trim_fraction", 0.20))
    trim = int(len(ordered) * trim_fraction)
    trimmed = ordered[trim : len(ordered) - trim] if len(ordered) > 2 * trim else ordered
    rates[LENDING_INDICATOR_SERIES] = _rate_entry(
        statistics.median(ordered),
        treatment="derived",
        unit="pct",
        sources=sources,
        as_of=latest_as_of,
        stale=any_stale,
        detail={
            "method": "median_with_trimmed_mean",
            "n_banks": len(ordered),
            "median_pct": _fmt(statistics.median(ordered)),
            "trimmed_mean_pct": _fmt(statistics.mean(trimmed)),
            "trim_fraction": _fmt(trim_fraction, 2),
        },
    )


def _fx_section(ctx: _Ctx) -> tuple[dict[str, Any], dict[str, str]]:
    """Rich FX detail plus the flat adapter map. The BoG weighted-median
    reference rate is the published anchor when present; the interbank mid is
    the fallback."""
    detail: dict[str, Any] = {}
    flat: dict[str, str] = {}
    mid = ctx.latest(USDGHS_MID_SERIES)
    ref = ctx.latest(USDGHS_REF_SERIES)
    pair: dict[str, Any] = {}
    if mid is not None:
        pair["interbank_mid"] = {
            "value": _fmt(mid.value, 4),
            "as_of": mid.as_of.isoformat(),
            "treatment": "pass_through",
            "source_series": USDGHS_MID_SERIES,
            "staleness_flag": ctx.is_stale(USDGHS_MID_SERIES, mid),
        }
    if ref is not None:
        pair["reference"] = {
            "value": _fmt(ref.value, 4),
            "as_of": ref.as_of.isoformat(),
            "treatment": "pass_through",
            "source_series": USDGHS_REF_SERIES,
            "staleness_flag": ctx.is_stale(USDGHS_REF_SERIES, ref),
            "note": "BoG weighted-median reference rate (BOG/FMD/2024/65)",
        }
    anchor = ref or mid
    if pair and anchor is not None:
        pair["published"] = "reference" if ref is not None else "interbank_mid"
        detail["USD/GHS"] = pair
        flat["USD/GHS"] = _fmt(anchor.value, 4)
    return detail, flat


# ---------------------------------------------------------------------------
# Cointegration — weekly diagnostic ONLY (never a published level in v1).
# ---------------------------------------------------------------------------


def _eg_pairs(ctx: _Ctx) -> tuple[list[float], list[float]]:
    """Auction-date alignment (the calibration alignment): for each 91-day
    tender in the trailing window, the freshest interbank print at most
    ``_EG_PAIR_MAX_GAP_DAYS`` old."""
    y: list[float] = []
    x: list[float] = []
    for auction in ctx.series.get("GHS.TBILL.91.DISCOUNT", []):
        interbank = ctx.prevailing(INTERBANK_SERIES, auction.as_of)
        if interbank is None or (auction.as_of - interbank.as_of).days > _EG_PAIR_MAX_GAP_DAYS:
            continue
        y.append(discount_to_yield(auction.value / 100.0, 91) * 100.0)
        x.append(interbank.value)
    return y, x


def _cointegration_diagnostic(ctx: _Ctx) -> dict[str, Any]:
    config = ctx.params.get("cointegration", {})
    base: dict[str, Any] = {
        "role": "diagnostic",
        "significance_disclosed": True,
        "window_bdays": int(config.get("diagnostic_window_bdays", 250)),
        "note": (
            "diagnostic only — never sets a published level in v1; promotion to "
            "level-setter is a documented future Track-2 event"
        ),
    }
    y, x = _eg_pairs(ctx)
    if len(y) < _MIN_EG_PAIRS:
        return {**base, "status": "insufficient_observations", "n_pairs": len(y)}
    try:
        result = engle_granger(y, x, maxlag=int(config.get("adf_maxlag", 8)))
    except CointegrationError as exc:
        return {**base, "status": "error", "detail": str(exc), "n_pairs": len(y)}
    verdict = (
        "cointegrated_at_5pct" if result.is_cointegrated("5%") else "not_cointegrated_at_5pct"
    )
    return {
        **base,
        "status": "computed",
        "n_pairs": len(y),
        "alpha": _fmt(result.alpha, 4),
        "beta": _fmt(result.beta, 4),
        "adf_stat": _fmt(result.adf_stat, 4),
        "adf_lags": result.adf_lags,
        "adf_nobs": result.adf_nobs,
        "critical_values": {level: _fmt(value, 2) for level, value in ADF_CRITICAL_VALUES.items()},
        "residual_std_pp": _fmt(statistics.pstdev(result.residuals), 4),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# The pure pipeline (spec §5 steps 1-9).
# ---------------------------------------------------------------------------


def run_pipeline(
    snapshot: Sequence[dict[str, str]],
    parameters: dict[str, Any],
    cob_date: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Snapshot x parameters -> (derived_values, qa_results). Pure.

    QA-gate failures are recorded, never raised: the returned
    ``derived_values`` still documents the attempt and carries
    ``qa_passed=False``, which the operator approve path refuses.
    """
    if not snapshot:
        raise CalculationError("the determination's input snapshot is empty.")
    flags: list[dict[str, str]] = []
    ctx = _Ctx(
        cob=cob_date,
        params=parameters,
        series=_parse_snapshot(snapshot, parameters, flags),
        flags=flags,
    )
    sovereign = _build_sovereign(ctx)
    agd = _build_agd(ctx)
    curves = _sovereign_blocks(ctx, sovereign)
    curves[AGD_CODE] = _agd_block(ctx, agd, sovereign)

    rates: dict[str, Any] = {}
    _mpr_rate(ctx, rates)
    _interbank_rate(ctx, rates)
    _tbill_rates(ctx, sovereign, rates)
    grr_check = _grr_check(ctx)
    _grr_rates(ctx, grr_check, rates)
    _lending_indicator(ctx, rates)
    fx_detail, fx_flat = _fx_section(ctx)

    forward_gate_passed = sovereign.qa is not None and sovereign.qa.passed
    qa_passed = sovereign.error is None and forward_gate_passed

    derived_values: dict[str, Any] = {
        "qa_passed": qa_passed,
        "curves": curves,
        "rates": rates,
        # Flat adapter-contract sections (aequor_desk build_extraction shape).
        "reference_rates": {code: entry["value"] for code, entry in rates.items()},
        "fx": fx_detail,
        "fx_rates": fx_flat,
    }
    qa_results: dict[str, Any] = {
        "qa_passed": qa_passed,
        "gates": {
            "curve_build": "pass" if sovereign.error is None else "fail",
            "forward_qa": "pass" if forward_gate_passed else "fail",
        },
        "forward_qa": _forward_qa_payload(sovereign.qa),
        "nss_fallback_used": sovereign.nss_used,
        "overnight_spread": agd.diagnostics,
        "cointegration_diagnostic": _cointegration_diagnostic(ctx),
        "grr_check": grr_check,
        "flags": ctx.flags,
    }
    return derived_values, qa_results


def _forward_qa_payload(qa: ForwardQaResult | None) -> dict[str, Any] | None:
    if qa is None:
        return None
    return {
        "min_forward": _fmt(qa.min_forward),
        "positivity_required": qa.positivity_required,
        "positivity_pass": qa.positivity_pass,
        "slope_sign_changes": qa.slope_sign_changes,
        "total_variation_ratio": _fmt(qa.total_variation_ratio),
        "oscillation_tolerance": _fmt(qa.oscillation_tolerance),
        "oscillation_pass": qa.oscillation_pass,
        "passed": qa.passed,
    }


# ---------------------------------------------------------------------------
# Snapshot finalization (the only DB-reading step; spec §5 step 1 capture).
# ---------------------------------------------------------------------------


def _history_lookback_days(parameters: dict[str, Any]) -> int:
    window_bdays = int(
        parameters.get("cointegration", {}).get("diagnostic_window_bdays", 250)
    )
    return math.ceil(window_bdays * 7 / 5) + 14


def _rows_between(
    db: Session, series_code: str, start: date, end: date
) -> list[DeskObservation]:
    return list(
        db.scalars(
            select(DeskObservation)
            .where(
                DeskObservation.series_code == series_code,
                DeskObservation.as_of_date >= start,
                DeskObservation.as_of_date <= end,
                DeskObservation.superseded_by.is_(None),
            )
            .order_by(DeskObservation.as_of_date)
        )
    )


def _latest_row(db: Session, series_code: str, on_or_before: date) -> DeskObservation | None:
    return db.scalar(
        select(DeskObservation)
        .where(
            DeskObservation.series_code == series_code,
            DeskObservation.as_of_date <= on_or_before,
            DeskObservation.superseded_by.is_(None),
        )
        .order_by(DeskObservation.as_of_date.desc())
        .limit(1)
    )


def _latest_rows_by_pattern(
    db: Session, prefix: str, on_or_before: date
) -> list[DeskObservation]:
    rows = db.scalars(
        select(DeskObservation)
        .where(
            DeskObservation.series_code.like(f"{prefix}%"),
            DeskObservation.as_of_date <= on_or_before,
            DeskObservation.superseded_by.is_(None),
        )
        .order_by(DeskObservation.series_code, DeskObservation.as_of_date)
    )
    latest: dict[str, DeskObservation] = {}
    for row in rows:
        latest[row.series_code] = row  # ordered by date: last write wins
    return [latest[code] for code in sorted(latest)]


def _entry(row: DeskObservation) -> dict[str, str]:
    return {
        "series_code": row.series_code,
        "as_of_date": row.as_of_date.isoformat(),
        "value": str(row.value),
    }


def _grr_month_rows(db: Session, cob_date: date) -> list[DeskObservation]:
    """The GRR cross-check inputs: the reference month's interbank prints plus
    the MPR and 91-day discount prevailing at that month's end."""
    grr = _latest_row(db, GRR_SERIES, cob_date)
    if grr is None:
        return []
    month_end = grr.as_of_date.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)
    rows = _rows_between(db, INTERBANK_SERIES, month_start, month_end)
    for code in (MPR_SERIES, "GHS.TBILL.91.DISCOUNT"):
        row = _latest_row(db, code, month_end)
        if row is not None:
            rows.append(row)
    return rows


def build_calculation_snapshot(
    db: Session, cob_date: date, *, parameters: dict[str, Any]
) -> list[dict[str, str]]:
    """The FULL observation set the pipeline consumes: the default
    point-in-time snapshot plus every windowed history the methodology's
    treatments require. Same entry shape and canonical sort as
    ``determinations.build_input_snapshot`` — value-based, id-free.
    """
    collected: dict[str, dict[str, str]] = {}

    def add(rows: list[DeskObservation]) -> None:
        for row in rows:
            entry = _entry(row)
            collected[_canonical_json(entry)] = entry

    for entry in determinations.build_input_snapshot(db, cob_date):
        collected[_canonical_json(entry)] = entry

    history_start = cob_date - timedelta(days=_history_lookback_days(parameters))
    for code in (INTERBANK_SERIES, "GHS.TBILL.91.DISCOUNT"):
        add(_rows_between(db, code, history_start, cob_date))
    # The MPR path over the window, plus the level prevailing at its start.
    add(_rows_between(db, MPR_SERIES, history_start, cob_date))
    before_window = _latest_row(db, MPR_SERIES, history_start - timedelta(days=1))
    if before_window is not None:
        add([before_window])
    # Latest-only extras beyond the default series.
    for code in (USDGHS_REF_SERIES, "GHS.TBILL.182.YIELD", "GHS.TBILL.364.YIELD"):
        row = _latest_row(db, code, cob_date)
        if row is not None:
            add([row])
    add(_grr_month_rows(db, cob_date))
    add(_latest_rows_by_pattern(db, APR_PREFIX, cob_date))
    add(_latest_rows_by_pattern(db, BOND_PREFIX, cob_date))

    return sorted(collected.values(), key=_canonical_json)


# ---------------------------------------------------------------------------
# Orchestration + the approve-path QA guard.
# ---------------------------------------------------------------------------


def compute_determination(
    db: Session, determination: DeskDetermination, *, methodology: DeskMethodology
) -> None:
    """Run the §5 pipeline on a DRAFT: finalize the input snapshot (windowed
    history included), re-digest it, and attach derived values + QA results.

    The draft's snapshot is REPLACED here — capture (§5 step 1) completes at
    compute time, and the stored snapshot must be exactly what the pipeline
    consumed or the reproducibility invariant is a lie. Drafts only: once
    submitted, snapshot and results are frozen for the checker.
    """
    if determination.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Determination is {determination.status!r}; only a draft can be computed."
            ),
        )
    if (
        methodology.methodology_code != determination.methodology_code
        or methodology.version != determination.methodology_version
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Methodology row does not match the determination's bound "
                f"{determination.methodology_code!r} v{determination.methodology_version}."
            ),
        )
    parameters: dict[str, Any] = methodology.parameters
    snapshot = build_calculation_snapshot(db, determination.cob_date, parameters=parameters)
    derived_values, qa_results = run_pipeline(snapshot, parameters, determination.cob_date)
    determination.input_snapshot = snapshot
    determination.input_digest = determinations.snapshot_digest(snapshot)
    determinations.set_results(
        db, determination.id, derived_values=derived_values, qa_results=qa_results
    )


def ensure_approvable(determination: DeskDetermination) -> None:
    """The API-layer hard-gate guard (spec §5 step 6): a determination whose
    computed results failed a hard QA gate cannot be approved. Enforced here
    because the determinations state machine is deliberately QA-agnostic."""
    derived = determination.derived_values or {}
    if derived.get("qa_passed") is False:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Determination failed a hard QA gate (qa_passed=false); correct the "
                "inputs and recompute before submitting for approval."
            ),
        )
