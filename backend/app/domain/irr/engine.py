"""Pure interest-rate-risk-in-the-banking-book (IRRBB) engine.

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the repricing positions (plus a decomposed
interest-rate-swap hedge), the base zero-coupon discount curve, and the Basel
stress-scenario shocks, and receive fully materialized gap, duration, economic
value of equity (EVE) and earnings-at-risk (EaR) results with per-line items.
Monetary amounts quantize to ``MONEY`` (4 dp), ratio percentages to
``RATIO_PCT`` (6 dp) and durations to ``DURATION`` (4 dp, in years), all with
``ROUND_HALF_UP``; status classification always happens AFTER quantization so
stored and displayed values agree.

Methodology (Product Documentation Module 1):

- **Repricing gap.** Positions are assigned to nine maturity/repricing buckets
  [overnight, 1-7d, 8-30d, 1-3m, 3-6m, 6-12m, 1-3y, 3-5y, 5y+]. The bucket gap
  is rate-sensitive assets minus rate-sensitive liabilities; the cumulative gap
  runs through the buckets in order. The 12-month cumulative gap sums the
  buckets whose midpoint falls on or within twelve months.
- **Present value / duration.** Each position is treated as a single
  zero-coupon claim of face ``amount`` maturing at its bucket ``midpoint``,
  discounted at the base curve zero rate for that midpoint:
  ``PV = amount / (1 + z)^t``. Macaulay duration of the portfolio is the
  PV-weighted average midpoint; modified duration divides by ``(1 + y)`` where
  ``y`` is the PV-weighted average zero rate (annual compounding, ``n = 1``);
  convexity is ``(1/P)·Σ(t²·PV)/(1 + y)²``. The duration gap is
  ``ModDur_assets − (PV_liabilities / PV_assets)·ModDur_liabilities``.
- **EVE.** ``EVE = PV(assets) − PV(liabilities)``. Each of the six Basel IRRBB
  scenarios shifts the curve bucket-wise and re-prices every position;
  ``ΔEVE = EVE_scenario − EVE_base``. The aggregate measure is the largest
  absolute ΔEVE; the limit breaches when any scenario's ``|ΔEVE| / Tier 1``
  exceeds the supervisory limit (15% by default). Scenario shifts (in decimal,
  added to the decimal zero rate):

  - ``parallel_up_200`` / ``parallel_down_200``: constant ``parallel_bp``.
  - ``short_up_250`` / ``short_down_250``: ``short_bp · e^(−t / decay_years)``
    (the short-rate shock decays with tenor; ``decay_years`` is a shock param).
  - ``steepener`` / ``flattener``: ``short_bp · s(t) + long_bp · (1 − s(t))``
    where the short weight ``s(t) = e^(−t / 4)`` follows the standard Basel
    rotational blend.
- **EaR.** ``ΔNII = Σ_i Gap_i · ΔRate · (12 − months_i) / 12`` over the ≤12-month
  buckets, evaluated under the parallel ±200 bp shocks; ``months_i`` is the
  bucket midpoint expressed in months. Decomposed swap legs sit in the gap
  buckets like any other position, so EaR already reprices the floating leg.
- **NII.** Annualized accrual net interest income is
  ``Σ asset amount·rate − Σ liability amount·rate`` over ALL positions,
  including decomposed interest-rate-swap legs. The swap treatment is complete:
  a swap contributes its net carry to base NII —
  ``carry = notional × (floating index rate − fixed rate) / 100`` for a
  pay-fixed swap, and ``carry = notional × (fixed rate − floating index rate)
  / 100`` for a receive-fixed swap. The floating index rate is the base-curve
  zero rate at the floating leg's bucket midpoint (the same curve point the
  leg reprices at, e.g. the ``0.17y`` key for a 91-day T-bill index), stamped
  onto the leg's ``rate_pct`` when the swap is decomposed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
DURATION = Decimal("0.0001")
_HUNDRED = Decimal("100")
_TEN_THOUSAND = Decimal("10000")
_TWELVE = Decimal("12")
_ZERO = Decimal("0")
_ONE = Decimal("1")

type IrrStatus = Literal["green", "amber", "red"]
type IrrLineSection = Literal["irr_gap", "irr_eve", "irr_ear"]
type IrrSide = Literal["asset", "liability"]

# Ordered repricing buckets with their canonical midpoint (in years). Every
# position in a bucket prices and reprices at that midpoint; the discount curve
# is keyed by the same midpoints.
IRR_BUCKETS: tuple[tuple[str, Decimal], ...] = (
    ("overnight", Decimal("0.003")),
    ("1-7d", Decimal("0.014")),
    ("8-30d", Decimal("0.06")),
    ("1-3m", Decimal("0.17")),
    ("3-6m", Decimal("0.38")),
    ("6-12m", Decimal("0.75")),
    ("1-3y", Decimal("1.9")),
    ("3-5y", Decimal("4.0")),
    ("5y+", Decimal("7.0")),
)
BUCKET_MIDPOINTS: dict[str, Decimal] = {name: midpoint for name, midpoint in IRR_BUCKETS}
# Buckets whose midpoint falls on or within twelve months feed the ≤12m
# cumulative gap and the earnings-at-risk sum.
SHORT_END_BUCKETS: tuple[str, ...] = tuple(
    name for name, midpoint in IRR_BUCKETS if midpoint * _TWELVE <= _TWELVE
)

BASE_CURVE_SCENARIO = "base_curve"
IRR_SCENARIO_CODES: tuple[str, ...] = (
    "parallel_up_200",
    "parallel_down_200",
    "short_up_250",
    "short_down_250",
    "steepener",
    "flattener",
)
EAR_UP_BP = Decimal("200")
EAR_DOWN_BP = Decimal("-200")

SHOCK_PARALLEL_BP = "parallel_bp"
SHOCK_SHORT_BP = "short_bp"
SHOCK_LONG_BP = "long_bp"
SHOCK_DECAY_YEARS = "decay_years"
# Standard Basel short-rate weight for the rotational (steepener/flattener)
# scenarios: s(t) = e^(-t / 4).
ROTATIONAL_DECAY_YEARS = Decimal("4")

# An EVE change is green while it clears the supervisory limit by this buffer
# (as a fraction of the limit); amber sits between the buffer and the limit.
EVE_GREEN_FRACTION = Decimal("0.75")


class MissingParameterError(Exception):
    """A position or scenario needs a curve point/shock the parameter set lacks."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No active IRR parameter covers '{name}'.")
        self.name = name


