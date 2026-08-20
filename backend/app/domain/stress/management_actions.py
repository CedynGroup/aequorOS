"""Management-actions modelling (docs/stress.md §3.7, Phase 3; ¶78–81, AppII T1).

The directive's Part IV requirement the platform lacked: model a **library of
credible management actions** (raising capital, dividend/distribution reduction,
reduction in RWA / reduced lending, asset sales, risk-appetite/limit changes,
change in business strategy), each with a **trigger**, a **quantified effect** on
the projection, a **timeline** (the projection year it takes effect), and a
**severity differentiation** (¶81) — and produce the enterprise capital
projection **with and without** those actions (¶67(f), AppII Table 1).

This module is PURE — Decimal-only, deterministic, free of any database or tenant
concern. It consumes the "results WITHOUT management actions" the projection
engine produces (:class:`app.domain.stress.projection.EnterpriseProjection`, the
directive's required pre-management-action output) and overlays a governed plan
onto the **stress leg** to yield the post-management-action position per year —
the "results WITH management actions". The service layer resolves an APPROVED
plan from the governed library, calls :func:`apply_management_actions`, and folds
the result into the immutable ``RegulatoryRun`` and the Appendix II Table 1
"Management actions" / "Post-capitalisation" / residual blocks.

**Why overlay the projection, not re-run it.** Appendix II Table 1 presents
management actions as discrete capital add-backs and RWA reliefs that bridge the
post-adverse (stress) position to the post-capitalisation position — a summary
overlay on the stressed capital and RWA, not a fresh dynamic projection. Applying
the actions arithmetically to each stress year's ``CapitalRatiosResult`` /
``RwaResult`` (the authoritative build the pure engines produced) matches that
table structure exactly and never re-derives a ratio the engine already owns.

**Effect model (all in full GHS; the Appendix II builder converts to GHS'000):**

- *Raising capital* — a one-off issuance adds a permanent stock to the chosen
  CRD tier (CET1/AT1/Tier2) from its effective year onward; an equity issuance
  (CET1) also lifts paid-up capital. AT1/Tier2 additions are recognised only up
  to the CRD caps (1.5% / 2.0% of post-action RWA), never below the pre-action
  recognised amount.
- *Dividend/distribution reduction* — preserves a fraction of each stress year's
  planned distribution as retained earnings, which **accumulates** into CET1 from
  the effective year onward.
- *Reduction in RWA / reduced lending / risk-appetite change / asset sales /
  change in business strategy* — reduce (credit) RWA from the effective year
  onward, floored at the non-credit (market + operational) RWA; balance-sheet-
  shrinking actions (asset sales, reduced lending) also reduce the leverage
  exposure.

**Severity differentiation (¶81).** Each action's magnitude is scaled by a
per-severity factor (default: mild 0.5, moderate 0.75, severe 1.0) — the bank
pulls the fuller lever in the more severe scenario. A ``fill_residual`` capital
raise is instead sized to the exact residual need, so severity flows through the
size of the gap it closes.

**Base scenario ⇒ no actions.** Under a base scenario the stress leg equals the
base leg and clears every minimum, so the breach-triggered actions do not fire
and the WITH projection is identical to the WITHOUT projection — a correct
zero-effect result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.domain.capital.engine import CapitalParams
from app.domain.stress.projection import (
    EnterpriseProjection,
    MinimaCheck,
    ProjectedYear,
)

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")

# CRD recognition caps for the ADDED capital (mirror Appendix II Table 2): AT1 is
# eligible up to 1.5% of RWA, Tier 2 up to 2% of RWA. Kept here rather than
# imported from ``appendix_ii`` so this pure module has no reverse dependency on
# the table builder (which itself consumes this module's result).
AT1_CAP_PCT_RWA = Decimal("1.5")
TIER2_CAP_PCT_RWA = Decimal("2")

# --- Vocabularies -----------------------------------------------------------

#: The credible action set the directive lists (¶79, AppII Table 1). ``kind``
#: drives the Table 1 line grouping and the effect a builder default applies.
ActionKind = Literal[
    "raise_capital",       # equity / AT1 / Tier2 issuance
    "revise_dividend",     # dividend / distribution reduction
    "reduce_risk",         # risk-appetite / limit change, reduced/tightened lending
    "sell_assets",         # sale of assets
    "change_strategy",     # change in business strategy
    "other",               # any other credible action
]
CapitalTierTarget = Literal["cet1", "at1", "tier2"]
SizingMode = Literal["fixed", "fill_residual"]
TriggerKind = Literal["always", "on_breach", "on_severity"]

_SEVERITY_RANK: dict[str, int] = {"mild": 1, "moderate": 2, "severe": 3}


def _default_severity_factors() -> dict[str, Decimal]:
    """¶81 severity differentiation: the fuller lever in the more severe case."""
    return {"mild": Decimal("0.5"), "moderate": Decimal("0.75"), "severe": Decimal("1")}


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


class ManagementActionError(Exception):
    """A management-action plan is structurally invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Triggers ---------------------------------------------------------------


