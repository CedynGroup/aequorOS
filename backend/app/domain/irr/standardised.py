"""The IRRBB Standardised Framework engine (pure).

A second, separate engine. ``app/domain/irr/engine.py`` — nine generic buckets,
annual compounding, one reporting currency, an absolute-value outlier test — is
left untouched so every return already filed from it keeps its numbers. The
Standardised Framework differs in every structural dimension, so it is built
alongside rather than retrofitted:

* a governed bucket ladder (currently nineteen buckets — the count is DATA);
* cash flows per currency, principal and interest kept apart;
* continuous discounting, ``DF(t) = exp(-R(t)·t)`` at the bucket midpoint;
* behavioural treatment of non-maturing deposits, prepayments and term
  deposits, each responding to the scenario through a governed scalar, and
  each priced on ONE book: the customer's exercise is settled once per ladder
  and then builds the base case and every shocked case alike
  (:func:`_exercise_policy`);
* aggregation across currencies that weights gains at ZERO — a gain in one
  currency never pays for a loss in another;
* an outlier test over a governed scenario set, on the LOSS, not on the
  absolute change.

Purity: Decimal only, no ``app.services`` / ``app.models`` imports, and no
regulatory number written down (D-024). Shocks, buckets, caps, scalars,
thresholds, horizons and scenario sets all arrive in :class:`SfParameters`.

Refusals are typed and fail closed, never a silent understatement:

* ``irrbb_sf_options_unsupported`` — the book holds automatic interest-rate
  options and this engine does not value them (DV-010). A refusal is the honest
  answer; an unmodelled option understates the loss.
* ``nmd_avg_maturity_cap_exceeded`` — the bank's own core slotting breaches the
  governed average-maturity cap. The bank owns the slotting, so this refuses
  rather than clamping.
* ``tier1_unavailable`` — no Tier 1, so no outlier test.
* ``missing_curve`` — a material currency arrived without a zero curve.

Rounding. Reported money and ratios quantise to six decimal places
(``MONEY``/``RATIO_PCT``) with ``ROUND_HALF_UP``, which is the precision the
hand-checked golden vectors are stated to; presentation rounding is the
caller's. Per-currency ΔEVE is quantised BEFORE it is aggregated, so the
reported currency rows always add up to the reported total. Classification —
the outlier verdict — happens AFTER quantisation, so the stored percentage and
the verdict can never disagree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from app.domain.irr.standardised_params import (
    BASIS_POINTS_IN_UNIT,
    HUNDRED,
    MONTHS_IN_YEAR,
    ONE,
    SCENARIO_LABELS,
    SCENARIOS,
    ZERO,
    NmdCap,
    NmdCategory,
    Rotation,
    SfError,
    SfParameters,
)

#: Reported precision. Six places is what the hand-checked golden vectors are
#: stated to; it is a presentation quantum, not a regulatory value.
MONEY = Decimal("0.000001")
RATIO_PCT = Decimal("0.000001")

#: Working precision for the exponentials and the survival powers.
_PRECISION = 34

MEASURE_ALL = "all"
MEASURE_MANDATORY = "mandatory"
MEASURE_OUTLIER_SET = "outlier_set"

#: Behavioural families a ladder can belong to.
KIND_FIXED = "fixed"
KIND_PREPAYABLE = "prepayable"
KIND_TD_RETAIL_REDEEMABLE = "td_retail_redeemable"
KIND_TD_WHOLESALE_REDEEMABLE = "td_wholesale_redeemable"
KIND_NMD = "nmd"

type LadderKind = Literal[
    "fixed", "prepayable", "td_retail_redeemable", "td_wholesale_redeemable", "nmd"
]

#: How a customer exercises the option they hold over a ladder's cash flows.
#: The policy is chosen ONCE per ladder and then prices the base book and every
#: shocked book alike — see :func:`_exercise_policy` for why that matters.
POLICY_CONTRACTUAL = "contractual"
POLICY_FULL_REDEMPTION = "full_redemption"

#: Assumption tallies the engine raises itself, on top of the projection's.
TALLY_NMD_NO_CORE_ESTIMATE = "nmd_no_core_estimate"
TALLY_NMD_CORE_WITHOUT_DURATION = "nmd_core_without_duration"
TALLY_NMD_CORE_CAP_BINDING = "nmd_core_cap_binding"
TALLY_NMD_HISTORY_SHORT = "nmd_history_short"
TALLY_NO_PREPAYMENT_RATE = "no_prepayment_rate"
TALLY_NO_REDEMPTION_RATE = "no_redemption_rate"

#: No automatic-option valuation exists yet, so K_AO is stated, not assumed.
K_AO_ZERO_STATEMENT = (
    "No automatic interest-rate options were valued: the book declares none, so the "
    "automatic-option add-on is zero."
)
#: No post-shock rate floor is applied. The code names what the ENGINE did, not
#: what any standard requires: the extract of the framework's shock appendices
#: this engine was built from carries no floor provision, and the wider
#: standardised-framework literature does prescribe one, so the absence is a
#: divergence to be settled against the primary text rather than a finding.
#: The bank-facing sentence says exactly that (``regulatory_irr_sf``); the code
#: itself is a wire/DB key and stays stable, the way a ``bog_``-prefixed fact
#: category does. Corrected 2026-09-20: it previously asserted, to the filer,
#: that the framework prescribes none.
POST_SHOCK_FLOOR = "not_prescribed"


class SfOptionsUnsupportedError(SfError):
    """The book holds automatic interest-rate options this engine cannot value.

    DV-010: refusing is the feature. Valuing the rest of the book and reporting
    it as the Standardised Framework measure would understate ΔEVE by exactly
    the option value, invisibly.
    """

    #: The ONE name this condition has, on the exception, in the message and on
    #: the run row alike (D-061). The design once carried a second spelling for
    #: the run row; it was retired, because two names for one condition is
    #: exactly what confuses an examiner reading a run row against the refusal
    #: the operator saw. Never reintroduce an alias or a mapping table.
    CODE = "irrbb_sf_options_unsupported"

    def __init__(self, options: Sequence[AutomaticOption]) -> None:
        notional = sum((option.notional for option in options), ZERO)
        super().__init__(
            self.CODE,
            "The Standardised Framework cannot value automatic interest-rate options, "
            f"and this book holds {len(options)}.",
            {
                "count": len(options),
                "notional": str(_money(notional)),
                "option_types": sorted({option.option_type for option in options}),
            },
        )


class NmdMaturityCapExceededError(SfError):
    """Core deposits are slotted longer than the governed average-maturity cap."""

    CODE = "nmd_avg_maturity_cap_exceeded"

    def __init__(self, currency: str, category: str, average: Decimal, cap: Decimal) -> None:
        super().__init__(
            self.CODE,
            f"Core {category.replace('_', ' ')} deposits average "
            f"{_ratio(average)} years, above the {_ratio(cap)}-year cap.",
            {
                "currency": currency,
                "category": category,
                "average": str(_ratio(average)),
                "cap": str(_ratio(cap)),
            },
        )


class SfInputError(SfError):
    """An input the engine cannot compute from."""


# --- inputs ------------------------------------------------------------------


@dataclass(frozen=True)
class Ladder:
    """One homogeneous portfolio in one currency, already bucketed.

    Amounts are SIGNED: assets positive, liabilities negative. ``principal`` and
    ``interest`` are kept apart because the earnings measure reprices principal
    only. ``outstanding_end`` is the scheduled balance after the bucket's
    principal, which is what the prepayment survival runs on.
    """

    currency: str
    portfolio: str
    kind: LadderKind
    principal: tuple[Decimal, ...]
    interest: tuple[Decimal, ...]
    outstanding_end: tuple[Decimal, ...]
    cpr0: Decimal | None = None
    tdrr0: Decimal | None = None

    @property
    def opening_outstanding(self) -> Decimal:
        if not self.outstanding_end:
            return ZERO
        return self.outstanding_end[0] + self.principal[0]


@dataclass(frozen=True)
class NmdBalance:
    """A non-maturing deposit product's balance and the bank's core estimate."""

    currency: str
    category: NmdCategory
    product: str
    balance: Decimal
    core_estimate: Decimal | None = None
    core_maturity_years: Decimal | None = None
    history_years: Decimal | None = None
    side: Literal["asset", "liability"] = "liability"


@dataclass(frozen=True)
class CurrencyInputs:
    """One currency's curve, conversion rate and banking-book size."""

    currency: str
    zero_cc: tuple[Decimal, ...]
    fx_to_reporting: Decimal
    bb_assets_rep: Decimal
    bb_liabilities_rep: Decimal