class UnsupportedShockError(Exception):
    """A stress scenario carries a shock key the engine does not understand."""

    def __init__(self, scenario_code: str, shock_key: str) -> None:
        super().__init__(
            f"Stress scenario '{scenario_code}' carries unsupported shock key '{shock_key}'."
        )
        self.scenario_code = scenario_code
        self.shock_key = shock_key


class IrrComputationError(Exception):
    """The supplied positions produce a degenerate result (empty side, zero Tier 1)."""


@dataclass(frozen=True)
class IrrPosition:
    """One rate-sensitive banking-book position reduced to the fields IRR uses.

    ``is_hedge`` marks synthetic legs decomposed from a derivative hedge (e.g. an
    interest-rate swap). Hedge legs participate in gap, duration, EVE and the
    accrual net-interest-income base alike — the two legs' offsetting accruals
    net to the swap carry — so the flag is provenance, not an exclusion.
    """

    side: IrrSide
    bucket: str
    amount: Decimal
    rate_pct: Decimal
    fixed_or_float: str
    midpoint_years: Decimal
    source: str
    is_hedge: bool = False


@dataclass(frozen=True)
class IrrLineItem:
    section: IrrLineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class GapBucket:
    bucket: str
    midpoint_years: Decimal
    rsa: Decimal
    rsl: Decimal
    gap: Decimal
    cumulative_gap: Decimal
    within_12m: bool


@dataclass(frozen=True)
class GapResult:
    buckets: tuple[GapBucket, ...]
    rsa_total: Decimal
    rsl_total: Decimal
    gap_total: Decimal
    cumulative_12m_gap: Decimal
    line_items: tuple[IrrLineItem, ...]


@dataclass(frozen=True)
class DurationResult:
    pv_assets: Decimal
    pv_liabilities: Decimal
    asset_macaulay: Decimal
    asset_modified: Decimal
    asset_convexity: Decimal
    liability_macaulay: Decimal
    liability_modified: Decimal
    liability_convexity: Decimal
    duration_gap: Decimal


@dataclass(frozen=True)
class EveScenario:
    scenario_code: str
    eve: Decimal
    delta_eve: Decimal
    delta_eve_pct_tier1: Decimal
    breach: bool