@dataclass(frozen=True)
class ActionTrigger:
    """When an action fires (¶80 credible, triggered actions).

    - ``always`` — an unconditional, board-committed action.
    - ``on_breach`` — fires when the WITHOUT-actions stress projection breaches a
      watched minimum (``watch_minima`` empty ⇒ any of CAR/CET1/Tier1/leverage/
      paid-up).
    - ``on_severity`` — fires when the scenario severity reaches ``min_severity``.
    """

    kind: TriggerKind
    watch_minima: tuple[str, ...] = ()
    min_severity: str | None = None


# --- Actions ----------------------------------------------------------------


@dataclass(frozen=True)
class ManagementAction:
    """One credible management action with a trigger, effect, timeline, severity.

    Effect primitives are pre-severity, full-GHS; a given action populates the
    ones its ``kind`` implies. ``effective_year`` is the first projection year the
    action takes effect (1..horizon); its effect persists for every later year.
    """

    action_id: str
    kind: ActionKind
    label: str
    trigger: ActionTrigger
    effective_year: int = 1
    # Capital raise (kind == raise_capital).
    capital_raise_ghs: Decimal = _ZERO
    capital_raise_tier: CapitalTierTarget = "cet1"
    counts_as_paid_up: bool = False
    sizing: SizingMode = "fixed"
    # Dividend / distribution reduction (kind == revise_dividend): % of each
    # stress year's planned distribution preserved as retained earnings.
    dividend_reduction_pct: Decimal = _ZERO
    # RWA relief (kind ∈ reduce_risk / sell_assets / change_strategy / other).
    rwa_reduction_ghs: Decimal = _ZERO
    shrinks_leverage_exposure: bool = False
    # ¶81 severity differentiation.
    severity_factors: Mapping[str, Decimal] = field(default_factory=_default_severity_factors)
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.effective_year < 1:
            raise ManagementActionError(
                "invalid_effective_year",
                f"Action '{self.action_id}' effective_year must be >= 1 "
                f"(got {self.effective_year}).",
            )


@dataclass(frozen=True)
class ManagementActionPlan:
    """A governed, documented set of credible actions (¶80 documented plan)."""

    plan_id: str
    name: str
    actions: tuple[ManagementAction, ...]


# --- Resolved outputs -------------------------------------------------------


@dataclass(frozen=True)
class ResolvedAction:
    """One action after trigger evaluation, severity scaling, and sizing."""

    action_id: str
    kind: ActionKind
    label: str
    fired: bool
    trigger_reason: str
    effective_year: int
    severity_factor: Decimal
    resolved_capital_raise: Decimal
    capital_raise_tier: CapitalTierTarget
    counts_as_paid_up: bool
    resolved_rwa_reduction: Decimal
    dividend_preserved_total: Decimal
    rationale: str


