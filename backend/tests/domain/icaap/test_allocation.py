"""Allocation: the parts add back up to the whole, whatever the order."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.allocation import AllocationError, Driver, allocate
from app.domain.icaap.units import AMOUNT_QUANTUM

LINE = "credit"


def equal_drivers(*units: str) -> list[Driver]:
    return [Driver(unit, LINE, "rwa_share", Decimal(1)) for unit in units]


def test_the_residual_quantum_goes_to_the_largest_remainder() -> None:
    result = allocate({LINE: Decimal(100)}, equal_drivers("a", "b", "c"))
    assert result.amounts[("a", LINE)] == Decimal("33.3334")
    assert result.amounts[("b", LINE)] == Decimal("33.3333")
    assert result.amounts[("c", LINE)] == Decimal("33.3333")
    assert result.line_totals[LINE] == Decimal(100)


def test_the_answer_does_not_depend_on_the_order_units_were_entered() -> None:
    forward = allocate({LINE: Decimal(100)}, equal_drivers("a", "b", "c"))
    backward = allocate({LINE: Decimal(100)}, equal_drivers("c", "b", "a"))
    assert forward.amounts == backward.amounts


def test_weights_split_proportionally() -> None:
    drivers = [
        Driver("retail", LINE, "exposure_share", Decimal(75)),
        Driver("corporate", LINE, "exposure_share", Decimal(25)),
    ]
    result = allocate({LINE: Decimal(200)}, drivers)
    assert result.amounts[("retail", LINE)] == Decimal(150)
    assert result.amounts[("corporate", LINE)] == Decimal(50)
    assert result.unit_totals["retail"] == Decimal(150)


def test_manual_percentages_must_total_one_hundred() -> None:
    drivers = [
        Driver("a", LINE, "manual_pct", Decimal(60)),
        Driver("b", LINE, "manual_pct", Decimal(30)),
    ]
    with pytest.raises(AllocationError) as error:
        allocate({LINE: Decimal(100)}, drivers)
    assert error.value.code == "manual_pct_not_hundred"


@pytest.mark.parametrize(
    ("drivers", "totals", "code"),
    [
        (
            [Driver("a", LINE, "rwa_share", Decimal(0))],
            {LINE: Decimal(100)},
            "drivers_all_zero",
        ),
        (
            [Driver("a", LINE, "rwa_share", Decimal(-1))],
            {LINE: Decimal(100)},
            "negative_driver",
        ),
        (
            [
                Driver("a", LINE, "rwa_share", Decimal(50)),
                Driver("b", LINE, "manual_pct", Decimal(50)),
            ],
            {LINE: Decimal(100)},
            "mixed_driver_kinds",
        ),
        (
            [Driver("a", "market", "rwa_share", Decimal(1))],
            {LINE: Decimal(100)},
            "unknown_line",
        ),
        (
            [Driver("a", LINE, "rwa_share", Decimal(1))],
            {LINE: Decimal(-100)},
            "negative_total",
        ),
    ],
)
def test_an_allocation_that_would_not_add_up_is_refused(
    drivers: list[Driver], totals: dict[str, Decimal], code: str
) -> None:
    with pytest.raises(AllocationError) as error:
        allocate(totals, drivers)
    assert error.value.code == code


@settings(max_examples=300, deadline=None)
@given(
    total=st.integers(min_value=0, max_value=10**7),
    weights=st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=12),
)
def test_the_parts_sum_to_the_whole_and_none_is_negative(total: int, weights: list[int]) -> None:
    if sum(weights) == 0:
        return
    drivers = [
        Driver(f"unit-{index}", LINE, "rwa_share", Decimal(weight))
        for index, weight in enumerate(weights)
    ]
    result = allocate({LINE: Decimal(total)}, drivers)
    amounts = [result.amounts[(driver.unit_key, LINE)] for driver in drivers]
    assert sum(amounts) == Decimal(total)
    assert all(value >= Decimal(0) for value in amounts)


@settings(max_examples=200, deadline=None)
@given(
    total=st.integers(min_value=1, max_value=10**6),
    weights=st.lists(st.integers(min_value=1, max_value=1000), min_size=1, max_size=8),
)
def test_each_part_is_within_a_quantum_of_its_exact_share(total: int, weights: list[int]) -> None:
    drivers = [
        Driver(f"unit-{index}", LINE, "rwa_share", Decimal(weight))
        for index, weight in enumerate(weights)
    ]
    result = allocate({LINE: Decimal(total)}, drivers)
    weight_total = Decimal(sum(weights))
    for driver in drivers:
        exact = Decimal(total) * driver.value / weight_total
        assert abs(result.amounts[(driver.unit_key, LINE)] - exact) <= AMOUNT_QUANTUM
