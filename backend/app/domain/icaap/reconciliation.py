"""Reconciliation: internal capital against the regulatory requirement (¶49(l)).

The guideline asks a bank to reconcile its internal assessment with the
regulatory one. That is two different reconciliations, and merging them —
as one table of "capital" — hides the question each is asking (D-015):

* **requirement**, by RISK. For each Pillar 1 risk the two sides agree by
  construction. For each Pillar 2 risk the internal figure is the bank's own
  assessment and the regulatory figure is whatever the supervisor imposed, and
  an explanation is owed when the bank's number is BELOW the supervisor's. Above
  it needs no defence — that is the bank being more conservative than required.
* **resources**, by COMPONENT, with eligibility. Internal capital may include
  things regulatory capital does not (unaudited profit), and regulatory capital
  caps what it does include (AT1 and Tier 2 against RWA). Every divergence is a
  line with an explanation, not a netted difference.

Plus one internal control, ``pillar2_source_consistency``, which compares the
same risk's figure across the places it appears — the ICAAP register, the
capital plan, the supervisory add-on, the stress overlay. It compares BASELINE
with baseline and STRESSED with stressed, enforced by the argument types: the
failure it exists to catch is a stressed figure read as a baseline one, which
looks like a large, alarming, entirely fictional inconsistency.

Aggregation is a simple sum (M19). No diversification benefit is assumed
across risk types unless the governed switch allows it, and then only as an
explicit, negative, Board-approved line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.domain.icaap.pillar2.types import MissingParameter
from app.domain.icaap.units import HUNDRED, ZERO, amount, pillar1_capital, ratio

PARAM_CAR_MIN = "car_min"
PARAM_CAR_MIN_INCLUDES_CCB1 = "icaap_car_min_includes_ccb1"
PARAM_CCB1 = "ccb1_pct"
PARAM_CCYB = "ccyb_pct"
PARAM_DSIB = "dsib_buffer_pct"
PARAM_AT1_CAP = "at1_cap_pct_rwa"
PARAM_TIER2_CAP = "tier2_cap_pct_rwa"
PARAM_SOURCE_TOLERANCE = "icaap_pillar2_source_tolerance_pct"

TOTAL_ROW = "total"

RiskGroup = Literal["pillar1", "pillar2"]
LineGroup = Literal["pillar1", "pillar2", "buffer", "supervisory", "diversification"]

TIER_CET1 = "cet1"
TIER_AT1 = "at1"
TIER_TIER2 = "tier2"


# --------------------------------------------------------------------------
# Requirement reconciliation, by risk
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegulatoryPolicy:
    """The governed capital policy, resolved at the cycle's as-of date."""

    car_min_pct: Decimal | None
    car_min_includes_ccb1: bool | None
    ccb1_pct: Decimal | None
    ccyb_pct: Decimal | None
    dsib_pct: Decimal | None


@dataclass(frozen=True)
class RiskInput:
    """One risk line, from whichever side of the reconciliation feeds it."""

    line_key: str
    label: str
    group: RiskGroup
    table5_row: str | None = None
    #: Pillar 1 lines carry RWA; the requirement is derived from it.
    rwa: Decimal | None = None
    #: Pillar 2 lines carry the ICAAP register's BASELINE amount.
    pillar2_baseline: Decimal | None = None
    #: Active supervisory add-ons attributed to this row.
    supervisory: Decimal = ZERO


@dataclass(frozen=True)
class RequirementLine:
    line_key: str
    label: str
    group: LineGroup
    table5_row: str | None
    internal: Decimal | None
    regulatory: Decimal | None
    difference: Decimal | None
    explanation_required: bool
    provenance: str


@dataclass(frozen=True)
class RequirementReconciliation:
    lines: tuple[RequirementLine, ...]
    total_internal_capital_requirement: Decimal
    total_regulatory_requirement: Decimal
    difference: Decimal
    explanation_required: bool


def _decimal_param(value: Decimal | None, code: str) -> Decimal:
    """A governed value this reconciliation cannot proceed without."""
    if value is None:
        raise MissingParameter(code)
    return value


def _flag_param(value: bool | None, code: str) -> bool:
    if value is None:
        raise MissingParameter(code)
    return value