@dataclass(frozen=True)
class YearActionAggregate:
    """The per-year, per-Table-1-line raw amounts (full GHS) the builder groups."""

    year: int
    capital_raise_cet1: Decimal
    capital_raise_at1: Decimal
    capital_raise_tier2: Decimal
    dividend_preserved: Decimal
    rwa_reduction_sale_of_assets: Decimal
    rwa_reduction_risk_reduction: Decimal
    rwa_reduction_business_strategy: Decimal
    rwa_reduction_other: Decimal
    paid_up_added: Decimal

    @property
    def capital_raise_total(self) -> Decimal:
        return self.capital_raise_cet1 + self.capital_raise_at1 + self.capital_raise_tier2

    @property
    def rwa_reduction_total(self) -> Decimal:
        return (
            self.rwa_reduction_sale_of_assets
            + self.rwa_reduction_risk_reduction
            + self.rwa_reduction_business_strategy
            + self.rwa_reduction_other
        )


@dataclass(frozen=True)
class PostActionYear:
    """One stress year after the management-action overlay (post-capitalisation)."""

    year: int
    cet1: Decimal
    at1: Decimal
    tier1: Decimal
    tier2: Decimal
    total_capital: Decimal
    total_rwa: Decimal
    paid_up: Decimal
    leverage_exposure: Decimal
    car_pct: Decimal
    cet1_ratio_pct: Decimal
    tier1_ratio_pct: Decimal
    leverage_ratio_pct: Decimal
    minima: MinimaCheck
    # The residual capital still required after actions to meet CAR + leverage +
    # paid-up at this year (0 when the actions fully restore adequacy).
    residual_capital_required: Decimal
    aggregate: YearActionAggregate


@dataclass(frozen=True)
class ManagementActionsResult:
    plan_id: str
    scenario_code: str
    severity: str | None
    car_target_pct: Decimal
    paid_up_min: Decimal
    actions: tuple[ResolvedAction, ...]
    post_action: tuple[PostActionYear, ...]
    stays_above_all_minima: bool
    first_breach_year: int | None
    binding_minima: tuple[str, ...]
    residual_capital_required: Decimal  # worst stress year, after actions

    def serialize(self) -> dict[str, object]:
        return _serialize_result(self)


# --- Per-year working accumulator --------------------------------------------


@dataclass
class _YearBucket:
    cr_cet1: Decimal = _ZERO
    cr_at1: Decimal = _ZERO
    cr_tier2: Decimal = _ZERO
    div_preserved: Decimal = _ZERO
    rwa_sale: Decimal = _ZERO
    rwa_risk: Decimal = _ZERO
    rwa_strategy: Decimal = _ZERO
    rwa_other: Decimal = _ZERO
    paid_up: Decimal = _ZERO
    lev_reduction: Decimal = _ZERO

    @property
    def cet1_add(self) -> Decimal:
        return self.cr_cet1 + self.div_preserved

    @property
    def rwa_reduction(self) -> Decimal:
        return self.rwa_sale + self.rwa_risk + self.rwa_strategy + self.rwa_other


@dataclass(frozen=True)
class _Position:
    cet1: Decimal
    at1: Decimal
    tier1: Decimal
    tier2: Decimal
    total: Decimal
    rwa: Decimal
    leverage_exposure: Decimal
    paid_up: Decimal
    car_pct: Decimal
    cet1_ratio_pct: Decimal
    tier1_ratio_pct: Decimal
    leverage_ratio_pct: Decimal


# --- Trigger evaluation ------------------------------------------------------


def _severity_rank(severity: str | None) -> int:
    return _SEVERITY_RANK.get(severity or "", 0)


def _evaluate_trigger(
    trigger: ActionTrigger, projection: EnterpriseProjection, severity: str | None
) -> tuple[bool, str]:
    if trigger.kind == "always":
        return True, "unconditional (board-committed)"
    if trigger.kind == "on_severity":
        threshold = trigger.min_severity
        fired = _severity_rank(severity) >= _severity_rank(threshold) and threshold is not None
        return fired, (
            f"scenario severity '{severity}' "
            f"{'>=' if fired else '<'} threshold '{threshold}'"
        )
    # on_breach
    watched = frozenset(trigger.watch_minima)
    for year in projection.stress:
        breaches = frozenset(year.minima.binding)
        hit = breaches if not watched else (breaches & watched)
        if hit:
            return True, (
                f"minimum {sorted(hit)} breached in stress year {year.year}"
            )
    scope = "any minimum" if not watched else f"{sorted(watched)}"
    return False, f"no breach of {scope} across the horizon"


