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

from app.domain.authority.outcomes import (
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    outcome,
)
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

#: The runtime vocabularies. ``ActionKind`` and friends are typing-only Literals,
#: erased at runtime, and a governed plan arrives from the database as plain
#: strings — so a kind outside this set used to produce an action that fired,
#: reported a trigger reason, and applied NOTHING (audit 2026-08-22 D-8).
ACTION_KINDS: frozenset[str] = frozenset(
    {"raise_capital", "revise_dividend", "reduce_risk", "sell_assets", "change_strategy", "other"}
)
CAPITAL_TIER_TARGETS: frozenset[str] = frozenset({"cet1", "at1", "tier2"})
SIZING_MODES: frozenset[str] = frozenset({"fixed", "fill_residual"})
TRIGGER_KINDS: frozenset[str] = frozenset({"always", "on_breach", "on_severity"})
#: The minima an ``on_breach`` trigger may watch (mirrors ``MinimaCheck.binding``).
WATCHABLE_MINIMA: frozenset[str] = frozenset({"car", "cet1", "tier1", "leverage", "paid_up"})
#: The action kinds that carry an RWA relief. A ``rwa_reduction_ghs`` on any
#: other kind was silently dropped by ``_apply_rwa``.
RWA_RELIEF_KINDS: frozenset[str] = frozenset(
    {"reduce_risk", "sell_assets", "change_strategy", "other"}
)

_SEVERITY_RANK: dict[str, int] = {"mild": 1, "moderate": 2, "severe": 3}
SEVERITIES: frozenset[str] = frozenset(_SEVERITY_RANK)


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


class ManagementActionNotComputable(ManagementActionError, NotComputable):
    """A management-action figure cannot be established from this plan.

    Doubly typed on the ``capital.engine.BiaGrossIncomeUnavailable`` pattern:
    ``ManagementActionError`` so every boundary that already refuses an invalid
    plan refuses this one identically, and ``NotComputable`` so the typed
    fail-closed detail travels with it.
    """

    def __init__(self, code: str, detail: OutcomeDetail) -> None:
        NotComputable.__init__(self, detail)
        self._code = code
        self.message = detail.message

    # ``NotComputable.code`` is a read-only property (``<state>:<metric_id>``),
    # so the plain attribute assignment ``ManagementActionError`` uses raises
    # ``AttributeError`` on this doubly-typed class. Overriding it keeps
    # ``exc.code`` the management-action code every existing boundary reads.
    @property
    def code(self) -> str:  # type: ignore[override]
        return self._code


