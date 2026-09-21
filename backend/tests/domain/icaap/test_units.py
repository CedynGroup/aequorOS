"""One conversion, four bases, and a refusal when the denominator is absent."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.units import (
    AMOUNT_QUANTUM,
    HUNDRED,
    RATIO_QUANTUM,
    Basis,
    Denominators,
    UnitConversionError,
    from_amount,
    pillar1_capital,
    to_amount,
)

FULL = Denominators(total_rwa=Decimal(1000), credit_rwa=Decimal(800), car_min_pct=Decimal(13))


def test_the_design_goldens_convert_exactly() -> None:
    assert to_amount(Basis.PCT_TOTAL_RWA, Decimal(2), FULL) == Decimal(20)
    assert to_amount(Basis.PCT_CREDIT_RWA, Decimal(2), FULL) == Decimal(16)
    assert to_amount(Basis.PCT_PILLAR1_CREDIT_CAPITAL, Decimal(10), FULL) == Decimal("10.4")
    assert to_amount(Basis.ABSOLUTE, Decimal("10.4"), Denominators()) == Decimal("10.4")


def test_pillar1_capital_applies_the_resolved_minimum_ratio() -> None:
    assert pillar1_capital(Decimal(800), Decimal(13)) == Decimal(104)


def test_from_amount_is_the_inverse() -> None:
    assert from_amount(Decimal(20), Basis.PCT_TOTAL_RWA, FULL) == Decimal(2)
    assert from_amount(Decimal("10.4"), Basis.PCT_TOTAL_RWA, FULL) == Decimal("1.04")
    assert from_amount(Decimal("10.4"), Basis.PCT_PILLAR1_CREDIT_CAPITAL, FULL) == Decimal(10)


@pytest.mark.parametrize(
    ("basis", "denominators", "denominator"),
    [
        (Basis.PCT_TOTAL_RWA, Denominators(), "total_rwa"),
        (Basis.PCT_CREDIT_RWA, Denominators(), "credit_rwa"),
        (Basis.PCT_PILLAR1_CREDIT_CAPITAL, Denominators(credit_rwa=Decimal(800)), "car_min_pct"),
    ],
)
def test_a_missing_denominator_is_a_typed_refusal(
    basis: Basis, denominators: Denominators, denominator: str
) -> None:
    """Never a substituted figure: the caller is told what it failed to bind."""
    with pytest.raises(UnitConversionError) as error:
        to_amount(basis, Decimal(2), denominators)
    assert error.value.code == "denominator_missing"
    assert error.value.denominator == denominator


def test_a_zero_denominator_is_not_a_conversion() -> None:
    with pytest.raises(UnitConversionError) as error:
        to_amount(Basis.PCT_TOTAL_RWA, Decimal(2), Denominators(total_rwa=Decimal(0)))
    assert error.value.code == "denominator_not_positive"


def test_a_negative_basis_value_is_refused() -> None:
    with pytest.raises(UnitConversionError) as error:
        to_amount(Basis.PCT_TOTAL_RWA, Decimal(-2), FULL)
    assert error.value.code == "negative_value"


@settings(max_examples=300, deadline=None)
@given(
    basis_value=st.decimals(
        min_value=Decimal(0), max_value=Decimal(100), places=4, allow_nan=False
    ),
    total_rwa=st.integers(min_value=1, max_value=10**9),
)
def test_a_conversion_is_exact_to_the_money_quantum(basis_value: Decimal, total_rwa: int) -> None:
    denominators = Denominators(total_rwa=Decimal(total_rwa))
    converted = to_amount(Basis.PCT_TOTAL_RWA, basis_value, denominators)
    exact = basis_value * Decimal(total_rwa) / HUNDRED
    assert abs(converted - exact) <= AMOUNT_QUANTUM


@settings(max_examples=300, deadline=None)
@given(
    basis_value=st.decimals(
        min_value=Decimal(0), max_value=Decimal(100), places=4, allow_nan=False
    ),
    total_rwa=st.integers(min_value=1000, max_value=10**6),
)
def test_a_percentage_survives_the_round_trip(basis_value: Decimal, total_rwa: int) -> None:
    """Within the two quanta the two directions are allowed to lose."""
    denominators = Denominators(total_rwa=Decimal(total_rwa))
    converted = to_amount(Basis.PCT_TOTAL_RWA, basis_value, denominators)
    back = from_amount(converted, Basis.PCT_TOTAL_RWA, denominators)
    tolerance = RATIO_QUANTUM + AMOUNT_QUANTUM * HUNDRED / Decimal(total_rwa)
    assert abs(back - basis_value) <= tolerance