def requirement_reconciliation(  # noqa: PLR0913 - the risks plus four governed inputs
    inputs: Sequence[RiskInput],
    *,
    total_rwa: Decimal,
    policy: RegulatoryPolicy,
    supervisory_unattributed: Decimal = ZERO,
    diversification: Decimal | None = None,
) -> RequirementReconciliation:
    """Line up internal and regulatory requirements risk by risk."""
    car_min_pct = _decimal_param(policy.car_min_pct, PARAM_CAR_MIN)
    includes_ccb1 = _flag_param(policy.car_min_includes_ccb1, PARAM_CAR_MIN_INCLUDES_CCB1)
    ccb1_pct = _decimal_param(policy.ccb1_pct, PARAM_CCB1)
    ccyb_pct = _decimal_param(policy.ccyb_pct, PARAM_CCYB)
    dsib_pct = _decimal_param(policy.dsib_pct, PARAM_DSIB)

    lines: list[RequirementLine] = []
    for risk in inputs:
        if risk.group == "pillar1":
            requirement = pillar1_capital(risk.rwa or ZERO, car_min_pct)
            lines.append(
                RequirementLine(
                    line_key=risk.line_key,
                    label=risk.label,
                    group="pillar1",
                    table5_row=risk.table5_row,
                    internal=requirement,
                    regulatory=requirement,
                    difference=ZERO,
                    explanation_required=False,
                    provenance=f"rwa*{PARAM_CAR_MIN}",
                )
            )
            continue
        internal = amount(risk.pillar2_baseline) if risk.pillar2_baseline is not None else ZERO
        regulatory = amount(risk.supervisory)
        lines.append(
            RequirementLine(
                line_key=risk.line_key,
                label=risk.label,
                group="pillar2",
                table5_row=risk.table5_row,
                internal=internal,
                regulatory=regulatory,
                difference=amount(internal - regulatory),
                explanation_required=internal < regulatory,
                provenance="icaap_register|supervisory_addons",
            )
        )

    for code, pct, label, include in (
        (PARAM_CCB1, ccb1_pct, "Capital conservation buffer", not includes_ccb1),
        (PARAM_CCYB, ccyb_pct, "Countercyclical buffer", True),
        (PARAM_DSIB, dsib_pct, "Systemic importance buffer", True),
    ):
        if not include:
            continue
        value = amount(pct * total_rwa / HUNDRED)
        lines.append(
            RequirementLine(
                line_key=code,
                label=label,
                group="buffer",
                table5_row=None,
                internal=value,
                regulatory=value,
                difference=ZERO,
                explanation_required=False,
                provenance=code,
            )
        )

    if supervisory_unattributed != ZERO:
        value = amount(supervisory_unattributed)
        lines.append(
            RequirementLine(
                line_key="supervisory_unattributed",
                label="Supervisory add-on not attributed to a risk",
                group="supervisory",
                table5_row=None,
                internal=None,
                regulatory=value,
                difference=None,
                explanation_required=True,
                provenance="supervisory_addons",
            )
        )

    if diversification is not None:
        value = amount(diversification)
        lines.append(
            RequirementLine(
                line_key="diversification_benefit",
                label="Diversification benefit",
                group="diversification",
                table5_row=None,
                internal=value,
                regulatory=None,
                difference=None,
                explanation_required=True,
                provenance="icaap_register",
            )
        )

    ticr = amount(sum((line.internal or ZERO for line in lines), ZERO))
    trr = amount(sum((line.regulatory or ZERO for line in lines), ZERO))
    return RequirementReconciliation(
        lines=tuple(lines),
        total_internal_capital_requirement=ticr,
        total_regulatory_requirement=trr,
        difference=amount(ticr - trr),
        explanation_required=ticr < trr,
    )


# --------------------------------------------------------------------------
# Resources reconciliation, by component
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    """One capital component, as the bank holds it and as the rules count it."""

    line_key: str
    label: str
    tier: str
    regulatory_amount: Decimal | None
    internal_amount: Decimal
    regulatory_eligible: bool


@dataclass(frozen=True)
class RecognitionCaps:
    """Governed caps on what each tier may contribute, against RWA."""

    at1_cap_pct_rwa: Decimal | None
    tier2_cap_pct_rwa: Decimal | None


@dataclass(frozen=True)
class ResourceLine:
    line_key: str
    label: str
    tier: str
    internal_amount: Decimal
    regulatory_amount: Decimal | None
    recognised_amount: Decimal
    above_cap_amount: Decimal
    regulatory_eligible: bool
    difference: Decimal | None
    explanation_required: bool