@dataclass(frozen=True)
class EveResult:
    tier1: Decimal
    base_eve: Decimal
    scenarios: tuple[EveScenario, ...]
    worst_scenario_code: str
    worst_delta_eve: Decimal
    worst_delta_eve_pct_tier1: Decimal
    breach: bool
    line_items: tuple[IrrLineItem, ...]


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def duration_years(value: Decimal) -> Decimal:
    return value.quantize(DURATION, rounding=ROUND_HALF_UP)


def classify_eve_change(abs_pct: Decimal, limit_pct: Decimal) -> IrrStatus:
    """Classify an already-quantized ``|ΔEVE| / Tier 1`` percentage.

    Red once the change exceeds the supervisory limit, amber inside the buffer
    band just below it, green otherwise.
    """
    if abs_pct > limit_pct:
        return "red"
    if abs_pct > limit_pct * EVE_GREEN_FRACTION:
        return "amber"
    return "green"


def compute_gap(positions: Sequence[IrrPosition]) -> GapResult:
    """Materialize the repricing gap by bucket with the cumulative 12-month gap."""
    _require_known_buckets(positions)
    buckets: list[GapBucket] = []
    line_items: list[IrrLineItem] = []
    cumulative = _ZERO
    cumulative_12m = _ZERO
    for name, midpoint in IRR_BUCKETS:
        rsa = money(_bucket_total(positions, name, "asset"))
        rsl = money(_bucket_total(positions, name, "liability"))
        gap = money(rsa - rsl)
        cumulative = money(cumulative + gap)
        within_12m = name in SHORT_END_BUCKETS
        if within_12m:
            cumulative_12m = money(cumulative_12m + gap)
        buckets.append(
            GapBucket(
                bucket=name,
                midpoint_years=midpoint,
                rsa=rsa,
                rsl=rsl,
                gap=gap,
                cumulative_gap=cumulative,
                within_12m=within_12m,
            )
        )
        line_items.append(
            IrrLineItem(
                section="irr_gap",
                line_code=name,
                description=f"{_describe_bucket(name)} Repricing Gap (RSA − RSL)",
                exposure_amount=rsa,
                rate_pct=None,
                weighted_amount=gap,
            )
        )
    rsa_total = money(sum((bucket.rsa for bucket in buckets), _ZERO))
    rsl_total = money(sum((bucket.rsl for bucket in buckets), _ZERO))
    return GapResult(
        buckets=tuple(buckets),
        rsa_total=rsa_total,
        rsl_total=rsl_total,
        gap_total=money(rsa_total - rsl_total),
        cumulative_12m_gap=cumulative_12m,
        line_items=tuple(line_items),
    )


def compute_duration(
    positions: Sequence[IrrPosition], curve: Mapping[Decimal, Decimal]
) -> DurationResult:
    """Compute PV, Macaulay/modified duration and convexity for each side."""
    assets = [position for position in positions if position.side == "asset"]
    liabilities = [position for position in positions if position.side == "liability"]
    pv_a, mac_a, mod_a, cvx_a = _side_duration(assets, curve, "asset")
    pv_l, mac_l, mod_l, cvx_l = _side_duration(liabilities, curve, "liability")
    if pv_a <= _ZERO:
        raise IrrComputationError("Asset present value must be positive to compute duration.")
    duration_gap = duration_years(mod_a - (pv_l / pv_a) * mod_l)
    return DurationResult(
        pv_assets=pv_a,
        pv_liabilities=pv_l,
        asset_macaulay=mac_a,
        asset_modified=mod_a,
        asset_convexity=cvx_a,
        liability_macaulay=mac_l,
        liability_modified=mod_l,
        liability_convexity=cvx_l,
        duration_gap=duration_gap,
    )


def compute_eve(
    positions: Sequence[IrrPosition],
    curve: Mapping[Decimal, Decimal],
    shifts: Mapping[Decimal, Decimal],
) -> Decimal:
    """PV(assets) − PV(liabilities) after applying ``shifts`` (decimal) to the curve."""
    pv_assets = _ZERO
    pv_liabilities = _ZERO
    for position in positions:
        base_rate = _curve_rate(curve, position.midpoint_years) / _HUNDRED
        shifted_rate = base_rate + shifts.get(position.midpoint_years, _ZERO)
        pv = _present_value(position.amount, position.midpoint_years, shifted_rate)
        if position.side == "asset":
            pv_assets += pv
        else:
            pv_liabilities += pv
    return money(pv_assets - pv_liabilities)