# --- Position + shortfall arithmetic -----------------------------------------


def _position(year: ProjectedYear, bucket: _YearBucket) -> _Position:
    ratios = year.ratios
    non_credit_rwa = year.rwa.market_rwa + year.rwa.operational_rwa
    credit_post = max(year.rwa.credit_rwa - bucket.rwa_reduction, _ZERO)
    rwa = money(non_credit_rwa + credit_post)

    cet1 = money(ratios.cet1_capital + bucket.cet1_add)
    at1_cap = rwa * AT1_CAP_PCT_RWA / _HUNDRED
    at1 = money(max(ratios.at1_capital, min(ratios.at1_capital + bucket.cr_at1, at1_cap)))
    tier2_cap = rwa * TIER2_CAP_PCT_RWA / _HUNDRED
    tier2 = money(
        max(ratios.tier2_capital, min(ratios.tier2_capital + bucket.cr_tier2, tier2_cap))
    )
    tier1 = money(cet1 + at1)
    total = money(tier1 + tier2)
    leverage_exposure = money(max(ratios.leverage_exposure - bucket.lev_reduction, _ZERO))
    paid_up = money(year.paid_up + bucket.paid_up)

    car = ratio_pct(total / rwa * _HUNDRED) if rwa > _ZERO else _ZERO
    cet1_ratio = ratio_pct(cet1 / rwa * _HUNDRED) if rwa > _ZERO else _ZERO
    tier1_ratio = ratio_pct(tier1 / rwa * _HUNDRED) if rwa > _ZERO else _ZERO
    leverage = (
        ratio_pct(tier1 / leverage_exposure * _HUNDRED) if leverage_exposure > _ZERO else _ZERO
    )
    return _Position(
        cet1=cet1,
        at1=at1,
        tier1=tier1,
        tier2=tier2,
        total=total,
        rwa=rwa,
        leverage_exposure=leverage_exposure,
        paid_up=paid_up,
        car_pct=car,
        cet1_ratio_pct=cet1_ratio,
        tier1_ratio_pct=tier1_ratio,
        leverage_ratio_pct=leverage,
    )


def _cet1_injection_to_clear(
    pos: _Position,
    params: CapitalParams,
    car_target_pct: Decimal,
    paid_up_min: Decimal,
    counts_as_paid_up: bool,
) -> Decimal:
    """CET1 injection that would clear CAR, leverage (and paid-up if equity).

    A CET1 injection X lifts total, Tier 1 and — for an equity issuance — paid-up
    all by X, leaving RWA and the leverage exposure unchanged, so the binding
    need is the max of the three shortfalls.
    """
    car_need = max(car_target_pct * pos.rwa / _HUNDRED - pos.total, _ZERO)
    leverage_need = max(
        params.leverage_min_pct * pos.leverage_exposure / _HUNDRED - pos.tier1, _ZERO
    )
    paid_up_need = max(paid_up_min - pos.paid_up, _ZERO) if counts_as_paid_up else _ZERO
    return money(max(car_need, leverage_need, paid_up_need))


def _residual_after(
    pos: _Position, params: CapitalParams, car_target_pct: Decimal, paid_up_min: Decimal
) -> Decimal:
    """Residual capital still required to meet CAR + leverage + paid-up (¶77).

    Mirrors the pre-action Table 1 ``capital_gap`` definition (the worst of the
    CAR-target need and the paid-up shortfall) extended with the leverage need.
    """
    car_need = max(car_target_pct * pos.rwa / _HUNDRED - pos.total, _ZERO)
    leverage_need = max(
        params.leverage_min_pct * pos.leverage_exposure / _HUNDRED - pos.tier1, _ZERO
    )
    paid_up_need = max(paid_up_min - pos.paid_up, _ZERO)
    return money(max(car_need, leverage_need, paid_up_need))