@dataclass(frozen=True)
class AutomaticOption:
    ref: str
    currency: str
    option_type: str
    notional: Decimal


@dataclass(frozen=True)
class PriorPeriod:
    """The comparative column: last year's reported figures."""

    delta_eve: Mapping[str, Decimal] = field(default_factory=dict)
    delta_nii: Mapping[str, Decimal] = field(default_factory=dict)
    tier1: Decimal | None = None


@dataclass(frozen=True)
class SfInputs:
    as_of: date
    reporting_currency: str
    ladders: tuple[Ladder, ...]
    nmds: tuple[NmdBalance, ...]
    currencies: tuple[CurrencyInputs, ...]
    tier1: Decimal
    kao: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)
    automatic_options: tuple[AutomaticOption, ...] = ()
    prior: PriorPeriod | None = None
    tallies: Mapping[str, int] = field(default_factory=dict)


# --- results -----------------------------------------------------------------


@dataclass(frozen=True)
class CurrencyScenarioResult:
    currency: str
    scenario: str
    eve_base_native: Decimal
    eve_scenario_native: Decimal
    delta_eve_native: Decimal
    delta_eve_reporting: Decimal
    k_ao_native: Decimal
    delta_nii_native: Decimal
    delta_nii_reporting: Decimal


@dataclass(frozen=True)
class ScenarioResult:
    code: str
    label: str
    mandatory: bool
    in_outlier_set: bool
    by_currency: tuple[CurrencyScenarioResult, ...]
    loss: Decimal
    net: Decimal
    delta_nii: Decimal