def run_irr_scenarios(
    positions: Sequence[IrrPosition],
    curve: Mapping[Decimal, Decimal],
    scenario_shocks: Mapping[str, Mapping[str, Decimal]],
    tier1: Decimal,
    limit_pct: Decimal,
) -> EveResult:
    """Run the base plus six Basel EVE scenarios and evaluate the ΔEVE limit."""
    if tier1 <= _ZERO:
        raise IrrComputationError("Tier 1 capital must be positive to evaluate the EVE limit.")
    base_eve = compute_eve(positions, curve, {})
    line_items: list[IrrLineItem] = [
        IrrLineItem(
            section="irr_eve",
            line_code="base",
            description="Economic Value of Equity (Base Curve)",
            exposure_amount=base_eve,
            rate_pct=None,
            weighted_amount=_ZERO,
        )
    ]
    scenarios: list[EveScenario] = []
    for code in IRR_SCENARIO_CODES:
        shocks = scenario_shocks.get(code)
        if shocks is None:
            raise MissingParameterError(f"stress_shock:{code}")
        shifts = _scenario_shifts(code, shocks, curve)
        eve = compute_eve(positions, curve, shifts)
        delta = money(eve - base_eve)
        pct = ratio_pct(delta / tier1 * _HUNDRED)
        breach = ratio_pct(abs(delta) / tier1 * _HUNDRED) > limit_pct
        scenarios.append(
            EveScenario(
                scenario_code=code,
                eve=eve,
                delta_eve=delta,
                delta_eve_pct_tier1=pct,
                breach=breach,
            )
        )
        line_items.append(
            IrrLineItem(
                section="irr_eve",
                line_code=code,
                description=f"EVE Under {_describe_scenario(code)}",
                exposure_amount=eve,
                rate_pct=pct,
                weighted_amount=delta,
            )
        )
    worst = max(scenarios, key=lambda scenario: abs(scenario.delta_eve))
    return EveResult(
        tier1=money(tier1),
        base_eve=base_eve,
        scenarios=tuple(scenarios),
        worst_scenario_code=worst.scenario_code,
        worst_delta_eve=worst.delta_eve,
        worst_delta_eve_pct_tier1=worst.delta_eve_pct_tier1,
        breach=any(scenario.breach for scenario in scenarios),
        line_items=tuple(line_items),
    )


def compute_ear(gap_result: GapResult, delta_bp: Decimal) -> Decimal:
    """ΔNII = Σ Gap_i · (Δbp/10000) · (12 − months_i)/12 over the ≤12m buckets."""
    delta_rate = delta_bp / _TEN_THOUSAND
    total = _ZERO
    for bucket in gap_result.buckets:
        if not bucket.within_12m:
            continue
        months = bucket.midpoint_years * _TWELVE
        residual = (_TWELVE - months) / _TWELVE
        total += bucket.gap * delta_rate * residual
    return money(total)


def ear_line_item(delta_bp: Decimal, delta_nii: Decimal) -> IrrLineItem:
    direction = "Up" if delta_bp >= _ZERO else "Down"
    line_code = f"ear_{'up' if delta_bp >= _ZERO else 'down'}_{abs(int(delta_bp))}"
    return IrrLineItem(
        section="irr_ear",
        line_code=line_code,
        description=f"Earnings at Risk (Parallel {direction} {abs(int(delta_bp))} bp, ≤12m ΔNII)",
        exposure_amount=None,
        rate_pct=delta_bp,
        weighted_amount=delta_nii,
    )


def compute_nii(positions: Sequence[IrrPosition]) -> Decimal:
    """Annualized net interest income of the rate-sensitive book, swap carry included.

    Every position accrues ``amount × rate_pct / 100`` (added for assets,
    subtracted for liabilities), including decomposed interest-rate-swap legs:
    the receive-leg accrual minus the pay-leg accrual is the swap's net carry,
    ``notional × (floating index rate − fixed rate) / 100`` for a pay-fixed
    swap and ``notional × (fixed rate − floating index rate) / 100`` for a
    receive-fixed swap. The floating index rate is the base-curve zero rate at
    the floating leg's bucket midpoint — the same curve point the leg reprices
    at — which the swap decomposition stamps onto the leg's ``rate_pct``.
    """
    interest = _ZERO
    for position in positions:
        accrual = position.amount * position.rate_pct / _HUNDRED
        interest += accrual if position.side == "asset" else -accrual
    return money(interest)


