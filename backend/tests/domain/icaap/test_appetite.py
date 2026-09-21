"""Appetite: the same ladder read in two directions, and never a code default."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.appetite import (
    APPETITE_METRIC_CATALOGUE,
    RAG,
    AppetiteStatus,
    OrderingViolation,
    RegulatoryReference,
    Thresholds,
    at_least_as_strict,
    evaluate,
    validate_ordering,
)
from app.domain.policy import Direction
from app.domain.policy.resolver import PARAMETER_DIRECTION

CAR = Thresholds(appetite=Decimal(16), tolerance=Decimal("14.5"), capacity=Decimal(13))
CAR_REFERENCE = RegulatoryReference("car_min", Decimal(13), Direction.FLOOR, "pending")
NPL = Thresholds(appetite=Decimal(5), tolerance=Decimal(7), capacity=Decimal(10))
NPL_REFERENCE = RegulatoryReference("npl_limit_pct", Decimal(10), Direction.CEILING, "pending")


def test_equality_with_the_regulatory_value_is_allowed() -> None:
    assert validate_ordering(Direction.FLOOR, CAR, CAR_REFERENCE) == ()
    assert validate_ordering(Direction.CEILING, NPL, NPL_REFERENCE) == ()
    assert at_least_as_strict(Direction.FLOOR, Decimal(13), Decimal(13))


def test_a_capacity_weaker_than_the_regulatory_value_is_refused_in_both_directions() -> None:
    weak_floor = Thresholds(Decimal(16), Decimal("14.5"), Decimal("12.5"))
    assert validate_ordering(Direction.FLOOR, weak_floor, CAR_REFERENCE) == (
        OrderingViolation.CAPACITY_WEAKER_THAN_REGULATORY,
    )
    weak_ceiling = Thresholds(Decimal(5), Decimal(7), Decimal(11))
    assert validate_ordering(Direction.CEILING, weak_ceiling, NPL_REFERENCE) == (
        OrderingViolation.CAPACITY_WEAKER_THAN_REGULATORY,
    )


def test_a_ladder_out_of_order_is_reported_level_by_level() -> None:
    inverted = Thresholds(appetite=Decimal(13), tolerance=Decimal(14), capacity=Decimal(15))
    violations = validate_ordering(Direction.FLOOR, inverted, None)
    assert OrderingViolation.APPETITE_WEAKER_THAN_TOLERANCE in violations
    assert OrderingViolation.TOLERANCE_WEAKER_THAN_CAPACITY in violations


def test_a_reference_pointing_the_other_way_is_a_direction_mismatch() -> None:
    assert validate_ordering(Direction.CEILING, NPL, CAR_REFERENCE) == (
        OrderingViolation.DIRECTION_MISMATCH,
    )


@pytest.mark.parametrize(
    ("value", "status", "rag"),
    [
        (Decimal(17), AppetiteStatus.WITHIN_APPETITE, "green"),
        (Decimal(15), AppetiteStatus.APPETITE_EXCEEDED, "amber"),
        (Decimal(14), AppetiteStatus.TOLERANCE_EXCEEDED, "red"),
        (Decimal("12.9"), AppetiteStatus.REGULATORY_BREACH, "black"),
    ],
)
def test_the_floor_ladder(value: Decimal, status: AppetiteStatus, rag: str) -> None:
    evaluation = evaluate(Direction.FLOOR, CAR, value, CAR_REFERENCE)
    assert evaluation.status is status
    assert evaluation.rag == rag


def test_capacity_exceeded_is_distinct_from_a_regulatory_breach() -> None:
    """With no governed floor the ladder still ends, it just ends differently."""
    evaluation = evaluate(Direction.FLOOR, CAR, Decimal(12), None)
    assert evaluation.status is AppetiteStatus.CAPACITY_EXCEEDED
    assert evaluation.regulatory_reference_absent


def test_the_ceiling_ladder_mirrors_every_inequality() -> None:
    assert evaluate(Direction.CEILING, NPL, Decimal(4), NPL_REFERENCE).status is (
        AppetiteStatus.WITHIN_APPETITE
    )
    assert evaluate(Direction.CEILING, NPL, Decimal(6), NPL_REFERENCE).status is (
        AppetiteStatus.APPETITE_EXCEEDED
    )
    assert evaluate(Direction.CEILING, NPL, Decimal(8), NPL_REFERENCE).status is (
        AppetiteStatus.TOLERANCE_EXCEEDED
    )
    assert evaluate(Direction.CEILING, NPL, Decimal(11), NPL_REFERENCE).status is (
        AppetiteStatus.REGULATORY_BREACH
    )


def test_utilisation_is_one_hundred_at_tolerance_in_both_directions() -> None:
    assert evaluate(Direction.FLOOR, CAR, Decimal(15), CAR_REFERENCE).utilisation_pct == Decimal(
        "96.666667"
    )
    assert evaluate(
        Direction.FLOOR, CAR, Decimal("14.5"), CAR_REFERENCE
    ).utilisation_pct == Decimal(100)
    assert evaluate(Direction.CEILING, NPL, Decimal(8), NPL_REFERENCE).utilisation_pct == Decimal(
        "114.285714"
    )
    assert evaluate(Direction.CEILING, NPL, Decimal(7), NPL_REFERENCE).utilisation_pct == Decimal(
        100
    )


def test_headroom_is_positive_on_the_safe_side() -> None:
    floor_case = evaluate(Direction.FLOOR, CAR, Decimal(15), CAR_REFERENCE)
    assert floor_case.headroom_to_tolerance == Decimal("0.5")
    assert floor_case.headroom_to_appetite == Decimal(-1)
    assert floor_case.headroom_to_regulatory == Decimal(2)
    ceiling_case = evaluate(Direction.CEILING, NPL, Decimal(8), NPL_REFERENCE)
    assert ceiling_case.headroom_to_tolerance == Decimal(-1)
    assert ceiling_case.headroom_to_capacity == Decimal(2)


@pytest.mark.parametrize(
    ("direction", "value", "prior", "trend"),
    [
        (Direction.FLOOR, Decimal(15), Decimal(14), "improving"),
        (Direction.FLOOR, Decimal(14), Decimal(15), "deteriorating"),
        (Direction.FLOOR, Decimal(15), Decimal(15), "stable"),
        (Direction.CEILING, Decimal(6), Decimal(8), "improving"),
        (Direction.CEILING, Decimal(8), Decimal(6), "deteriorating"),
        (Direction.FLOOR, Decimal(15), None, "unknown"),
    ],
)
def test_the_trend_is_read_toward_the_safe_side(
    direction: Direction, value: Decimal, prior: Decimal | None, trend: str
) -> None:
    thresholds = CAR if direction is Direction.FLOOR else NPL
    assert evaluate(direction, thresholds, value, None, prior).trend == trend


def test_a_metric_with_no_reading_is_not_evaluable() -> None:
    evaluation = evaluate(Direction.FLOOR, CAR, None, CAR_REFERENCE)
    assert evaluation.status is AppetiteStatus.NOT_EVALUABLE
    assert evaluation.rag == "none"
    assert evaluation.utilisation_pct is None


def test_the_catalogue_agrees_with_the_control_plane_on_direction() -> None:
    """A metric whose direction contradicts the governed code is a trap."""
    for metric in APPETITE_METRIC_CATALOGUE.values():
        code = metric.regulatory_param_code
        if code is None or code not in PARAMETER_DIRECTION:
            continue
        assert metric.direction == PARAMETER_DIRECTION[code], metric.key


def test_every_status_has_a_colour() -> None:
    assert set(RAG) == set(AppetiteStatus)


@settings(max_examples=300, deadline=None)
@given(
    first=st.decimals(min_value=Decimal(0), max_value=Decimal(50), places=2),
    second=st.decimals(min_value=Decimal(0), max_value=Decimal(50), places=2),
)
def test_a_safer_reading_is_never_a_worse_status(first: Decimal, second: Decimal) -> None:
    severity = {
        AppetiteStatus.WITHIN_APPETITE: 0,
        AppetiteStatus.APPETITE_EXCEEDED: 1,
        AppetiteStatus.TOLERANCE_EXCEEDED: 2,
        AppetiteStatus.CAPACITY_EXCEEDED: 3,
        AppetiteStatus.REGULATORY_BREACH: 4,
    }
    low, high = sorted((first, second))
    floor_low = evaluate(Direction.FLOOR, CAR, low, CAR_REFERENCE)
    floor_high = evaluate(Direction.FLOOR, CAR, high, CAR_REFERENCE)
    assert severity[floor_high.status] <= severity[floor_low.status]
    ceiling_low = evaluate(Direction.CEILING, NPL, low, NPL_REFERENCE)
    ceiling_high = evaluate(Direction.CEILING, NPL, high, NPL_REFERENCE)
    assert severity[ceiling_low.status] <= severity[ceiling_high.status]