@dataclass(frozen=True)
class MeasureSet:
    name: str
    scenarios: tuple[str, ...]
    measure: Decimal
    worst_scenario: str | None


@dataclass(frozen=True)
class Measures:
    all_scenarios: MeasureSet
    mandatory: MeasureSet
    outlier_set: MeasureSet


@dataclass(frozen=True)
class CurrencyScope:
    currency: str
    assets_reporting: Decimal
    liabilities_reporting: Decimal
    asset_share_pct: Decimal
    liability_share_pct: Decimal
    share_pct: Decimal
    material: bool
    fx_to_reporting: Decimal


@dataclass(frozen=True)
class Table8Row:
    code: str
    label: str
    delta_eve: Decimal
    delta_eve_net: Decimal
    delta_nii: Decimal
    delta_eve_prior: Decimal | None
    delta_nii_prior: Decimal | None


@dataclass(frozen=True)
class NmdDisclosure:
    currency: str
    category: str
    balance: Decimal
    core: Decimal
    non_core: Decimal
    core_cap_pct: Decimal
    cap_binding: bool
    average_core_maturity_years: Decimal
    longest_core_maturity_years: Decimal


@dataclass(frozen=True)
class Table7Quantitative:
    average_repricing_maturity_years: Decimal
    longest_repricing_maturity_years: Decimal


@dataclass(frozen=True)
class LadderRow:
    currency: str
    bucket_key: str
    principal: Decimal
    interest: Decimal
    net: Decimal


@dataclass(frozen=True)
class SfResult:
    as_of: date
    reporting_currency: str
    bucket_keys: tuple[str, ...]
    currencies: tuple[CurrencyScope, ...]
    excluded_currencies: tuple[str, ...]
    scenarios: tuple[ScenarioResult, ...]
    measures: Measures
    tier1: Decimal
    pct_tier1: Decimal
    outlier: bool
    outlier_threshold_pct: Decimal
    table8: tuple[Table8Row, ...]
    table7_quantitative: Table7Quantitative
    nmd_disclosure: tuple[NmdDisclosure, ...]
    ladder_base: tuple[LadderRow, ...]
    assumption_tallies: Mapping[str, int]
    parameters_pending_confirmation: tuple[str, ...]
    representative_parameters: tuple[str, ...]
    statements: tuple[str, ...]
    k_ao_statement: str = K_AO_ZERO_STATEMENT
    post_shock_floor: str = POST_SHOCK_FLOOR


# --- quantisation ------------------------------------------------------------


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


# --- shocks ------------------------------------------------------------------


def short_weight(t: Decimal, decay_x: Decimal) -> Decimal:
    """The short-rate shape ``exp(-t/x)``."""
    return (-t / decay_x).exp()


def scenario_shifts_bp(
    params: SfParameters, currency: str, scenario: str
) -> tuple[Decimal, ...]:
    """The scenario's shift at each bucket midpoint, in basis points."""
    parallel = params.shock_bp("parallel", currency)
    short = params.shock_bp("short", currency)
    long = params.shock_bp("long", currency)
    rotation = params.rotation
    shifts: list[Decimal] = []
    for midpoint in params.midpoints:
        weight = short_weight(midpoint, params.short_decay_x)
        short_shift = short * weight
        long_shift = long * (ONE - weight)
        shifts.append(
            _one_shift(scenario, parallel, abs(short_shift), abs(long_shift), rotation)
        )
    return tuple(shifts)


def _one_shift(
    scenario: str, parallel: Decimal, short: Decimal, long: Decimal, rotation: Rotation
) -> Decimal:
    if scenario == "parallel_up":
        return parallel
    if scenario == "parallel_down":
        return -parallel
    if scenario == "short_up":
        return short
    if scenario == "short_down":
        return -short
    if scenario == "steepener":
        return rotation.steep_short * short + rotation.steep_long * long
    if scenario == "flattener":
        return rotation.flat_short * short + rotation.flat_long * long
    raise SfInputError("unknown_scenario", f"{scenario!r} is not one of the six shapes")


def _discount_factors(
    zero_cc: Sequence[Decimal], shifts_bp: Sequence[Decimal], midpoints: Sequence[Decimal]
) -> list[Decimal]:
    return [
        (-(zero_cc[index] + shifts_bp[index] / BASIS_POINTS_IN_UNIT) * midpoint).exp()
        for index, midpoint in enumerate(midpoints)
    ]


def _present_value(flows: Sequence[Decimal], factors: Sequence[Decimal]) -> Decimal:
    return sum((flow * factors[index] for index, flow in enumerate(flows)), ZERO)


# --- behavioural transforms --------------------------------------------------


def behavioural_rate(rate: Decimal, multiplier: Decimal) -> Decimal:
    """A behavioural rate under a scenario, capped at full participation."""
    return min(ONE, multiplier * rate)


def prepayment_fractions(rate: Decimal, params: SfParameters) -> list[Decimal]:
    """Per-bucket prepayment fraction ``c_k``."""
    if params.cpr_time_scaling == "per_bucket_as_printed":
        return [rate] * params.bucket_count
    survival_rate = ONE - rate
    return [ONE - survival_rate**width for width in params.widths_years]