@dataclass(frozen=True)
class ResourcesReconciliation:
    lines: tuple[ResourceLine, ...]
    available_internal_capital: Decimal
    recognised_regulatory_capital: Decimal
    regulatory_total_capital: Decimal | None
    matches_regulatory_total: bool | None
    internal_capital_surplus: Decimal
    coverage_pct: Decimal | None
    explanation_required: bool


def resources_reconciliation(  # noqa: PLR0913 - the components plus four governed inputs
    components: Sequence[Component],
    *,
    total_rwa: Decimal,
    caps: RecognitionCaps,
    regulatory_total_capital: Decimal | None,
    ticr: Decimal,
) -> ResourcesReconciliation:
    """Line up internal resources with recognised regulatory capital."""
    at1_cap = _decimal_param(caps.at1_cap_pct_rwa, PARAM_AT1_CAP)
    tier2_cap = _decimal_param(caps.tier2_cap_pct_rwa, PARAM_TIER2_CAP)

    remaining = {
        TIER_AT1: amount(at1_cap * total_rwa / HUNDRED),
        TIER_TIER2: amount(tier2_cap * total_rwa / HUNDRED),
    }

    lines: list[ResourceLine] = []
    for component in sorted(components, key=lambda item: (item.tier, item.line_key)):
        regulatory = component.regulatory_amount if component.regulatory_eligible else None
        if regulatory is None:
            recognised = ZERO
            above_cap = ZERO
        elif component.tier in remaining:
            headroom = remaining[component.tier]
            recognised = amount(min(regulatory, headroom))
            above_cap = amount(max(ZERO, regulatory - recognised))
            remaining[component.tier] = amount(headroom - recognised)
        else:
            recognised = amount(regulatory)
            above_cap = ZERO
        difference = None if regulatory is None else amount(component.internal_amount - regulatory)
        lines.append(
            ResourceLine(
                line_key=component.line_key,
                label=component.label,
                tier=component.tier,
                internal_amount=amount(component.internal_amount),
                regulatory_amount=None if regulatory is None else amount(regulatory),
                recognised_amount=recognised,
                above_cap_amount=above_cap,
                regulatory_eligible=component.regulatory_eligible,
                difference=difference,
                explanation_required=not component.regulatory_eligible
                or difference != ZERO
                or above_cap > ZERO,
            )
        )

    available = amount(sum((line.internal_amount for line in lines), ZERO))
    recognised_total = amount(sum((line.recognised_amount for line in lines), ZERO))
    coverage = ratio(available * HUNDRED / ticr) if ticr > ZERO else None
    matches = (
        None
        if regulatory_total_capital is None
        else recognised_total == amount(regulatory_total_capital)
    )
    return ResourcesReconciliation(
        lines=tuple(lines),
        available_internal_capital=available,
        recognised_regulatory_capital=recognised_total,
        regulatory_total_capital=(
            None if regulatory_total_capital is None else amount(regulatory_total_capital)
        ),
        matches_regulatory_total=matches,
        internal_capital_surplus=amount(available - ticr),
        coverage_pct=coverage,
        explanation_required=any(line.explanation_required for line in lines) or matches is False,
    )


# --------------------------------------------------------------------------
# Internal control: the same risk, across the places it is stated
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineByRow:
    """Pillar 2 amounts on the BASELINE basis, keyed by Table 5 row."""

    values: Mapping[str, Decimal | None]


@dataclass(frozen=True)
class StressedByRow:
    """Pillar 2 amounts on the STRESSED basis, keyed by Table 5 row."""

    values: Mapping[str, Decimal | None]


ComparisonStatus = Literal["consistent", "inconsistent", "not_comparable", "both_absent"]

COMPARATOR_CAPITAL_PLAN = "capital_plan"
COMPARATOR_SUPERVISORY = "supervisory"
COMPARATOR_STRESS_OVERLAY = "stress_overlay"
BASIS_BASELINE = "baseline"
BASIS_STRESSED = "stressed"


@dataclass(frozen=True)
class Comparison:
    comparison_key: str
    basis: str
    comparator: str
    row: str
    icaap_value: Decimal | None
    other_value: Decimal | None
    relative_difference_pct: Decimal | None
    status: ComparisonStatus


