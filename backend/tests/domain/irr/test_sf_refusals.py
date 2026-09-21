"""What the Standardised Framework refuses to compute, and why that is correct.

A framework run is a filed regulatory number. Where the engine cannot honestly
produce one, it must say so rather than produce a smaller one: an understated
loss looks exactly like a good result, and nothing downstream can tell the
difference. So each of these is a typed refusal with the facts attached.

The option refusal is the sharpest of them (DV-010). Automatic interest-rate
options — caps, floors, collars, swaptions — carry value this engine cannot
compute. Pricing the rest of the book and calling the answer "the Standardised
Framework measure" would understate ΔEVE by exactly the option value, and the
bank would file it. Refusing means such a book cannot use the framework until
the valuation is built, which is visible, arguable and fixable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.irr.standardised import (
    K_AO_ZERO_STATEMENT,
    POST_SHOCK_FLOOR,
    AutomaticOption,
    CurrencyInputs,
    SfInputError,
    SfInputs,
    SfOptionsUnsupportedError,
)
from app.domain.irr.standardised import run as sf_run
from tests.domain.irr.test_sf_fixtures import flat_curve, sf_parameters, simple_ladder

AS_OF = date(2026, 12, 31)
PARAMS = sf_parameters()

OPTIONS = (
    AutomaticOption(ref="CAP-1", currency="GHS", option_type="cap", notional=Decimal(5000)),
    AutomaticOption(
        ref="SWO-1", currency="GHS", option_type="swaption", notional=Decimal(2500)
    ),
)


def _inputs(**overrides: object) -> SfInputs:
    base = {
        "as_of": AS_OF,
        "reporting_currency": "GHS",
        "ladders": (simple_ladder(PARAMS, "GHS", {1: "-1000", 11: "1000"}),),
        "nmds": (),
        "currencies": (
            CurrencyInputs(
                currency="GHS",
                zero_cc=flat_curve("0.10", PARAMS),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(1000),
                bb_liabilities_rep=Decimal(1000),
            ),
        ),
        "tier1": Decimal(700),
    }
    base.update(overrides)
    return SfInputs(**base)  # type: ignore[arg-type]


def test_an_option_book_refuses_rather_than_understating_the_loss() -> None:
    with pytest.raises(SfOptionsUnsupportedError) as caught:
        sf_run(_inputs(automatic_options=OPTIONS), PARAMS)

    assert caught.value.code == "irrbb_sf_options_unsupported"
    assert caught.value.detail["count"] == 2
    assert caught.value.detail["notional"] == "7500.000000"
    assert caught.value.detail["option_types"] == ["cap", "swaption"]


def test_the_option_refusal_has_exactly_one_name() -> None:
    """D-061: the run row records the same name the exception carries.

    The design once gave this condition a second spelling for the run row
    (``automatic_options_not_modelled``). The lead retired it, so this pins the
    ABSENCE of the alias as well as the presence of the name — an alias that
    came back would pass a test asserting only the primary name.
    """
    with pytest.raises(SfOptionsUnsupportedError) as caught:
        sf_run(_inputs(automatic_options=OPTIONS), PARAMS)

    assert caught.value.code == "irrbb_sf_options_unsupported"
    assert SfOptionsUnsupportedError.CODE == "irrbb_sf_options_unsupported"
    assert not hasattr(SfOptionsUnsupportedError, "SERVICE_ERROR_CODE")
    assert "service_error_code" not in caught.value.detail
    assert "automatic_options_not_modelled" not in str(caught.value.detail)


def test_the_option_refusal_comes_before_every_other_check() -> None:
    """An option book is refused even when something else is also wrong."""
    with pytest.raises(SfOptionsUnsupportedError):
        sf_run(_inputs(automatic_options=OPTIONS, tier1=Decimal(0)), PARAMS)


def test_a_book_without_options_states_that_the_add_on_is_zero() -> None:
    """Silence would read as "no options" and as "not modelled" alike."""
    result = sf_run(_inputs(), PARAMS)

    assert result.k_ao_statement == K_AO_ZERO_STATEMENT
    assert "zero" in result.k_ao_statement
    for scenario in result.scenarios:
        assert all(row.k_ao_native == Decimal("0.000000") for row in scenario.by_currency)


def test_a_declared_option_add_on_lands_in_delta_eve() -> None:
    """The seam the valuation will fill: an add-on supplied is an add-on used."""
    with_add_on = sf_run(_inputs(kao={("parallel_up", "GHS"): Decimal(25)}), PARAMS)
    without = sf_run(_inputs(), PARAMS)

    up_with = next(r for r in with_add_on.scenarios if r.code == "parallel_up")
    up_without = next(r for r in without.scenarios if r.code == "parallel_up")
    assert up_with.by_currency[0].k_ao_native == Decimal("25.000000")
    assert (
        up_with.by_currency[0].delta_eve_native
        == up_without.by_currency[0].delta_eve_native + Decimal(25)
    )


def test_no_tier_one_means_no_outlier_test() -> None:
    with pytest.raises(SfInputError) as caught:
        sf_run(_inputs(tier1=Decimal(0)), PARAMS)

    assert caught.value.code == "tier1_unavailable"


def test_a_material_currency_without_a_curve_refuses() -> None:
    inputs = _inputs(
        currencies=(
            CurrencyInputs(
                currency="GHS",
                zero_cc=(),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(1000),
                bb_liabilities_rep=Decimal(1000),
            ),
        )
    )

    with pytest.raises(SfInputError) as caught:
        sf_run(inputs, PARAMS)

    assert caught.value.code == "missing_curve"
    assert caught.value.detail["currency"] == "GHS"


def test_an_immaterial_currency_needs_no_curve() -> None:
    """It is excluded from the measure, so there is nothing to discount."""
    inputs = _inputs(
        currencies=(
            CurrencyInputs(
                currency="GHS",
                zero_cc=flat_curve("0.10", PARAMS),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(1000),
                bb_liabilities_rep=Decimal(1000),
            ),
            CurrencyInputs(
                currency="EUR",
                zero_cc=(),
                fx_to_reporting=Decimal(12),
                bb_assets_rep=Decimal(1),
                bb_liabilities_rep=Decimal(1),
            ),
        )
    )

    result = sf_run(inputs, PARAMS)

    assert result.excluded_currencies == ("EUR",)


def test_the_absence_of_a_post_shock_floor_is_reported_not_assumed() -> None:
    """The engine reports that it applied no floor, as a code on the result.

    The code is a wire/DB key and stays stable; what it MEANS to a reader is
    the service's sentence, and that sentence was corrected on 2026-09-20 — it
    used to tell the filer that the framework prescribes no floor, which is a
    claim about a supervisory standard the platform cannot support. Pinned in
    ``tests/services/test_regulatory_irr_sf.py`` and at the route.
    """
    result = sf_run(_inputs(), PARAMS)

    assert result.post_shock_floor == POST_SHOCK_FLOOR
    assert POST_SHOCK_FLOOR == "not_prescribed"