def _minima(pos: _Position, params: CapitalParams, paid_up_min: Decimal) -> MinimaCheck:
    return MinimaCheck(
        car_pct=pos.car_pct,
        car_min_pct=params.car_min_pct,
        car_ok=pos.car_pct >= params.car_min_pct,
        cet1_pct=pos.cet1_ratio_pct,
        cet1_min_pct=params.cet1_min_pct,
        cet1_ok=pos.cet1_ratio_pct >= params.cet1_min_pct,
        tier1_pct=pos.tier1_ratio_pct,
        tier1_min_pct=params.tier1_min_pct,
        tier1_ok=pos.tier1_ratio_pct >= params.tier1_min_pct,
        leverage_pct=pos.leverage_ratio_pct,
        leverage_min_pct=params.leverage_min_pct,
        leverage_ok=pos.leverage_ratio_pct >= params.leverage_min_pct,
        paid_up=pos.paid_up,
        paid_up_min=paid_up_min,
        paid_up_ok=pos.paid_up >= paid_up_min,
    )


# --- The overlay -------------------------------------------------------------


def apply_management_actions(  # noqa: PLR0913 - names the full overlay input set
    projection: EnterpriseProjection,
    plan: ManagementActionPlan,
    *,
    severity: str | None,
    capital_params: CapitalParams,
    paid_up_min: Decimal,
    car_target_pct: Decimal,
) -> ManagementActionsResult:
    """Overlay a governed plan on the stress leg → the WITH-actions projection.

    Produces the post-management-action position for every stress year (the
    Appendix II "Post-capitalisation" block), the per-year Table 1 action
    aggregates, and the residual capital still required after actions (¶77).
    """
    stress_years = projection.stress
    horizon = projection.horizon_years
    buckets: dict[int, _YearBucket] = {year.year: _YearBucket() for year in stress_years}
    dividends_by_year: dict[int, Decimal] = {y.year: y.pnl.dividends for y in stress_years}

    resolved: list[ResolvedAction] = []

    # --- Pass 1: triggers, severity scaling, and every FIXED-size effect. -----
    fill_actions: list[tuple[ManagementAction, Decimal]] = []
    for action in plan.actions:
        fired, reason = _evaluate_trigger(action.trigger, projection, severity)
        factor = _severity_factor(action, severity)
        dividend_total = _ZERO
        rwa_total = _ZERO
        capital_stock = _ZERO
        if fired:
            dividend_total = _apply_dividend(action, factor, buckets, dividends_by_year, horizon)
            rwa_total = _apply_rwa(action, factor, buckets, horizon)
            if action.capital_raise_ghs > _ZERO and action.sizing == "fixed":
                capital_stock = money(action.capital_raise_ghs * factor)
                _apply_capital_stock(action, capital_stock, buckets, horizon)
            if action.sizing == "fill_residual":
                fill_actions.append((action, factor))
        resolved.append(
            ResolvedAction(
                action_id=action.action_id,
                kind=action.kind,
                label=action.label,
                fired=fired,
                trigger_reason=reason,
                effective_year=action.effective_year,
                severity_factor=factor,
                resolved_capital_raise=capital_stock,
                capital_raise_tier=action.capital_raise_tier,
                counts_as_paid_up=action.counts_as_paid_up,
                resolved_rwa_reduction=rwa_total,
                dividend_preserved_total=dividend_total,
                rationale=action.rationale,
            )
        )

    # --- Pass 2: size fill_residual raises against the post-fixed residual. ----
    for action, _factor in fill_actions:
        stock = _size_fill_residual(
            action, stress_years, buckets, capital_params, car_target_pct, paid_up_min
        )
        _apply_capital_stock(action, stock, buckets, horizon)
        for index, item in enumerate(resolved):
            if item.action_id == action.action_id and item.fired:
                resolved[index] = _with_resolved_capital(item, stock)
                break

    # --- Assemble the post-action years. --------------------------------------
    post_years: list[PostActionYear] = []
    worst_residual = _ZERO
    first_breach: int | None = None
    binding: set[str] = set()
    for year in stress_years:
        bucket = buckets[year.year]
        pos = _position(year, bucket)
        minima = _minima(pos, capital_params, paid_up_min)
        residual = _residual_after(pos, capital_params, car_target_pct, paid_up_min)
        worst_residual = max(worst_residual, residual)
        if not minima.all_ok and first_breach is None:
            first_breach = year.year
        binding.update(minima.binding)
        post_years.append(
            PostActionYear(
                year=year.year,
                cet1=pos.cet1,
                at1=pos.at1,
                tier1=pos.tier1,
                tier2=pos.tier2,
                total_capital=pos.total,
                total_rwa=pos.rwa,
                paid_up=pos.paid_up,
                leverage_exposure=pos.leverage_exposure,
                car_pct=pos.car_pct,
                cet1_ratio_pct=pos.cet1_ratio_pct,
                tier1_ratio_pct=pos.tier1_ratio_pct,
                leverage_ratio_pct=pos.leverage_ratio_pct,
                minima=minima,
                residual_capital_required=residual,
                aggregate=_aggregate(year.year, bucket),
            )
        )

    return ManagementActionsResult(
        plan_id=plan.plan_id,
        scenario_code=projection.scenario_code,
        severity=severity,
        car_target_pct=car_target_pct,
        paid_up_min=paid_up_min,
        actions=tuple(resolved),
        post_action=tuple(post_years),
        stays_above_all_minima=first_breach is None,
        first_breach_year=first_breach,
        binding_minima=tuple(sorted(binding)),
        residual_capital_required=money(worst_residual),
    )