def _compare(  # noqa: PLR0913 - the pair, its two labels and the governed tolerance
    basis: str,
    comparator: str,
    row: str,
    left: Decimal | None,
    right: Decimal | None,
    tolerance_pct: Decimal,
) -> Comparison:
    key = f"{basis}:{comparator}:{row}"
    if left is None and right is None:
        status: ComparisonStatus = "both_absent"
        relative = None
    elif left is None or right is None:
        status = "not_comparable"
        relative = None
    else:
        scale = max(abs(left), abs(right))
        if scale == ZERO:
            status = "consistent"
            relative = ZERO
        else:
            relative = ratio(abs(left - right) * HUNDRED / scale)
            status = "consistent" if relative <= tolerance_pct else "inconsistent"
    return Comparison(
        comparison_key=key,
        basis=basis,
        comparator=comparator,
        row=row,
        icaap_value=left,
        other_value=right,
        relative_difference_pct=relative,
        status=status,
    )


def _total(values: Mapping[str, Decimal | None]) -> Decimal | None:
    present = [value for value in values.values() if value is not None]
    if not present:
        return None
    return amount(sum(present, ZERO))


def _pairs(
    basis: str,
    comparator: str,
    left: Mapping[str, Decimal | None],
    right: Mapping[str, Decimal | None],
    tolerance_pct: Decimal,
) -> list[Comparison]:
    rows = sorted(set(left) | set(right))
    comparisons = [
        _compare(basis, comparator, row, left.get(row), right.get(row), tolerance_pct)
        for row in rows
    ]
    comparisons.append(
        _compare(basis, comparator, TOTAL_ROW, _total(left), _total(right), tolerance_pct)
    )
    return comparisons


def pillar2_source_consistency(  # noqa: PLR0913 - five sources and the governed tolerance
    *,
    icaap_baseline: BaselineByRow,
    plan_baseline: BaselineByRow,
    supervisory_baseline: BaselineByRow,
    icaap_stressed: StressedByRow,
    overlay_stressed: StressedByRow,
    tolerance_pct: Decimal | None,
) -> tuple[Comparison, ...]:
    """Compare each Pillar 2 row with itself, like basis for like basis."""
    if tolerance_pct is None:
        raise MissingParameter(PARAM_SOURCE_TOLERANCE)
    for name, value, expected in (
        ("icaap_baseline", icaap_baseline, BaselineByRow),
        ("plan_baseline", plan_baseline, BaselineByRow),
        ("supervisory_baseline", supervisory_baseline, BaselineByRow),
        ("icaap_stressed", icaap_stressed, StressedByRow),
        ("overlay_stressed", overlay_stressed, StressedByRow),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"basis_mismatch:{name}")

    comparisons: list[Comparison] = []
    comparisons.extend(
        _pairs(
            BASIS_BASELINE,
            COMPARATOR_CAPITAL_PLAN,
            icaap_baseline.values,
            plan_baseline.values,
            tolerance_pct,
        )
    )
    comparisons.extend(
        _pairs(
            BASIS_BASELINE,
            COMPARATOR_SUPERVISORY,
            icaap_baseline.values,
            supervisory_baseline.values,
            tolerance_pct,
        )
    )
    comparisons.extend(
        _pairs(
            BASIS_STRESSED,
            COMPARATOR_STRESS_OVERLAY,
            icaap_stressed.values,
            overlay_stressed.values,
            tolerance_pct,
        )
    )
    return tuple(comparisons)


__all__ = [
    "BASIS_BASELINE",
    "BASIS_STRESSED",
    "COMPARATOR_CAPITAL_PLAN",
    "COMPARATOR_STRESS_OVERLAY",
    "COMPARATOR_SUPERVISORY",
    "PARAM_AT1_CAP",
    "PARAM_CAR_MIN",
    "PARAM_CAR_MIN_INCLUDES_CCB1",
    "PARAM_CCB1",
    "PARAM_CCYB",
    "PARAM_DSIB",
    "PARAM_SOURCE_TOLERANCE",
    "PARAM_TIER2_CAP",
    "TIER_AT1",
    "TIER_CET1",
    "TIER_TIER2",
    "TOTAL_ROW",
    "BaselineByRow",
    "Comparison",
    "ComparisonStatus",
    "Component",
    "RecognitionCaps",
    "RegulatoryPolicy",
    "RequirementLine",
    "RequirementReconciliation",
    "ResourceLine",
    "ResourcesReconciliation",
    "RiskInput",
    "StressedByRow",
    "pillar2_source_consistency",
    "requirement_reconciliation",
    "resources_reconciliation",
]
