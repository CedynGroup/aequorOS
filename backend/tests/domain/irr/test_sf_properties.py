"""Properties the Standardised Framework must hold for every book, not just seven.

The golden vectors pin the arithmetic on books small enough to check by hand.
These pin the structure on books nobody wrote down: that gains never subsidise
losses, that the measure cannot be reduced by adding a currency that only
gains, that behavioural redistribution conserves money, that the answer does
not depend on the order rows arrived in, and that nothing about the engine
knows which currency it is looking at.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.domain.irr import standardised_params as sp
from app.domain.irr.standardised import (
    CurrencyInputs,
    Ladder,
    SfInputs,
    SfResult,
    apply_prepayment,
    apply_redemption,
    behavioural_rate,
)
from app.domain.irr.standardised import run as sf_run
from tests.domain.irr.test_sf_fixtures import (
    seed_rows,
    sf_parameters,
)
from tests.irrbb.testing.assertions import (
    assert_irrbb_invariants,
    assert_irrbb_scaled,
    assert_principal_conserved,
)
from tests.irrbb.testing.generators import (
    amounts,
    behavioural_rates,
    currency_inputs,
    ladder,
    zero_rates,
)

AS_OF = date(2026, 12, 31)
PARAMS = sf_parameters()
ZERO = Decimal(0)
_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_AMOUNTS = amounts
_RATES = behavioural_rates
_ZERO_RATES = zero_rates
_ladder = ladder


_currency = currency_inputs


def _run(
    ladders: tuple[Ladder, ...], currencies: tuple[CurrencyInputs, ...], tier1: Decimal
) -> SfResult:
    return sf_run(
        SfInputs(
            as_of=AS_OF,
            reporting_currency="GHS",
            ladders=ladders,
            nmds=(),
            currencies=currencies,
            tier1=tier1,
        ),
        PARAMS,
    )


# --- aggregation -------------------------------------------------------------


def test_a_book_with_no_positions_has_no_risk() -> None:
    result = _run((), (_currency("GHS", Decimal("0.1"), Decimal(1000)),), Decimal(500))

    assert result.measures.all_scenarios.measure == ZERO
    assert result.measures.all_scenarios.worst_scenario is None
    assert result.pct_tier1 == ZERO
    assert result.outlier is False
    assert all(row.loss == ZERO for row in result.scenarios)


@given(amounts=_AMOUNTS, rate=_ZERO_RATES, tier1=st.integers(min_value=1, max_value=10**6))
@_SETTINGS
def test_the_measure_is_never_negative_and_never_below_a_single_currencys_loss(
    amounts: list[Decimal], rate: Decimal, tier1: int
) -> None:
    result = _run(
        (_ladder("GHS", amounts),), (_currency("GHS", rate, Decimal(1000)),), Decimal(tier1)
    )

    assert_irrbb_invariants(result)


@given(amounts=_AMOUNTS, scale=st.integers(min_value=2, max_value=9))
@_SETTINGS
def test_delta_eve_scales_with_the_book(amounts: list[Decimal], scale: int) -> None:
    currencies = (_currency("GHS", Decimal("0.1"), Decimal(1000)),)
    base = _run((_ladder("GHS", amounts),), currencies, Decimal(10**6))
    scaled = _run(
        (_ladder("GHS", [amount * scale for amount in amounts]),), currencies, Decimal(10**6)
    )

    assert_irrbb_scaled(scaled, base, scale)


@given(amounts=_AMOUNTS, other=_AMOUNTS, rate=_ZERO_RATES)
@_SETTINGS
def test_a_currency_that_gains_adds_nothing_to_that_scenarios_loss(
    amounts: list[Decimal], other: list[Decimal], rate: Decimal
) -> None:
    """Gains weigh zero, so a second currency can only ever add to the loss.

    Netting the two would let a windfall in one currency pay for a shortfall in
    another, which is the arithmetic the framework's aggregation rule exists to
    forbid — and the reason a bank cannot hedge its reported measure by holding
    an offsetting foreign book it could not actually realise.
    """
    ghs = _currency("GHS", Decimal("0.1"), Decimal(1000))
    usd = _currency("USD", rate, Decimal(1000))

    base = _run((_ladder("GHS", amounts),), (ghs,), Decimal(10**6))
    combined = _run((_ladder("GHS", amounts), _ladder("USD", other)), (ghs, usd), Decimal(10**6))

    by_code = {row.code: row for row in base.scenarios}
    for scenario in combined.scenarios:
        added = next(row for row in scenario.by_currency if row.currency == "USD")
        before = by_code[scenario.code].loss
        assert scenario.loss >= before, scenario.code
        if added.delta_eve_reporting <= ZERO:
            assert scenario.loss == before, scenario.code
        else:
            assert scenario.loss == before + added.delta_eve_reporting, scenario.code
    assert combined.measures.all_scenarios.measure >= base.measures.all_scenarios.measure


@given(amounts=_AMOUNTS, other=_AMOUNTS, seed=st.integers(min_value=0, max_value=10**6))
@_SETTINGS
def test_the_answer_does_not_depend_on_the_order_rows_arrived_in(
    amounts: list[Decimal], other: list[Decimal], seed: int
) -> None:
    ghs = _currency("GHS", Decimal("0.1"), Decimal(1000))
    usd = _currency("USD", Decimal("0.05"), Decimal(1000))
    ladders = [_ladder("GHS", amounts), _ladder("USD", other), _ladder("GHS", other)]
    shuffled = list(ladders)
    random.Random(seed).shuffle(shuffled)

    first = _run(tuple(ladders), (ghs, usd), Decimal(10**6))
    second = _run(tuple(shuffled), (usd, ghs), Decimal(10**6))

    assert first.measures.all_scenarios.measure == second.measures.all_scenarios.measure
    assert {row.code: row.loss for row in first.scenarios} == {
        row.code: row.loss for row in second.scenarios
    }


@given(
    amounts=_AMOUNTS,
    lower=st.integers(min_value=1, max_value=10**5),
    extra=st.integers(min_value=1, max_value=10**5),
)
@_SETTINGS
def test_more_capital_never_turns_a_bank_into_an_outlier(
    amounts: list[Decimal], lower: int, extra: int
) -> None:
    currencies = (_currency("GHS", Decimal("0.1"), Decimal(1000)),)
    ladders = (_ladder("GHS", amounts),)

    weaker = _run(ladders, currencies, Decimal(lower))
    stronger = _run(ladders, currencies, Decimal(lower + extra))

    assert stronger.pct_tier1 <= weaker.pct_tier1
    assert not (stronger.outlier and not weaker.outlier)


@given(amounts=_AMOUNTS, rate=_ZERO_RATES)
@_SETTINGS
def test_the_engine_does_not_know_which_currency_it_is_looking_at(
    amounts: list[Decimal], rate: Decimal
) -> None:
    """Currency identity is data. A relabelled book with the same calibration
    must produce the same numbers, or a jurisdiction is hiding in the code."""
    calibration = {
        code: sp.GovernedValue(
            param_code=code,
            value_json={"schema": "irrbb-sf-currency-bp-v1", "XTS": value["GHS"], "OTHER": "1"},
            unit="bps",
            source_citation="test",
        )
        for code, value in (
            (sp.CODE_PARALLEL_SHOCK_BP, {"GHS": "450"}),
            (sp.CODE_SHORT_SHOCK_BP, {"GHS": "500"}),
            (sp.CODE_LONG_SHOCK_BP, {"GHS": "300"}),
        )
    }
    relabelled = sp.parse_parameters({**seed_rows(), **calibration})

    native = _run(
        (_ladder("GHS", amounts),), (_currency("GHS", rate, Decimal(1000)),), Decimal(10**6)
    )
    foreign = sf_run(
        SfInputs(
            as_of=AS_OF,
            reporting_currency="XTS",
            ladders=(_ladder("XTS", amounts),),
            nmds=(),
            currencies=(_currency("XTS", rate, Decimal(1000)),),
            tier1=Decimal(10**6),
        ),
        relabelled,
    )

    assert {row.code: row.loss for row in native.scenarios} == {
        row.code: row.loss for row in foreign.scenarios
    }


# --- behavioural conservation ------------------------------------------------


@given(amounts=_AMOUNTS, rate=_RATES)
@_SETTINGS
def test_prepayment_moves_money_without_creating_or_destroying_it(
    amounts: list[Decimal], rate: Decimal
) -> None:
    ladder = _ladder("GHS", amounts, kind="prepayable")

    principal, _ = apply_prepayment(ladder, rate, PARAMS)

    total = sum(principal, ZERO)
    opening = ladder.opening_outstanding
    assert_principal_conserved(total, opening, operation="behavioural redistribution")


@given(amounts=_AMOUNTS, rate=_RATES)
@_SETTINGS
def test_early_redemption_splits_a_balance_without_changing_it(
    amounts: list[Decimal], rate: Decimal
) -> None:
    ladder = _ladder("GHS", amounts, kind="td_retail_redeemable")

    principal, _ = apply_redemption(ladder, rate)

    total = sum(principal, ZERO)
    opening = ladder.opening_outstanding
    assert_principal_conserved(total, opening, operation="behavioural redistribution")


@given(
    rate=_RATES,
    multiplier=st.integers(min_value=0, max_value=500).map(lambda pct: Decimal(pct) / Decimal(100)),
)
@_SETTINGS
def test_a_behavioural_rate_never_exceeds_full_participation(
    rate: Decimal, multiplier: Decimal
) -> None:
    scaled = behavioural_rate(rate, multiplier)

    assert ZERO <= scaled <= Decimal(1)
    assert scaled <= rate * multiplier