def _side_duration(
    positions: Sequence[IrrPosition], curve: Mapping[Decimal, Decimal], side: str
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if not positions:
        return _ZERO, _ZERO, _ZERO, _ZERO
    pv_total = _ZERO
    weighted_time = _ZERO
    weighted_time_sq = _ZERO
    weighted_rate = _ZERO
    for position in positions:
        rate = _curve_rate(curve, position.midpoint_years) / _HUNDRED
        pv = _present_value(position.amount, position.midpoint_years, rate)
        pv_total += pv
        weighted_time += position.midpoint_years * pv
        weighted_time_sq += position.midpoint_years * position.midpoint_years * pv
        weighted_rate += rate * pv
    if pv_total <= _ZERO:
        raise IrrComputationError(f"{side} present value must be positive to compute duration.")
    macaulay = weighted_time / pv_total
    yield_rate = weighted_rate / pv_total
    modified = macaulay / (_ONE + yield_rate)
    convexity = (weighted_time_sq / pv_total) / ((_ONE + yield_rate) ** 2)
    return (
        money(pv_total),
        duration_years(macaulay),
        duration_years(modified),
        duration_years(convexity),
    )


def _scenario_shifts(
    scenario_code: str, shocks: Mapping[str, Decimal], curve: Mapping[Decimal, Decimal]
) -> dict[Decimal, Decimal]:
    known = {SHOCK_PARALLEL_BP, SHOCK_SHORT_BP, SHOCK_LONG_BP, SHOCK_DECAY_YEARS}
    for key in shocks:
        if key not in known:
            raise UnsupportedShockError(scenario_code, key)
    midpoints = sorted(curve.keys())
    if SHOCK_PARALLEL_BP in shocks:
        shift = shocks[SHOCK_PARALLEL_BP] / _TEN_THOUSAND
        return {midpoint: shift for midpoint in midpoints}
    if SHOCK_LONG_BP in shocks:
        if SHOCK_SHORT_BP not in shocks:
            raise MissingParameterError(f"stress_shock:{scenario_code}:{SHOCK_SHORT_BP}")
        short = shocks[SHOCK_SHORT_BP] / _TEN_THOUSAND
        long = shocks[SHOCK_LONG_BP] / _TEN_THOUSAND
        shifts: dict[Decimal, Decimal] = {}
        for midpoint in midpoints:
            short_weight = (-midpoint / ROTATIONAL_DECAY_YEARS).exp()
            shifts[midpoint] = short * short_weight + long * (_ONE - short_weight)
        return shifts
    if SHOCK_SHORT_BP in shocks:
        if SHOCK_DECAY_YEARS not in shocks:
            raise MissingParameterError(f"stress_shock:{scenario_code}:{SHOCK_DECAY_YEARS}")
        short = shocks[SHOCK_SHORT_BP] / _TEN_THOUSAND
        decay = shocks[SHOCK_DECAY_YEARS]
        return {midpoint: short * (-midpoint / decay).exp() for midpoint in midpoints}
    raise MissingParameterError(f"stress_shock:{scenario_code}:{SHOCK_PARALLEL_BP}")


def _present_value(amount: Decimal, midpoint: Decimal, rate: Decimal) -> Decimal:
    return money(amount / (_ONE + rate) ** midpoint)


def _curve_rate(curve: Mapping[Decimal, Decimal], midpoint: Decimal) -> Decimal:
    rate = curve.get(midpoint)
    if rate is None:
        raise MissingParameterError(f"base_curve:{midpoint}")
    return rate


def _bucket_total(positions: Sequence[IrrPosition], bucket: str, side: str) -> Decimal:
    return sum(
        (
            position.amount
            for position in positions
            if position.bucket == bucket and position.side == side
        ),
        _ZERO,
    )


def _require_known_buckets(positions: Iterable[IrrPosition]) -> None:
    for position in positions:
        if position.bucket not in BUCKET_MIDPOINTS:
            raise IrrComputationError(
                f"Position bucket '{position.bucket}' is not a valid IRR bucket."
            )


def _describe_bucket(name: str) -> str:
    return name.replace("-", " To ").replace("+", " Plus").title()


def _describe_scenario(code: str) -> str:
    return code.replace("_", " ").title()