def _severity_factor(action: ManagementAction, severity: str | None) -> Decimal:
    """The action's severity-scaling factor (1.0 when the severity is unmapped)."""
    if severity is None:
        return _ONE
    return action.severity_factors.get(severity, _ONE)


def _apply_dividend(
    action: ManagementAction,
    factor: Decimal,
    buckets: dict[int, _YearBucket],
    dividends_by_year: dict[int, Decimal],
    horizon: int,
) -> Decimal:
    """Accumulate preserved distributions into CET1 from ``effective_year`` on."""
    if action.kind != "revise_dividend" or action.dividend_reduction_pct <= _ZERO:
        return _ZERO
    rate = action.dividend_reduction_pct / _HUNDRED * factor
    cumulative = _ZERO
    total = _ZERO
    for year in range(1, horizon + 1):
        if year < action.effective_year:
            continue
        preserved = money(dividends_by_year.get(year, _ZERO) * rate)
        cumulative = money(cumulative + preserved)
        if year in buckets:
            buckets[year].div_preserved = money(buckets[year].div_preserved + cumulative)
        total = cumulative
    return total


def _apply_rwa(
    action: ManagementAction,
    factor: Decimal,
    buckets: dict[int, _YearBucket],
    horizon: int,
) -> Decimal:
    """Reduce RWA (and, when balance-sheet-shrinking, leverage exposure)."""
    if action.rwa_reduction_ghs <= _ZERO or action.kind not in (
        "reduce_risk",
        "sell_assets",
        "change_strategy",
        "other",
    ):
        return _ZERO
    amount = money(action.rwa_reduction_ghs * factor)
    for year in range(action.effective_year, horizon + 1):
        bucket = buckets.get(year)
        if bucket is None:
            continue
        if action.kind == "sell_assets":
            bucket.rwa_sale = money(bucket.rwa_sale + amount)
        elif action.kind == "reduce_risk":
            bucket.rwa_risk = money(bucket.rwa_risk + amount)
        elif action.kind == "change_strategy":
            bucket.rwa_strategy = money(bucket.rwa_strategy + amount)
        else:
            bucket.rwa_other = money(bucket.rwa_other + amount)
        if action.shrinks_leverage_exposure:
            bucket.lev_reduction = money(bucket.lev_reduction + amount)
    return amount


