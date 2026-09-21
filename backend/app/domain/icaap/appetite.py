"""Risk appetite: direction-aware limits, and what it means to breach one.

Half the metrics a bank sets appetite on are FLOORS (capital ratios, liquidity
ratios — higher is safer) and half are CEILINGS (NPL ratio, open position,
concentration — lower is safer). Code that assumes one direction gets the other
exactly backwards and reports a breach as headroom, so direction is carried on
every comparison here rather than implied by the metric's name.

The ladder is appetite → tolerance → capacity, each no stronger than the last,
and capacity no weaker than the regulatory value. That last rule is what makes
the framework real: a bank may not declare that it can live below the
supervisor's minimum. The regulatory value is resolved from the console at the
cycle's as-of date and passed in — this module never knows a floor (D-024).

When no floor is governed at all (the liquidity ratios, whose directive is not
public), the capacity check reads "not assessed against a regulatory floor" and
readiness flags it. That is D-036: an absent floor is a gap to be shown, not a
refusal, and never a number invented here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from app.domain.icaap.units import HUNDRED, ZERO, ratio
from app.domain.policy import Direction

Rag = Literal["green", "amber", "red", "black", "none"]
Trend = Literal["improving", "deteriorating", "stable", "unknown"]


@dataclass(frozen=True)
class Thresholds:
    """The bank's own three levels, in the metric's unit."""

    appetite: Decimal
    tolerance: Decimal
    capacity: Decimal


@dataclass(frozen=True)
class RegulatoryReference:
    """A governed value the capacity level is checked against."""

    param_code: str
    value: Decimal
    direction: Direction
    #: ``pending`` / ``confirmed`` — printed with the figure (D-039).
    confirmation_status: str
    #: True when the row is a platform calibration, not a published rule.
    representative: bool = False


class OrderingViolation(StrEnum):
    """A ladder that does not hold together."""

    APPETITE_WEAKER_THAN_TOLERANCE = "appetite_weaker_than_tolerance"
    TOLERANCE_WEAKER_THAN_CAPACITY = "tolerance_weaker_than_capacity"
    CAPACITY_WEAKER_THAN_REGULATORY = "capacity_weaker_than_regulatory"
    DIRECTION_MISMATCH = "direction_mismatch"


class AppetiteStatus(StrEnum):
    WITHIN_APPETITE = "within_appetite"
    APPETITE_EXCEEDED = "appetite_exceeded"
    TOLERANCE_EXCEEDED = "tolerance_exceeded"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    REGULATORY_BREACH = "regulatory_breach"
    NOT_EVALUABLE = "not_evaluable"


RAG: Mapping[AppetiteStatus, Rag] = MappingProxyType(
    {
        AppetiteStatus.WITHIN_APPETITE: "green",
        AppetiteStatus.APPETITE_EXCEEDED: "amber",
        AppetiteStatus.TOLERANCE_EXCEEDED: "red",
        AppetiteStatus.CAPACITY_EXCEEDED: "black",
        AppetiteStatus.REGULATORY_BREACH: "black",
        AppetiteStatus.NOT_EVALUABLE: "none",
    }
)


@dataclass(frozen=True)
class MetricDef:
    """Catalogue metadata for a standard metric. Structural — no numbers."""

    key: str
    label: str
    unit: str
    direction: Direction
    default_risk_key: str
    source_block_type: str | None
    source_fact_key: str | None
    regulatory_param_code: str | None
    board_register_code: str | None


UNIT_PCT = "pct"
UNIT_INDEX = "index"

_CATALOGUE: tuple[MetricDef, ...] = (
    MetricDef(
        "car",
        "Total capital ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "capital_adequacy",
        "capital_position",
        "car_pct",
        "car_min",
        "car_min",
    ),
    MetricDef(
        "cet1_ratio",
        "Common equity tier 1 ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "capital_adequacy",
        "capital_position",
        "cet1_ratio_pct",
        "cet1_min",
        "cet1_min",
    ),
    MetricDef(
        "tier1_ratio",
        "Tier 1 capital ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "capital_adequacy",
        "capital_position",
        "tier1_ratio_pct",
        "tier1_min",
        "tier1_min",
    ),
    MetricDef(
        "leverage_ratio",
        "Leverage ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "capital_adequacy",
        "capital_position",
        "leverage_ratio_pct",
        "leverage_min",
        "leverage_min",
    ),
    MetricDef(
        "lcr",
        "Liquidity coverage ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "liquidity",
        "ilaap",
        "lcr_pct",
        "lcr_min",
        None,
    ),
    MetricDef(
        "nsfr",
        "Net stable funding ratio",
        UNIT_PCT,
        Direction.FLOOR,
        "liquidity",
        "ilaap",
        "nsfr_pct",
        "nsfr_min",
        None,
    ),
    MetricDef(
        "irrbb_eve_loss_pct_tier1",
        "Economic-value loss against tier 1",
        UNIT_PCT,
        Direction.CEILING,
        "irrbb",
        "pillar2_summary",
        "irrbb_outlier_measure_pct",
        "irrbb_outlier_threshold_pct_tier1",
        None,
    ),
    MetricDef(
        "fx_nop_pct_tier1",
        "Net open position against tier 1",
        UNIT_PCT,
        Direction.CEILING,
        "market_risk",
        "fx_position",
        "nop_pct_tier1",
        None,
        None,
    ),
    MetricDef(
        "largest_group_pct_capital",
        "Largest connected group against capital",
        UNIT_PCT,
        Direction.CEILING,
        "credit_concentration",
        "concentration",
        "single_name_top_share_of_capital_pct",
        "single_obligor_limit_pct",
        None,
    ),
    MetricDef(
        "npl_ratio",
        "Non-performing loan ratio",
        UNIT_PCT,
        Direction.CEILING,
        "credit_risk",
        None,
        None,
        "npl_limit_pct",
        None,
    ),
    MetricDef(
        "name_hhi",
        "Single-name concentration index",
        UNIT_INDEX,
        Direction.CEILING,
        "credit_concentration",
        "concentration",
        "hhi_single_name",
        None,
        None,
    ),
)

APPETITE_METRIC_CATALOGUE: Mapping[str, MetricDef] = MappingProxyType(
    {metric.key: metric for metric in _CATALOGUE}
)
#: A custom metric may only reference a governed code the catalogue already
#: uses, so a bank cannot bind its appetite to an arbitrary control-plane row.
ALLOWED_REFERENCE_CODES: frozenset[str] = frozenset(
    metric.regulatory_param_code
    for metric in _CATALOGUE
    if metric.regulatory_param_code is not None
)


def at_least_as_strict(direction: Direction, a: Decimal, b: Decimal) -> bool:
    """Is ``a`` at least as conservative as ``b``? Equality counts."""
    return a >= b if direction is Direction.FLOOR else a <= b


def validate_ordering(
    direction: Direction, thresholds: Thresholds, reference: RegulatoryReference | None
) -> tuple[OrderingViolation, ...]:
    """Appetite ≥ tolerance ≥ capacity ≥ regulatory, read in the direction."""
    violations: list[OrderingViolation] = []
    if not at_least_as_strict(direction, thresholds.appetite, thresholds.tolerance):
        violations.append(OrderingViolation.APPETITE_WEAKER_THAN_TOLERANCE)
    if not at_least_as_strict(direction, thresholds.tolerance, thresholds.capacity):
        violations.append(OrderingViolation.TOLERANCE_WEAKER_THAN_CAPACITY)
    if reference is not None:
        if reference.direction is not direction:
            violations.append(OrderingViolation.DIRECTION_MISMATCH)
        elif not at_least_as_strict(direction, thresholds.capacity, reference.value):
            violations.append(OrderingViolation.CAPACITY_WEAKER_THAN_REGULATORY)
    return tuple(violations)


@dataclass(frozen=True)
class AppetiteEvaluation:
    status: AppetiteStatus
    rag: Rag
    #: 100 = exactly at tolerance; above 100 = beyond it, either direction.
    utilisation_pct: Decimal | None
    headroom_to_appetite: Decimal | None
    headroom_to_tolerance: Decimal | None
    headroom_to_capacity: Decimal | None
    headroom_to_regulatory: Decimal | None
    trend: Trend
    #: True when no governed floor exists for this metric (D-036).
    regulatory_reference_absent: bool


def _headroom(direction: Direction, value: Decimal, level: Decimal) -> Decimal:
    """Signed distance to a level: positive is the safe side."""
    return value - level if direction is Direction.FLOOR else level - value


def _utilisation(direction: Direction, value: Decimal, tolerance: Decimal) -> Decimal | None:
    if direction is Direction.FLOOR:
        if value <= ZERO:
            return None
        return ratio(tolerance * HUNDRED / value)
    if tolerance <= ZERO:
        return None
    return ratio(value * HUNDRED / tolerance)


def _trend(direction: Direction, value: Decimal, prior: Decimal | None) -> Trend:
    if prior is None:
        return "unknown"
    if value == prior:
        return "stable"
    improving = value > prior if direction is Direction.FLOOR else value < prior
    return "improving" if improving else "deteriorating"


def evaluate(
    direction: Direction,
    thresholds: Thresholds,
    value: Decimal | None,
    reference: RegulatoryReference | None = None,
    prior: Decimal | None = None,
) -> AppetiteEvaluation:
    """Where the current reading sits on the ladder."""
    if value is None:
        return AppetiteEvaluation(
            status=AppetiteStatus.NOT_EVALUABLE,
            rag=RAG[AppetiteStatus.NOT_EVALUABLE],
            utilisation_pct=None,
            headroom_to_appetite=None,
            headroom_to_tolerance=None,
            headroom_to_capacity=None,
            headroom_to_regulatory=None,
            trend="unknown",
            regulatory_reference_absent=reference is None,
        )

    breach = reference is not None and not at_least_as_strict(direction, value, reference.value)
    if breach:
        status = AppetiteStatus.REGULATORY_BREACH
    elif at_least_as_strict(direction, value, thresholds.appetite):
        status = AppetiteStatus.WITHIN_APPETITE
    elif at_least_as_strict(direction, value, thresholds.tolerance):
        status = AppetiteStatus.APPETITE_EXCEEDED
    elif at_least_as_strict(direction, value, thresholds.capacity):
        status = AppetiteStatus.TOLERANCE_EXCEEDED
    else:
        status = AppetiteStatus.CAPACITY_EXCEEDED

    return AppetiteEvaluation(
        status=status,
        rag=RAG[status],
        utilisation_pct=_utilisation(direction, value, thresholds.tolerance),
        headroom_to_appetite=_headroom(direction, value, thresholds.appetite),
        headroom_to_tolerance=_headroom(direction, value, thresholds.tolerance),
        headroom_to_capacity=_headroom(direction, value, thresholds.capacity),
        headroom_to_regulatory=(
            None if reference is None else _headroom(direction, value, reference.value)
        ),
        trend=_trend(direction, value, prior),
        regulatory_reference_absent=reference is None,
    )


__all__ = [
    "ALLOWED_REFERENCE_CODES",
    "APPETITE_METRIC_CATALOGUE",
    "RAG",
    "UNIT_INDEX",
    "UNIT_PCT",
    "AppetiteEvaluation",
    "AppetiteStatus",
    "MetricDef",
    "OrderingViolation",
    "Rag",
    "RegulatoryReference",
    "Thresholds",
    "Trend",
    "at_least_as_strict",
    "evaluate",
    "validate_ordering",
]