def apply_prepayment(
    ladder: Ladder, rate: Decimal, params: SfParameters
) -> tuple[list[Decimal], list[Decimal]]:
    """Redistribute a ladder's flows for a prepayment rate.

    ``CF(k) = S(k)·[P(k) + I(k)] + c(k)·S(k-1)·N(k-1)``. It telescopes: the
    prepaid share of the maturity bucket is exactly the share the survival
    factor removed from it, so principal is conserved.
    """
    fractions = prepayment_fractions(rate, params)
    principal: list[Decimal] = []
    interest: list[Decimal] = []
    survival_prev = ONE
    outstanding_prev = ladder.opening_outstanding
    for index in range(params.bucket_count):
        fraction = fractions[index]
        survival = survival_prev * (ONE - fraction)
        principal.append(
            survival * ladder.principal[index] + fraction * survival_prev * outstanding_prev
        )
        interest.append(survival * ladder.interest[index])
        survival_prev = survival
        outstanding_prev = ladder.outstanding_end[index]
    return principal, interest


def apply_redemption(ladder: Ladder, rate: Decimal) -> tuple[list[Decimal], list[Decimal]]:
    """Move a redeemed share of a term-deposit balance to the first bucket.

    The redeemed share takes its principal early and forfeits the interest it
    would have earned; the rest runs to contract.
    """
    remaining = ONE - rate
    principal = [remaining * amount for amount in ladder.principal]
    interest = [remaining * amount for amount in ladder.interest]
    principal[0] += rate * ladder.opening_outstanding
    return principal, interest


def _contractual(ladder: Ladder) -> tuple[list[Decimal], list[Decimal]]:
    return list(ladder.principal), list(ladder.interest)


def _all_redeemed(ladder: Ladder, params: SfParameters) -> tuple[list[Decimal], list[Decimal]]:
    principal = [ZERO] * params.bucket_count
    principal[0] = ladder.opening_outstanding
    return principal, [ZERO] * params.bucket_count


# --- currency scope ----------------------------------------------------------


def _share_pct(part: Decimal, total: Decimal) -> Decimal:
    if total == ZERO:
        return ZERO
    return part / total * HUNDRED


def _currency_scope(inputs: SfInputs, params: SfParameters) -> tuple[CurrencyScope, ...]:
    total_assets = sum((c.bb_assets_rep for c in inputs.currencies), ZERO)
    total_liabilities = sum((c.bb_liabilities_rep for c in inputs.currencies), ZERO)
    scopes: list[CurrencyScope] = []
    for currency in inputs.currencies:
        asset_share = _share_pct(currency.bb_assets_rep, total_assets)
        liability_share = _share_pct(currency.bb_liabilities_rep, total_liabilities)
        share = max(asset_share, liability_share)
        scopes.append(
            CurrencyScope(
                currency=currency.currency,
                assets_reporting=_money(currency.bb_assets_rep),
                liabilities_reporting=_money(currency.bb_liabilities_rep),
                asset_share_pct=_ratio(asset_share),
                liability_share_pct=_ratio(liability_share),
                share_pct=_ratio(share),
                material=_ratio(share) > params.major_currency_threshold_pct,
                fx_to_reporting=currency.fx_to_reporting,
            )
        )
    return tuple(scopes)


# --- non-maturing deposits ---------------------------------------------------


@dataclass
class _NmdWork:
    ladders: list[Ladder] = field(default_factory=list)
    disclosure: list[NmdDisclosure] = field(default_factory=list)
    tallies: dict[str, int] = field(default_factory=dict)
    assigned: list[tuple[Decimal, Decimal]] = field(default_factory=list)


def _tally(counter: dict[str, int], marker: str, count: int = 1) -> None:
    counter[marker] = counter.get(marker, 0) + count


def _core_estimates(
    balances: Sequence[NmdBalance], work: _NmdWork
) -> list[Decimal]:
    """The bank's core amount per product, before the category cap."""
    cores: list[Decimal] = []
    for balance in balances:
        if balance.core_estimate is None:
            _tally(work.tallies, TALLY_NMD_NO_CORE_ESTIMATE)
            cores.append(ZERO)
            continue
        if balance.core_maturity_years is None:
            _tally(work.tallies, TALLY_NMD_CORE_WITHOUT_DURATION)
            cores.append(ZERO)
            continue
        cores.append(balance.balance * balance.core_estimate)
    return cores