def _apply_capital_stock(
    action: ManagementAction,
    stock: Decimal,
    buckets: dict[int, _YearBucket],
    horizon: int,
) -> None:
    """Add a permanent capital stock to the chosen tier from ``effective_year``."""
    if stock <= _ZERO:
        return
    for year in range(action.effective_year, horizon + 1):
        bucket = buckets.get(year)
        if bucket is None:
            continue
        if action.capital_raise_tier == "cet1":
            bucket.cr_cet1 = money(bucket.cr_cet1 + stock)
            if action.counts_as_paid_up:
                bucket.paid_up = money(bucket.paid_up + stock)
        elif action.capital_raise_tier == "at1":
            bucket.cr_at1 = money(bucket.cr_at1 + stock)
        else:
            bucket.cr_tier2 = money(bucket.cr_tier2 + stock)


def _size_fill_residual(  # noqa: PLR0913 - names the full sizing input set
    action: ManagementAction,
    stress_years: Sequence[ProjectedYear],
    buckets: dict[int, _YearBucket],
    params: CapitalParams,
    car_target_pct: Decimal,
    paid_up_min: Decimal,
) -> Decimal:
    """Size a fill_residual CET1 raise to the worst post-fixed residual it spans."""
    worst = _ZERO
    for year in stress_years:
        if year.year < action.effective_year:
            continue
        pos = _position(year, buckets[year.year])
        need = _cet1_injection_to_clear(
            pos, params, car_target_pct, paid_up_min, action.counts_as_paid_up
        )
        worst = max(worst, need)
    return worst


def _with_resolved_capital(item: ResolvedAction, stock: Decimal) -> ResolvedAction:
    return ResolvedAction(
        action_id=item.action_id,
        kind=item.kind,
        label=item.label,
        fired=item.fired,
        trigger_reason=item.trigger_reason,
        effective_year=item.effective_year,
        severity_factor=item.severity_factor,
        resolved_capital_raise=stock,
        capital_raise_tier=item.capital_raise_tier,
        counts_as_paid_up=item.counts_as_paid_up,
        resolved_rwa_reduction=item.resolved_rwa_reduction,
        dividend_preserved_total=item.dividend_preserved_total,
        rationale=item.rationale,
    )


def _aggregate(year: int, bucket: _YearBucket) -> YearActionAggregate:
    return YearActionAggregate(
        year=year,
        capital_raise_cet1=bucket.cr_cet1,
        capital_raise_at1=bucket.cr_at1,
        capital_raise_tier2=bucket.cr_tier2,
        dividend_preserved=bucket.div_preserved,
        rwa_reduction_sale_of_assets=bucket.rwa_sale,
        rwa_reduction_risk_reduction=bucket.rwa_risk,
        rwa_reduction_business_strategy=bucket.rwa_strategy,
        rwa_reduction_other=bucket.rwa_other,
        paid_up_added=bucket.paid_up,
    )


# --- Documented in-code default plan -----------------------------------------


def default_action_plan(plan_id: str = "default_recovery") -> ManagementActionPlan:
    """A credible, bank-agnostic default recovery plan (the ¶79 documented set).

    Two levers that need no bank-specific magnitudes and are always credible:

    1. **Dividend suspension** (revise_dividend) — fires on any minimum breach,
       from year 1, preserving 100% of the planned distribution as retained
       earnings (severity-scaled).
    2. **Capital raise to restore adequacy** (equity issuance, CET1) — fires on a
       CAR / paid-up / leverage breach, effective year 2 (an external raise takes
       time to execute), sized to the residual so it lifts the position back above
       every minimum; the issuance also lifts paid-up.

    RWA-relief and asset-sale actions carry bank-specific magnitudes and so are
    authored per bank through the governed plan library, not this default.
    """
    return ManagementActionPlan(
        plan_id=plan_id,
        name="Default capital-restoration plan",
        actions=(
            ManagementAction(
                action_id="dividend_suspension",
                kind="revise_dividend",
                label="Suspend dividends and distributions",
                trigger=ActionTrigger(kind="on_breach"),
                effective_year=1,
                dividend_reduction_pct=_HUNDRED,
                rationale="Retain earnings while any regulatory minimum is under pressure.",
            ),
            ManagementAction(
                action_id="capital_raise",
                kind="raise_capital",
                label="Raise CET1 (rights issue / private placement)",
                trigger=ActionTrigger(
                    kind="on_breach", watch_minima=("car", "cet1", "tier1", "leverage", "paid_up")
                ),
                effective_year=2,
                sizing="fill_residual",
                capital_raise_ghs=_ONE,  # sentinel; sizing overrides the amount
                capital_raise_tier="cet1",
                counts_as_paid_up=True,
                rationale="Issue equity to restore the capital position above all minima (¶77).",
            ),
        ),
    )