class PostActionPositionNotComputable(ManagementActionNotComputable):
    """The post-action position has no denominator, so its ratios are not numbers.

    The overlay used to emit ``0`` for each of the four ratios whenever the
    post-action RWA or leverage exposure reached zero — an action plan that shrank
    the balance sheet to nothing produced a complete Appendix II
    "Post-capitalisation" block reading 0.00% CAR, which is a manufactured
    regulatory figure and not the absence of one (audit 2026-08-22 D-8b). The
    registered authority ``capital.engine.compute_capital_ratios`` raises
    ``CapitalComputationError`` on exactly this input; there is now one behaviour,
    not two.
    """


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

    def __post_init__(self) -> None:
        if self.kind not in TRIGGER_KINDS:
            raise ManagementActionError(
                "invalid_trigger_kind",
                f"Trigger kind '{self.kind}' is not one of {sorted(TRIGGER_KINDS)}.",
            )
        unknown = tuple(name for name in self.watch_minima if name not in WATCHABLE_MINIMA)
        if unknown:
            raise ManagementActionError(
                "invalid_watch_minima",
                f"Trigger watches unknown minima {sorted(unknown)}; expected a subset of "
                f"{sorted(WATCHABLE_MINIMA)}. An unknown name never matches a breach, so "
                "the action would never fire.",
            )
        if self.kind == "on_severity" and self.min_severity not in SEVERITIES:
            raise ManagementActionError(
                "invalid_min_severity",
                f"An 'on_severity' trigger needs a severity threshold from "
                f"{sorted(SEVERITIES)} (got {self.min_severity!r}). An unrecognised "
                "threshold ranked zero, so the trigger fired in every scenario.",
            )


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
        self._validate_vocabularies()
        self._validate_effects()

    def _validate_vocabularies(self) -> None:
        """Every enumerated field must be a value this module acts on.

        The Literals are erased at runtime and a governed plan arrives as plain
        strings, so an unrecognised value used to be accepted and then silently
        ignored by the effect appliers — a documented, approved, board-committed
        action that did nothing (audit 2026-08-22 D-8).
        """
        for field_name, value, allowed in (
            ("kind", self.kind, ACTION_KINDS),
            ("capital_raise_tier", self.capital_raise_tier, CAPITAL_TIER_TARGETS),
            ("sizing", self.sizing, SIZING_MODES),
        ):
            if value not in allowed:
                raise ManagementActionError(
                    f"invalid_action_{field_name}",
                    f"Action '{self.action_id}' has {field_name} '{value}', which is not "
                    f"one of {sorted(allowed)}.",
                )
        unknown_severities = tuple(
            name for name in self.severity_factors if name not in SEVERITIES
        )
        if unknown_severities:
            raise ManagementActionError(
                "invalid_severity_factors",
                f"Action '{self.action_id}' declares severity factors for "
                f"{sorted(unknown_severities)}, which are not scenario severities "
                f"({sorted(SEVERITIES)}).",
            )

    def _validate_effects(self) -> None:
        """A declared effect must belong to the action's kind.

        ``_apply_rwa`` and ``_apply_dividend`` filter on ``kind``, so an RWA
        relief on a ``raise_capital`` row (or a dividend reduction on an asset
        sale) was accepted, stored, snapshotted into the run's ``input_hash`` —
        and then dropped without a word.
        """
        if self.rwa_reduction_ghs > _ZERO and self.kind not in RWA_RELIEF_KINDS:
            raise ManagementActionError(
                "effect_not_supported_by_kind",
                f"Action '{self.action_id}' declares an RWA relief but its kind "
                f"'{self.kind}' does not carry one; RWA relief belongs to "
                f"{sorted(RWA_RELIEF_KINDS)}.",
            )
        if self.dividend_reduction_pct > _ZERO and self.kind != "revise_dividend":
            raise ManagementActionError(
                "effect_not_supported_by_kind",
                f"Action '{self.action_id}' declares a dividend reduction but its kind "
                f"is '{self.kind}', not 'revise_dividend'.",
            )
        if (
            self.capital_raise_ghs > _ZERO or self.sizing == "fill_residual"
        ) and self.kind != "raise_capital":
            raise ManagementActionError(
                "effect_not_supported_by_kind",
                f"Action '{self.action_id}' declares a capital raise but its kind is "
                f"'{self.kind}', not 'raise_capital'.",
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


def _no_denominator(
    code: str, year: int, *, metric_id: str, reason: str, items: tuple[str, ...]
) -> PostActionPositionNotComputable:
    """Refuse a post-action ratio whose denominator the plan drove to zero."""
    return PostActionPositionNotComputable(
        code,
        outcome(
            OutcomeState.NOT_COMPUTABLE,
            metric_id=metric_id,
            reason=reason,
            items=items,
            context={"projection_year": year},
        ),
    )


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

    # Fail closed on a zero denominator, exactly as the registered authority
    # ``capital.engine.compute_capital_ratios`` does (audit 2026-08-22 D-8b).
    # Reachable: an RWA-reduction action larger than credit RWA at an institution
    # with no market or operational charge, or a balance-sheet-shrinking action
    # whose leverage relief exceeds the exposure.
    if rwa <= _ZERO:
        raise _no_denominator(
            "post_action_rwa_not_positive",
            year.year,
            metric_id="post_action_car_pct",
            reason=(
                f"The management-action plan reduces year {year.year}'s risk-weighted "
                "assets to zero, so the post-action capital ratios have no denominator "
                "and are not numbers. Reduce the modelled RWA relief to a credible "
                "amount, or remove the action."
            ),
            items=("input:post_action_total_rwa",),
        )
    if leverage_exposure <= _ZERO:
        raise _no_denominator(
            "post_action_leverage_exposure_not_positive",
            year.year,
            metric_id="post_action_leverage_ratio_pct",
            reason=(
                f"The management-action plan reduces year {year.year}'s leverage "
                "exposure to zero, so the post-action leverage ratio has no denominator "
                "and is not a number. Reduce the modelled balance-sheet reduction to a "
                "credible amount, or remove the action."
            ),
            items=("input:post_action_leverage_exposure",),
        )
    car = ratio_pct(total / rwa * _HUNDRED)
    cet1_ratio = ratio_pct(cet1 / rwa * _HUNDRED)
    tier1_ratio = ratio_pct(tier1 / rwa * _HUNDRED)
    leverage = ratio_pct(tier1 / leverage_exposure * _HUNDRED)
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
        # Carry the institution's regime (enterprise audit P0-9 companion): this
        # was omitted, so the field defaulted True and the post-management-action
        # leg re-imposed the Basel CET1/Tier1/leverage minima on an SDI that the
        # projection leg had deliberately excluded (docs/sdi.md §4.6). An SDI's
        # "with actions" verdict could therefore be a breach of minima that do
        # not apply to it.
        basel_applicable=params.basel_applicable,
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
    # Every effect applier walks ``range(effective_year, horizon + 1)`` and reads
    # its year out of a dict, substituting a zero (dividends) or skipping the year
    # (RWA relief, capital stock) when the projection does not carry it. A
    # projection whose stress leg does not cover its own declared horizon would
    # therefore drop part of an approved plan silently (audit 2026-08-22 D-8).
    uncovered = tuple(
        str(year)
        for year in range(1, horizon + 1)
        if year not in {stress_year.year for stress_year in stress_years}
    )
    if uncovered:
        raise ManagementActionNotComputable(
            "projection_horizon_not_covered",
            outcome(
                OutcomeState.MISSING_REQUIRED_INPUT,
                metric_id="management_actions.post_action_position",
                reason=(
                    "The stress projection does not carry every year of its declared "
                    f"{horizon}-year horizon, so the plan's effects in the uncovered "
                    "years cannot be applied or reported."
                ),
                items=tuple(f"projection_year:{year}" for year in uncovered),
                context={"horizon_years": horizon, "uncovered": list(uncovered)},
            ),
        )
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


def severity_pricing_binds(factors: Mapping[str, Decimal]) -> bool:
    """True when a set of ¶81 factors makes the band change the magnitude.

    The lowest form of the rule, taking the mapping rather than an action so a
    caller holding STORED pricing can ask it without first constructing a
    :class:`ManagementAction` — construction validates vocabularies and refuses,
    which is right for a run and wrong for reading back a draft someone is still
    writing.

    Fewer than two distinct factors means the band cannot change anything.
    """
    return len(set(factors.values())) > 1


def default_severity_factors() -> dict[str, Decimal]:
    """The register an action inherits when it prices no bands of its own.

    Exported because it is NOT neutral — it is ``{mild, moderate, severe}`` with
    three distinct factors, so an action that stores no pricing still has its
    magnitude bound to the band. A caller that treated absent pricing as "no
    band needed" would be wrong in the direction that hurts.
    """
    return _default_severity_factors()


def is_severity_priced(action: ManagementAction) -> bool:
    """True when the scenario's severity band changes this action's magnitude.

    The single predicate behind both halves of the ¶81 severity rule: the
    run-time refusal in :func:`_severity_factor`, and the build-time readiness
    signal :func:`severity_priced_action_ids`. Stating it once is the point — a
    readiness surface that disagreed with the gate it advertises would send an
    analyst to a screen that says the plan is fine and a run that refuses it.

    Pure and total: it raises nothing and reads no database, so a caller may ask
    it of a draft plan the run would never accept.
    """
    return severity_pricing_binds(action.severity_factors)


def severity_priced_action_ids(plan: ManagementActionPlan) -> tuple[str, ...]:
    """The ids of actions in ``plan`` whose magnitude depends on the band.

    A plan holding any of these can only be run against a scenario that declares
    a severity band; against one that does not, :func:`_severity_factor` refuses
    (``scenario_severity_undeclared``). Surfacing the requirement on the PLAN —
    where it is a property of how the actions were priced, and knowable before a
    scenario is chosen — lets that be discovered while the plan is being written
    rather than at the moment someone tries to run it.

    Empty means the plan runs against any scenario, declared band or not.
    """
    return tuple(action.action_id for action in plan.actions if is_severity_priced(action))


def _severity_factor(action: ManagementAction, severity: str | None) -> Decimal:
    """The action's ¶81 severity-scaling factor.

    A severity the plan does not price REFUSES (audit 2026-08-22 D-8). The
    fallback used to be ``1.0`` — which in the default register is the SEVERE
    factor, the fullest lever the bank ever pulls — so a plan that priced only
    ``mild`` and ``moderate`` silently applied its largest possible action in a
    severe scenario, the most flattering assumption available.

    ``severity is None`` — the scenario declares no band, which the scenario
    register permits (``ck_macro_scenarios_severity`` allows NULL) — used to
    return ``_ONE`` on the reasoning that "there is no band to differentiate on".
    That reasoning is wrong, and this is the second half of the same defect
    (audit 2026-08-22 D-8, WS-A3 open item 2). ``_ONE`` is not a neutral value
    here: in the default register ``{mild: 0.5, moderate: 0.75, severe: 1}`` it
    IS the severe factor. An undeclared band therefore pulled every action to its
    full authored magnitude — the largest capital raise, the deepest RWA relief,
    the biggest dividend cut — and produced the most flattering post-action
    capital position the plan can produce, from the ABSENCE of a severity
    declaration rather than from a declared severe scenario.

    It now refuses, with one exception that is an explicit zero rather than an
    absent one: when every band the action prices carries the SAME factor (or it
    prices none at all), the undeclared band cannot change the magnitude, so
    there is nothing to resolve and that unambiguous factor stands.
    """
    if severity is None:
        if not is_severity_priced(action):
            # Every priced band carries the same factor, or none is priced: the
            # absent band cannot change the magnitude. Shares its predicate with
            # ``severity_priced_action_ids`` so the readiness surface and this
            # gate can never disagree about which actions need a band.
            return next(iter(set(action.severity_factors.values())), _ONE)
        raise ManagementActionNotComputable(
            "scenario_severity_undeclared",
            outcome(
                OutcomeState.POLICY_UNRESOLVED,
                metric_id="management_action.severity_factor",
                reason=(
                    f"Action '{action.action_id}' is priced differently by scenario "
                    "severity (Stress Testing Guideline paragraph 81), but the scenario "
                    "declares no severity band, so the magnitude the action would be "
                    "pulled to cannot be established. An undeclared band used to apply "
                    "the action at full magnitude, which is the plan's most favourable "
                    "assumption. Declare the scenario's severity, or price the action "
                    "identically across the bands so the band does not change it."
                ),
                items=(f"action:{action.action_id}", "scenario:severity"),
                context={
                    "action_id": action.action_id,
                    "severity": None,
                    "priced": {
                        name: str(value) for name, value in sorted(action.severity_factors.items())
                    },
                },
            ),
        )
    factor = action.severity_factors.get(severity)
    if factor is None:
        raise ManagementActionNotComputable(
            "severity_factor_unresolved",
            outcome(
                OutcomeState.POLICY_UNRESOLVED,
                metric_id="management_action.severity_factor",
                reason=(
                    f"Action '{action.action_id}' declares no severity factor for a "
                    f"'{severity}' scenario, so the magnitude it would be pulled to "
                    "cannot be established (Stress Testing Guideline paragraph 81 "
                    "severity differentiation). Price the action for every severity "
                    "the plan may be run against."
                ),
                items=(f"action:{action.action_id}", f"severity:{severity}"),
                context={
                    "action_id": action.action_id,
                    "severity": severity,
                    "priced": sorted(action.severity_factors),
                },
            ),
        )
    return factor


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