def _nmd_category(
    currency: str,
    category: str,
    balances: Sequence[NmdBalance],
    params: SfParameters,
    work: _NmdWork,
) -> None:
    cap = params.nmd_caps[category]
    total = sum((balance.balance for balance in balances), ZERO)
    cores = _core_estimates(balances, work)
    declared = sum(cores, ZERO)
    allowed = cap.core_cap_pct / HUNDRED * total
    binding = declared > allowed
    if binding:
        _tally(work.tallies, TALLY_NMD_CORE_CAP_BINDING)
        scale = allowed / declared if declared != ZERO else ZERO
        cores = [core * scale for core in cores]
    _check_history(balances, params, work)
    core_total = sum(cores, ZERO)
    _append_nmd_ladders(currency, balances, cores, params, work)
    slotted = _slotted_core(balances, cores)
    _check_maturity_cap(currency, category, slotted, cap)
    work.disclosure.append(
        NmdDisclosure(
            currency=currency,
            category=category,
            balance=_money(total),
            core=_money(core_total),
            non_core=_money(total - core_total),
            core_cap_pct=cap.core_cap_pct,
            cap_binding=binding,
            average_core_maturity_years=_ratio(_weighted(slotted)),
            longest_core_maturity_years=_ratio(
                max((years for _, years in slotted), default=ZERO)
            ),
        )
    )


def _slotted_core(
    balances: Sequence[NmdBalance], cores: Sequence[Decimal]
) -> tuple[tuple[Decimal, Decimal], ...]:
    """(core amount, assigned maturity) for every product with core deposits."""
    return tuple(
        (cores[index], balance.core_maturity_years)
        for index, balance in enumerate(balances)
        if cores[index] != ZERO and balance.core_maturity_years is not None
    )


def _check_history(
    balances: Sequence[NmdBalance], params: SfParameters, work: _NmdWork
) -> None:
    for balance in balances:
        if balance.history_years is None or balance.history_years < params.nmd_history_years:
            _tally(work.tallies, TALLY_NMD_HISTORY_SHORT)