# --- Serialization -----------------------------------------------------------


def _s(value: Decimal) -> str:
    return str(value)


def _serialize_aggregate(aggregate: YearActionAggregate) -> dict[str, object]:
    return {
        "year": aggregate.year,
        "capital_raise_cet1": _s(aggregate.capital_raise_cet1),
        "capital_raise_at1": _s(aggregate.capital_raise_at1),
        "capital_raise_tier2": _s(aggregate.capital_raise_tier2),
        "capital_raise_total": _s(aggregate.capital_raise_total),
        "dividend_preserved": _s(aggregate.dividend_preserved),
        "rwa_reduction_sale_of_assets": _s(aggregate.rwa_reduction_sale_of_assets),
        "rwa_reduction_risk_reduction": _s(aggregate.rwa_reduction_risk_reduction),
        "rwa_reduction_business_strategy": _s(aggregate.rwa_reduction_business_strategy),
        "rwa_reduction_other": _s(aggregate.rwa_reduction_other),
        "rwa_reduction_total": _s(aggregate.rwa_reduction_total),
        "paid_up_added": _s(aggregate.paid_up_added),
    }


def _serialize_result(result: ManagementActionsResult) -> dict[str, object]:
    return {
        "plan_id": result.plan_id,
        "scenario_code": result.scenario_code,
        "severity": result.severity,
        "car_target_pct": _s(result.car_target_pct),
        "paid_up_min": _s(result.paid_up_min),
        "stays_above_all_minima": result.stays_above_all_minima,
        "first_breach_year": result.first_breach_year,
        "binding_minima": list(result.binding_minima),
        "residual_capital_required": _s(result.residual_capital_required),
        "actions": [
            {
                "action_id": action.action_id,
                "kind": action.kind,
                "label": action.label,
                "fired": action.fired,
                "trigger_reason": action.trigger_reason,
                "effective_year": action.effective_year,
                "severity_factor": _s(action.severity_factor),
                "resolved_capital_raise": _s(action.resolved_capital_raise),
                "capital_raise_tier": action.capital_raise_tier,
                "counts_as_paid_up": action.counts_as_paid_up,
                "resolved_rwa_reduction": _s(action.resolved_rwa_reduction),
                "dividend_preserved_total": _s(action.dividend_preserved_total),
                "rationale": action.rationale,
            }
            for action in result.actions
        ],
        "post_action": [
            {
                "year": year.year,
                "cet1": _s(year.cet1),
                "at1": _s(year.at1),
                "tier1": _s(year.tier1),
                "tier2": _s(year.tier2),
                "total_capital": _s(year.total_capital),
                "total_rwa": _s(year.total_rwa),
                "paid_up": _s(year.paid_up),
                "leverage_exposure": _s(year.leverage_exposure),
                "car_pct": _s(year.car_pct),
                "cet1_ratio_pct": _s(year.cet1_ratio_pct),
                "tier1_ratio_pct": _s(year.tier1_ratio_pct),
                "leverage_ratio_pct": _s(year.leverage_ratio_pct),
                "minima_all_ok": year.minima.all_ok,
                "binding_minima": list(year.minima.binding),
                "residual_capital_required": _s(year.residual_capital_required),
                "aggregate": _serialize_aggregate(year.aggregate),
            }
            for year in result.post_action
        ],
    }
