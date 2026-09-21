"""FX: which side of an open book loses, and by how much above Pillar 1."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.pillar2.fx import (
    APPRECIATION,
    DEPRECIATION,
    CurrencyPosition,
    FxShockSet,
    MissingShockError,
    fx_nop_addon,
    fx_revaluation_addon,
    revaluation_losses,
)
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from tests.domain.icaap.pillar2.conftest import CAR_MIN_PCT, FX_SHOCK_BODY

BOOK = [CurrencyPosition("USD", Decimal(100)), CurrencyPosition("EUR", Decimal(-40))]


def shocks() -> FxShockSet:
    return FxShockSet.from_payload(FX_SHOCK_BODY)


def test_the_reference_book_loses_on_depreciation_and_charges_the_excess() -> None:
    result = fx_nop_addon(
        positions=BOOK, shocks=shocks(), market_rwa=Decimal(50), car_min_pct=CAR_MIN_PCT
    )
    assert result.status is MethodStatus.COMPUTED
    assert result.detail["depreciation_loss"] == "12.0000"
    assert result.detail["appreciation_loss"] == "10.0000"
    assert result.detail["worst_direction"] == DEPRECIATION
    assert result.detail["pillar1_fx_capital"] == "6.5000"
    assert result.baseline_amount == Decimal("5.5")


def test_a_long_only_book_loses_only_when_the_reporting_currency_strengthens() -> None:
    loss = revaluation_losses([CurrencyPosition("USD", Decimal(100))], shocks())
    assert loss.depreciation_loss == Decimal(0)
    assert loss.appreciation_loss == Decimal(10)
    assert loss.worst_direction == APPRECIATION


def test_a_short_only_book_loses_the_other_way() -> None:
    loss = revaluation_losses([CurrencyPosition("EUR", Decimal(-40))], shocks())
    assert loss.depreciation_loss == Decimal(12)
    assert loss.appreciation_loss == Decimal(0)
    assert loss.worst_direction == DEPRECIATION


def test_losses_are_not_offset_across_currencies() -> None:
    """A gain on the long leg does not pay for the loss on the short one."""
    loss = revaluation_losses(BOOK, shocks())
    assert loss.depreciation_loss == Decimal(12)
    assert loss.by_currency[("USD", DEPRECIATION)] == Decimal(0)


def test_a_per_currency_shock_beats_the_default() -> None:
    table = FxShockSet.from_payload(
        {"depreciation": {"default": 30, "EUR": 50}, "appreciation": {"default": 10}}
    )
    loss = revaluation_losses([CurrencyPosition("EUR", Decimal(-40))], table)
    assert loss.depreciation_loss == Decimal(20)


def test_a_currency_with_no_governed_shock_is_incomplete_not_zero() -> None:
    table = FxShockSet(depreciation_pct={}, appreciation_pct={})
    with pytest.raises(MissingShockError):
        revaluation_losses(BOOK, table)
    result = fx_nop_addon(
        positions=BOOK, shocks=table, market_rwa=Decimal(50), car_min_pct=CAR_MIN_PCT
    )
    assert result.status is MethodStatus.INCOMPLETE
    assert result.reasons == ("fx_shock_missing:USD:depreciation",)
    assert result.baseline_amount is None


def test_a_stress_scenario_shocks_one_way_only() -> None:
    one_way = FxShockSet.single_direction(DEPRECIATION, Decimal(30))
    loss = revaluation_losses(BOOK, one_way)
    assert loss.appreciation_loss is None
    assert loss.worst_loss == Decimal(12)


def test_the_add_on_is_never_negative() -> None:
    covered = fx_revaluation_addon(BOOK, shocks(), Decimal(1000))
    assert covered.addon == Decimal(0)


def test_the_stressed_column_takes_the_worse_of_the_two() -> None:
    result = fx_nop_addon(
        positions=BOOK,
        shocks=shocks(),
        market_rwa=Decimal(50),
        car_min_pct=CAR_MIN_PCT,
        overlay_stressed_addon=Decimal(9),
    )
    assert result.stressed_amount == Decimal(9)
    assert result.stressed_derivation == "max_of_baseline_and_scenario"


@pytest.mark.parametrize(
    ("shock_set", "car_min", "expected"),
    [(None, CAR_MIN_PCT, "fx_p2_shock_pct"), (FX_SHOCK_BODY, None, "car_min")],
)
def test_an_unresolved_parameter_is_a_typed_refusal(
    shock_set: dict[str, object] | None, car_min: Decimal | None, expected: str
) -> None:
    with pytest.raises(MissingParameter) as error:
        fx_nop_addon(
            positions=BOOK,
            shocks=None if shock_set is None else FxShockSet.from_payload(shock_set),
            market_rwa=Decimal(50),
            car_min_pct=car_min,
        )
    assert error.value.param_code == expected


@settings(max_examples=300, deadline=None)
@given(
    net=st.decimals(min_value=Decimal(-1000), max_value=Decimal(1000), places=2),
    low=st.integers(min_value=0, max_value=50),
    extra=st.integers(min_value=0, max_value=50),
)
def test_a_bigger_shock_never_produces_a_smaller_loss(net: Decimal, low: int, extra: int) -> None:
    position = [CurrencyPosition("USD", net)]
    smaller = revaluation_losses(
        position,
        FxShockSet(
            depreciation_pct={},
            appreciation_pct={},
            default_depreciation_pct=Decimal(low),
            default_appreciation_pct=Decimal(low),
        ),
    )
    larger = revaluation_losses(
        position,
        FxShockSet(
            depreciation_pct={},
            appreciation_pct={},
            default_depreciation_pct=Decimal(low + extra),
            default_appreciation_pct=Decimal(low + extra),
        ),
    )
    assert larger.worst_loss >= smaller.worst_loss >= Decimal(0)