def _weighted(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    total = sum((abs(weight) for weight, _ in pairs), ZERO)
    if total == ZERO:
        return ZERO
    return sum((abs(weight) * value for weight, value in pairs), ZERO) / total


def _check_maturity_cap(
    currency: str, category: str, slotted: Sequence[tuple[Decimal, Decimal]], cap: NmdCap
) -> None:
    """The bank owns its core slotting, so a breach refuses; it never clamps."""
    if not slotted:
        return
    average = _weighted(slotted)
    if average > cap.avg_maturity_cap_years:
        raise NmdMaturityCapExceededError(
            currency, category, average, cap.avg_maturity_cap_years
        )


def _append_nmd_ladders(
    currency: str,
    balances: Sequence[NmdBalance],
    cores: Sequence[Decimal],
    params: SfParameters,
    work: _NmdWork,
) -> None:
    size = params.bucket_count
    for index, balance in enumerate(balances):
        sign = ONE if balance.side == "asset" else -ONE
        core = cores[index]
        principal = [ZERO] * size
        principal[0] += sign * (balance.balance - core)
        if core != ZERO and balance.core_maturity_years is not None:
            principal[params.index_for_years(balance.core_maturity_years)] += sign * core
            work.assigned.append((core, balance.core_maturity_years))
        if balance.balance - core != ZERO:
            work.assigned.append((balance.balance - core, ZERO))
        work.ladders.append(
            Ladder(
                currency=currency,
                portfolio=f"NMD|{balance.category}|{balance.product}",
                kind=KIND_NMD,
                principal=tuple(principal),
                interest=tuple([ZERO] * size),
                outstanding_end=tuple(_running_outstanding(principal)),
            )
        )


def _running_outstanding(principal: Sequence[Decimal]) -> list[Decimal]:
    remaining = sum(principal, ZERO)
    ends: list[Decimal] = []
    for amount in principal:
        remaining -= amount
        ends.append(remaining)
    return ends


def _non_maturing_deposits(inputs: SfInputs, params: SfParameters) -> _NmdWork:
    work = _NmdWork()
    keys = sorted({(balance.currency, balance.category) for balance in inputs.nmds})
    for currency, category in keys:
        balances = [
            balance
            for balance in inputs.nmds
            if balance.currency == currency and balance.category == category
        ]
        _nmd_category(currency, category, balances, params, work)
    return work


# --- scenario cash flows -----------------------------------------------------


def _base_flows(
    ladder: Ladder, params: SfParameters, policy: str
) -> tuple[list[Decimal], list[Decimal]]:
    if policy == POLICY_FULL_REDEMPTION:
        return _all_redeemed(ladder, params)
    if ladder.kind == KIND_PREPAYABLE and ladder.cpr0 is not None:
        return apply_prepayment(ladder, min(ONE, ladder.cpr0), params)
    if ladder.kind == KIND_TD_RETAIL_REDEEMABLE and ladder.tdrr0 is not None:
        return apply_redemption(ladder, min(ONE, ladder.tdrr0))
    return _contractual(ladder)


def _scenario_flows(
    ladder: Ladder, scenario: str, params: SfParameters, policy: str
) -> tuple[list[Decimal], list[Decimal]]:
    """The ladder's flows under one scenario, on the chosen exercise policy."""
    if policy == POLICY_FULL_REDEMPTION:
        return _all_redeemed(ladder, params)
    if ladder.kind == KIND_PREPAYABLE and ladder.cpr0 is not None:
        rate = behavioural_rate(ladder.cpr0, params.cpr_multipliers[scenario])
        return apply_prepayment(ladder, rate, params)
    if ladder.kind == KIND_TD_RETAIL_REDEEMABLE and ladder.tdrr0 is not None:
        rate = behavioural_rate(ladder.tdrr0, params.tdrr_scalars[scenario])
        return apply_redemption(ladder, rate)
    return _contractual(ladder)


# --- per-currency computation ------------------------------------------------


@dataclass(frozen=True)
class _CurrencyRun:
    eve_base: Decimal
    base_principal: tuple[Decimal, ...]
    base_interest: tuple[Decimal, ...]
    eve: Mapping[str, Decimal]
    principal: Mapping[str, tuple[Decimal, ...]]


def _sum_flows(
    parts: Iterable[tuple[list[Decimal], list[Decimal]]], size: int
) -> tuple[list[Decimal], list[Decimal]]:
    principal = [ZERO] * size
    interest = [ZERO] * size
    for flow_principal, flow_interest in parts:
        for index in range(size):
            principal[index] += flow_principal[index]
            interest[index] += flow_interest[index]
    return principal, interest


def _run_currency(
    currency: CurrencyInputs, ladders: Sequence[Ladder], params: SfParameters
) -> _CurrencyRun:
    size = params.bucket_count
    if len(currency.zero_cc) != size:
        raise SfInputError(
            "missing_curve",
            f"{currency.currency} has no zero curve at every bucket midpoint",
            {"currency": currency.currency},
        )
    base_factors = _discount_factors(currency.zero_cc, [ZERO] * size, params.midpoints)
    policies = [_exercise_policy(ladder, params, base_factors) for ladder in ladders]
    base_parts = [
        _base_flows(ladder, params, policy)
        for ladder, policy in zip(ladders, policies, strict=True)
    ]
    base_principal, base_interest = _sum_flows(base_parts, size)
    base_flow = [base_principal[k] + base_interest[k] for k in range(size)]
    eve_base = _present_value(base_flow, base_factors)

    eve: dict[str, Decimal] = {}
    principal: dict[str, tuple[Decimal, ...]] = {}
    for scenario in SCENARIOS:
        shifts = scenario_shifts_bp(params, currency.currency, scenario)
        factors = _discount_factors(currency.zero_cc, shifts, params.midpoints)
        chosen = [
            _scenario_flows(ladder, scenario, params, policy)
            for ladder, policy in zip(ladders, policies, strict=True)
        ]
        scenario_principal, scenario_interest = _sum_flows(chosen, size)
        flow = [scenario_principal[k] + scenario_interest[k] for k in range(size)]
        eve[scenario] = _present_value(flow, factors)
        principal[scenario] = tuple(scenario_principal)
    return _CurrencyRun(
        eve_base=eve_base,
        base_principal=tuple(base_principal),
        base_interest=tuple(base_interest),
        eve=eve,
        principal=principal,
    )


def _exercise_policies(ladder: Ladder) -> tuple[str, ...]:
    """The exercises open to the customer on this ladder."""
    if ladder.kind == KIND_TD_WHOLESALE_REDEEMABLE:
        return (POLICY_CONTRACTUAL, POLICY_FULL_REDEMPTION)
    return (POLICY_CONTRACTUAL,)


def _exercise_policy(
    ladder: Ladder, params: SfParameters, base_factors: Sequence[Decimal]
) -> str:
    """The exercise most disadvantageous to the bank, chosen ONCE per ladder.

    A term deposit the depositor may break without penalty is an option the
    DEPOSITOR holds, and the framework prices it at the exercise that hurts the
    bank most. The decisive word is *once*: the policy is settled here, in the
    current rate environment, and the same policy then builds the base book and
    every shocked book. ΔEVE is therefore the change the SHOCK caused, on one
    behavioural book.

    Pricing the option on one leg only is not conservatism, it is a broken
    subtraction. Until the 2026-09-20 audit the base leg fell through to
    contractual while the shocked leg took the worst of {contractual, fully
    redeemed}; because moving a liability to the shortest bucket always
    minimises its present value, the shocked leg took full redemption in every
    scenario at every rate level. ΔEVE then collected the whole discount of a
    term liability: on a single ordinary deposit, 59.30% of Tier 1 and a
    supervisory OUTLIER verdict, with the six rate shocks moving the answer by
    0.05% of itself. A measure that barely responds to rates is not a measure
    of interest-rate risk, and it flagged a bank that was not an outlier. The
    same deposit now reports 0.01% — and 9.13% if it is a genuine term
    liability the depositor cannot break, which is a DATA question settled by
    the early-withdrawal terms at ingestion, not by this function.

    The consequence of the correction is stated rather than hidden: a wholesale
    deposit with no evidence of a redemption penalty is demandable at par, so
    it is priced as overnight money on BOTH legs and contributes almost no
    rate sensitivity of its own. That is the honest reading — a bank cannot
    lose funding protection it never had — and at balance-sheet level it is the
    conservative one, because it shortens the liability side and widens the
    repricing gap the asset side is measured against.

    Each ladder contributes additively to the currency's EVE, so choosing per
    ladder is exact rather than an approximation of a portfolio-level choice.
    A tie leaves the contractual policy in place: a worthless option is not
    exercised.
    """
    policies = _exercise_policies(ladder)
    if len(policies) == 1:
        return policies[0]
    scored = [
        (_present_value_of(_base_flows(ladder, params, policy), base_factors), policy)
        for policy in policies
    ]
    return min(scored, key=lambda item: item[0])[1]


def _present_value_of(
    flows: tuple[list[Decimal], list[Decimal]], factors: Sequence[Decimal]
) -> Decimal:
    flow_principal, flow_interest = flows
    return _present_value(
        [flow_principal[k] + flow_interest[k] for k in range(len(flow_principal))], factors
    )


def _delta_nii(
    principal: Sequence[Decimal], shifts_bp: Sequence[Decimal], params: SfParameters
) -> Decimal:
    """Repricing-gap earnings change over the governed horizon, loss-positive."""
    horizon = Decimal(params.nii_horizon_months) / Decimal(MONTHS_IN_YEAR)
    if horizon <= ZERO:
        return ZERO
    total = ZERO
    for index, midpoint in enumerate(params.midpoints):
        if midpoint >= horizon:
            continue
        shift = shifts_bp[index] / BASIS_POINTS_IN_UNIT
        total += principal[index] * shift * (ONE - midpoint / horizon)
    return -total


# --- assembly ----------------------------------------------------------------


def _scenario_results(
    inputs: SfInputs,
    params: SfParameters,
    runs: Mapping[str, _CurrencyRun],
    material: Sequence[CurrencyInputs],
) -> tuple[ScenarioResult, ...]:
    results: list[ScenarioResult] = []
    for scenario in SCENARIOS:
        rows = tuple(
            _currency_scenario_row(inputs, params, runs[c.currency], c, scenario)
            for c in material
        )
        losses = (row.delta_eve_reporting for row in rows if row.delta_eve_reporting > ZERO)
        loss = sum(losses, ZERO)
        results.append(
            ScenarioResult(
                code=scenario,
                label=SCENARIO_LABELS[scenario],
                mandatory=scenario in params.mandatory_scenarios,
                in_outlier_set=scenario in params.outlier_scenarios,
                by_currency=rows,
                loss=_money(loss),
                net=_money(sum((row.delta_eve_reporting for row in rows), ZERO)),
                delta_nii=_money(sum((row.delta_nii_reporting for row in rows), ZERO)),
            )
        )
    return tuple(results)


def _currency_scenario_row(
    inputs: SfInputs,
    params: SfParameters,
    run: _CurrencyRun,
    currency: CurrencyInputs,
    scenario: str,
) -> CurrencyScenarioResult:
    k_ao = inputs.kao.get((scenario, currency.currency), ZERO)
    delta = run.eve_base - run.eve[scenario] + k_ao
    shifts = scenario_shifts_bp(params, currency.currency, scenario)
    nii = _delta_nii(run.principal[scenario], shifts, params)
    return CurrencyScenarioResult(
        currency=currency.currency,
        scenario=scenario,
        eve_base_native=_money(run.eve_base),
        eve_scenario_native=_money(run.eve[scenario]),
        delta_eve_native=_money(delta),
        delta_eve_reporting=_money(delta * currency.fx_to_reporting),
        k_ao_native=_money(k_ao),
        delta_nii_native=_money(nii),
        delta_nii_reporting=_money(nii * currency.fx_to_reporting),
    )


def _measure(name: str, scenarios: Sequence[str], results: Sequence[ScenarioResult]) -> MeasureSet:
    selected = [result for result in results if result.code in scenarios]
    best: tuple[Decimal, str | None] = (ZERO, None)
    for result in selected:
        if result.loss > best[0]:
            best = (result.loss, result.code)
    return MeasureSet(
        name=name,
        scenarios=tuple(result.code for result in selected),
        measure=_money(best[0]),
        worst_scenario=best[1],
    )


def _table8(
    params: SfParameters, results: Sequence[ScenarioResult], prior: PriorPeriod | None
) -> tuple[Table8Row, ...]:
    by_code = {result.code: result for result in results}
    rows: list[Table8Row] = []
    for code in params.mandatory_scenarios:
        result = by_code[code]
        rows.append(
            Table8Row(
                code=code,
                label=SCENARIO_LABELS[code],
                delta_eve=result.loss,
                delta_eve_net=result.net,
                delta_nii=result.delta_nii,
                delta_eve_prior=_prior_value(prior, code, eve=True),
                delta_nii_prior=_prior_value(prior, code, eve=False),
            )
        )
    if rows:
        rows.append(_maximum_row(rows))
    return tuple(rows)


def _maximum_row(rows: Sequence[Table8Row]) -> Table8Row:
    priors_eve = [row.delta_eve_prior for row in rows if row.delta_eve_prior is not None]
    priors_nii = [row.delta_nii_prior for row in rows if row.delta_nii_prior is not None]
    return Table8Row(
        code="maximum",
        label="Maximum",
        delta_eve=max(row.delta_eve for row in rows),
        delta_eve_net=max(row.delta_eve_net for row in rows),
        delta_nii=max(row.delta_nii for row in rows),
        delta_eve_prior=max(priors_eve) if priors_eve else None,
        delta_nii_prior=max(priors_nii) if priors_nii else None,
    )


def _prior_value(prior: PriorPeriod | None, code: str, *, eve: bool) -> Decimal | None:
    if prior is None:
        return None
    table = prior.delta_eve if eve else prior.delta_nii
    value = table.get(code)
    return None if value is None else _money(value)


def _ladder_rows(
    params: SfParameters, runs: Mapping[str, _CurrencyRun], material: Sequence[CurrencyInputs]
) -> tuple[LadderRow, ...]:
    rows: list[LadderRow] = []
    for currency in material:
        run = runs[currency.currency]
        for index, key in enumerate(params.bucket_keys):
            principal = run.base_principal[index]
            interest = run.base_interest[index]
            rows.append(
                LadderRow(
                    currency=currency.currency,
                    bucket_key=key,
                    principal=_money(principal),
                    interest=_money(interest),
                    net=_money(principal + interest),
                )
            )
    return tuple(rows)


def _table7(assigned: Sequence[tuple[Decimal, Decimal]]) -> Table7Quantitative:
    """Notional-weighted average and longest maturity assigned to deposits."""
    weights = [abs(weight) for weight, _ in assigned]
    total = sum(weights, ZERO)
    if total == ZERO:
        return Table7Quantitative(
            average_repricing_maturity_years=ZERO, longest_repricing_maturity_years=ZERO
        )
    weighted = sum((abs(weight) * years for weight, years in assigned), ZERO)
    return Table7Quantitative(
        average_repricing_maturity_years=_ratio(weighted / total),
        longest_repricing_maturity_years=_ratio(max(years for _, years in assigned)),
    )


def _statements(params: SfParameters, tallies: Mapping[str, int]) -> tuple[str, ...]:
    lines = [row.statement for row in params.provenance if row.representative or
             row.pending_confirmation]
    if tallies:
        lines.append(
            "Modelling assumptions were substituted where positions arrived without terms; "
            "every substitution is counted in the assumption tallies."
        )
    return tuple(lines)


def _refuse_options(inputs: SfInputs) -> None:
    if inputs.automatic_options:
        raise SfOptionsUnsupportedError(inputs.automatic_options)


def _outlier(measure: Decimal, tier1: Decimal, threshold: Decimal) -> tuple[Decimal, bool]:
    pct = _ratio(measure / tier1 * HUNDRED)
    return pct, pct > threshold


def run(inputs: SfInputs, params: SfParameters) -> SfResult:
    """Compute the Standardised Framework result for one as-of date."""
    with localcontext() as context:
        context.prec = _PRECISION
        return _run(inputs, params)


def _run(inputs: SfInputs, params: SfParameters) -> SfResult:
    _refuse_options(inputs)
    if inputs.tier1 <= ZERO:
        raise SfInputError(
            "tier1_unavailable", "The outlier test needs a positive Tier 1 capital figure."
        )
    scopes = _currency_scope(inputs, params)
    material_codes = {scope.currency for scope in scopes if scope.material}
    material = tuple(c for c in inputs.currencies if c.currency in material_codes)
    nmd = _non_maturing_deposits(inputs, params)

    runs = {
        currency.currency: _run_currency(
            currency, _ladders_for(currency.currency, inputs, nmd), params
        )
        for currency in material
    }
    results = _scenario_results(inputs, params, runs, material)
    measures = Measures(
        all_scenarios=_measure(MEASURE_ALL, SCENARIOS, results),
        mandatory=_measure(MEASURE_MANDATORY, params.mandatory_scenarios, results),
        outlier_set=_measure(MEASURE_OUTLIER_SET, params.outlier_scenarios, results),
    )
    pct, outlier = _outlier(
        measures.outlier_set.measure, inputs.tier1, params.outlier_threshold_pct
    )
    tallies = dict(inputs.tallies)
    for marker, count in nmd.tallies.items():
        _tally(tallies, marker, count)
    return SfResult(
        as_of=inputs.as_of,
        reporting_currency=inputs.reporting_currency,
        bucket_keys=params.bucket_keys,
        currencies=scopes,
        excluded_currencies=tuple(
            scope.currency for scope in scopes if not scope.material
        ),
        scenarios=results,
        measures=measures,
        tier1=_money(inputs.tier1),
        pct_tier1=pct,
        outlier=outlier,
        outlier_threshold_pct=params.outlier_threshold_pct,
        table8=_table8(params, results, inputs.prior),
        table7_quantitative=_table7(nmd.assigned),
        nmd_disclosure=tuple(nmd.disclosure),
        ladder_base=_ladder_rows(params, runs, material),
        assumption_tallies=tallies,
        parameters_pending_confirmation=params.pending_codes,
        representative_parameters=params.representative_codes,
        statements=_statements(params, tallies),
    )


def _ladders_for(currency: str, inputs: SfInputs, nmd: _NmdWork) -> tuple[Ladder, ...]:
    return tuple(
        ladder
        for ladder in (*inputs.ladders, *nmd.ladders)
        if ladder.currency == currency
    )
