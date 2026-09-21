"""Hand-checked golden vectors for the Standardised Framework (P5 design §3).

Every figure below was computed independently, to forty digits, from the
framework's own definitions, and is stated to six decimal places. The engine
must reproduce them exactly. These are not regression snapshots taken from the
engine's own output: if one of them moves, the engine is wrong until the
arithmetic says otherwise.

The vectors are deliberately small enough to check by hand:

* SF-1/SF-2 — one currency, two cash flows. The outlier test turns on the
  parallel shock's size, which is the whole reason the framework's own
  calibration cannot be swapped for a smaller one.
* SF-2b — a barbell that gains under both parallel shocks, proving the measure
  floors at zero and that the governed scenario SET changes the answer.
* SF-3 — the shock shapes themselves.
* SF-4 — two material currencies and one immaterial one: gains weigh zero, so
  the measure is larger than the naive net, not smaller.
* SF-6 — the earnings measure.
* SF-7 — the same arithmetic reached from instrument terms rather than a
  hand-written ladder.
* SF-8 — the calibration changes in the control plane and the answer follows,
  with no code change (D-024).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

import pytest

from app.domain.irr import standardised_params as sp
from app.domain.irr.standardised import (
    CurrencyInputs,
    Ladder,
    NmdBalance,
    SfInputs,
    SfResult,
    scenario_shifts_bp,
    short_weight,
)
from app.domain.irr.standardised import run as sf_run
from app.domain.irr.standardised_cash_flows import InstrumentTerms
from tests.domain.irr.test_sf_fixtures import (
    flat_curve,
    ladder_from_instruments,
    running_outstanding,
    sf_parameters,
    simple_ladder,
)

AS_OF = date(2026, 12, 31)
SIX = Decimal("0.000001")


def q(value: Decimal) -> Decimal:
    return value.quantize(SIX, rounding=ROUND_HALF_UP)


def _ghs_currency(params: sp.SfParameters) -> CurrencyInputs:
    return CurrencyInputs(
        currency="GHS",
        zero_cc=flat_curve("0.10", params),
        fx_to_reporting=Decimal(1),
        bb_assets_rep=Decimal(1000),
        bb_liabilities_rep=Decimal(1000),
    )


def _run_single(params: sp.SfParameters, ladder: Ladder, tier1: str) -> SfResult:
    return sf_run(
        SfInputs(
            as_of=AS_OF,
            reporting_currency="GHS",
            ladders=(ladder,),
            nmds=(),
            currencies=(_ghs_currency(params),),
            tier1=Decimal(tier1),
        ),
        params,
    )


def _scenario(result: SfResult, code: str):
    return next(row for row in result.scenarios if row.code == code)


# --- SF-1 / SF-2 -------------------------------------------------------------

#: ΔEVE for the SF-1 ladder under each scenario. Positive is a loss.
SF1_DELTA_EVE: dict[str, str] = {
    "parallel_up": "116.759902",
    "parallel_down": "-142.996097",
    "steepener": "21.759660",
    "flattener": "2.264802",
    "short_up": "44.776382",
    "short_down": "-48.180155",
}
SF1_EVE_SCENARIO: dict[str, str] = {
    "parallel_up": "-478.851790",
    "parallel_down": "-219.095791",
    "steepener": "-383.851548",
    "flattener": "-364.356689",
    "short_up": "-406.868270",
    "short_down": "-313.911733",
}


def _sf1_ladder(params: sp.SfParameters) -> Ladder:
    return simple_ladder(params, "GHS", {1: "-1000", 11: "1000"})


def test_sf1_delta_eve_for_every_scenario() -> None:
    params = sf_parameters()

    result = _run_single(params, _sf1_ladder(params), "700")

    assert _scenario(result, "parallel_up").by_currency[0].eve_base_native == Decimal(
        "-362.091888"
    )
    for code, expected in SF1_DELTA_EVE.items():
        row = _scenario(result, code).by_currency[0]
        assert row.delta_eve_native == Decimal(expected), code
        assert row.eve_scenario_native == Decimal(SF1_EVE_SCENARIO[code]), code


def test_sf1_the_measure_is_the_worst_loss_and_450_trips_the_outlier() -> None:
    """The framework's own parallel shock is what makes this bank an outlier."""
    params = sf_parameters()

    result = _run_single(params, _sf1_ladder(params), "700")

    assert result.measures.all_scenarios.measure == Decimal("116.759902")
    assert result.measures.mandatory.measure == Decimal("116.759902")
    assert result.measures.outlier_set.measure == Decimal("116.759902")
    assert result.measures.outlier_set.worst_scenario == "parallel_up"
    assert result.pct_tier1 == Decimal("16.679986")
    assert result.outlier is True


def test_sf1_a_smaller_parallel_shock_would_not_have_flagged_it() -> None:
    """SF-8: the calibration is a console row, so the answer follows it."""
    params = sf_parameters(
        {
            sp.CODE_PARALLEL_SHOCK_BP: sp.GovernedValue(
                param_code=sp.CODE_PARALLEL_SHOCK_BP,
                value_json={"schema": "irrbb-sf-currency-bp-v1", "GHS": "200", "OTHER": "325"},
                unit="bps",
                source_citation="console generation",
                confirmation_status="approved",
            )
        }
    )

    result = _run_single(params, _sf1_ladder(params), "700")

    assert _scenario(result, "parallel_up").by_currency[0].delta_eve_native == Decimal(
        "54.823916"
    )
    assert _scenario(result, "parallel_down").by_currency[0].delta_eve_native == Decimal(
        "-59.992189"
    )
    assert result.measures.outlier_set.measure == Decimal("54.823916")
    assert result.pct_tier1 == Decimal("7.831988")
    assert result.outlier is False


def test_sf2_a_gain_is_not_a_breach() -> None:
    """The legacy engine's absolute rule would flag this bank on a GAIN.

    ΔEVE is -142.996097 under the parallel-down shock, which is 17.87% of an 800
    Tier 1 — above the threshold — but it is money the bank makes. The
    Standardised Framework measures the LOSS, so this bank is not an outlier
    (design decision D-013).
    """
    params = sf_parameters()

    result = _run_single(params, _sf1_ladder(params), "800")

    assert result.pct_tier1 == Decimal("14.594988")
    assert result.outlier is False
    losses = {row.code: row.loss for row in result.scenarios}
    assert losses["parallel_down"] == Decimal("0.000000")


# --- SF-2b -------------------------------------------------------------------


def _barbell_long_leg() -> Decimal:
    """The 25-year leg that makes the barbell duration-neutral."""
    with localcontext() as context:
        context.prec = 40
        short = Decimal(1000) * Decimal("0.0028") * (-Decimal("0.00028")).exp()
        mid = Decimal(1000) * Decimal("4.5") * (-Decimal("0.45")).exp()
        return (mid - short) / (Decimal(25) * (-Decimal("2.5")).exp())


def test_sf2b_a_book_that_gains_under_both_parallel_shocks() -> None:
    params = sf_parameters()
    long_leg = _barbell_long_leg()
    assert q(long_leg) == Decimal("1396.858142")
    principal = (
        Decimal(1000),
        *[Decimal(0)] * 9,
        Decimal(-1000),
        *[Decimal(0)] * 7,
        long_leg,
    )
    ladder = Ladder(
        currency="GHS",
        portfolio="barbell",
        kind="fixed",
        principal=principal,
        interest=tuple(Decimal(0) for _ in principal),
        outstanding_end=running_outstanding(principal),
    )

    result = _run_single(params, ladder, "700")

    assert _scenario(result, "parallel_up").by_currency[0].eve_base_native == Decimal(
        "476.752986"
    )
    assert _scenario(result, "parallel_up").by_currency[0].delta_eve_native == Decimal(
        "-39.323812"
    )
    assert _scenario(result, "parallel_down").by_currency[0].delta_eve_native == Decimal(
        "-95.523853"
    )
    assert _scenario(result, "steepener").by_currency[0].delta_eve_native == Decimal(
        "34.353121"
    )
    assert _scenario(result, "short_down").by_currency[0].delta_eve_native == Decimal(
        "47.903136"
    )
    # Both mandatory scenarios are gains, so that measure floors at zero — and
    # the six-scenario measure does not. Which set the outlier test runs over is
    # a governed choice, and this is the book that proves it matters.
    assert result.measures.mandatory.measure == Decimal("0.000000")
    assert result.measures.mandatory.worst_scenario is None
    assert result.measures.all_scenarios.measure == Decimal("47.903136")
    assert result.measures.all_scenarios.worst_scenario == "short_down"


# --- SF-3 --------------------------------------------------------------------

SF3_SHAPES: tuple[tuple[int, str, str, str, str, str], ...] = (
    (0, "0.999300", "499.650122", "0.209927", "-324.583646", "399.594142"),
    (1, "0.989629", "494.814576", "3.111254", "-318.829345", "393.984908"),
    (6, "0.731616", "365.807814", "80.515311", "-165.311299", "244.337065"),
    (10, "0.324652", "162.326234", "202.604260", "76.831782", "8.298431"),
    (16, "0.043937", "21.968467", "286.818920", "243.857524", "-154.516578"),
    (18, "0.001930", "0.965227", "299.420864", "268.851380", "-178.880337"),
)


@pytest.mark.parametrize(("index", "expected"), [(row[0], row[1:]) for row in SF3_SHAPES])
def test_sf3_shock_shapes(index: int, expected: tuple[str, ...]) -> None:
    weight, short, long, steepener, flattener = expected
    params = sf_parameters()
    midpoint = params.midpoints[index]

    up = scenario_shifts_bp(params, "GHS", "short_up")
    down = scenario_shifts_bp(params, "GHS", "short_down")
    steep = scenario_shifts_bp(params, "GHS", "steepener")
    flat = scenario_shifts_bp(params, "GHS", "flattener")
    # The long component is implied by the rotation the engine actually applied.
    implied_long = (steep[index] - params.rotation.steep_short * up[index]) / (
        params.rotation.steep_long
    )

    assert q(short_weight(midpoint, params.short_decay_x)) == Decimal(weight)
    assert q(up[index]) == Decimal(short)
    assert q(down[index]) == -Decimal(short)
    assert q(implied_long) == Decimal(long)
    assert q(steep[index]) == Decimal(steepener)
    assert q(flat[index]) == Decimal(flattener)


def test_sf3_the_parallel_shock_is_flat_across_every_bucket() -> None:
    params = sf_parameters()

    shifts = scenario_shifts_bp(params, "GHS", "parallel_up")

    assert set(shifts) == {Decimal("450")}


# --- SF-4 / SF-6 -------------------------------------------------------------

#: USD ΔEVE, native and converted at 15 reporting units per USD. The converted
#: figure is the unrounded native one times the rate, then rounded — not the
#: rounded native one times the rate, which would drift by half a millionth.
SF4_USD: dict[str, tuple[str, str]] = {
    "parallel_up": ("-7.906550", "-118.598245"),
    "parallel_down": ("8.826559", "132.398381"),
    "steepener": ("-4.149181", "-62.237715"),
    "flattener": ("1.704195", "25.562922"),
    "short_up": ("-3.095241", "-46.428619"),
    "short_down": ("3.227458", "48.411876"),
}
SF4_LOSS: dict[str, str] = {
    "parallel_up": "116.759902",
    "parallel_down": "132.398381",
    "steepener": "21.759660",
    "flattener": "27.827724",
    "short_up": "44.776382",
    "short_down": "48.411876",
}


def _multi_currency_inputs(params: sp.SfParameters) -> SfInputs:
    return SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(
            simple_ladder(params, "GHS", {1: "-1000", 11: "1000"}),
            simple_ladder(params, "USD", {1: "100", 12: "-100"}),
        ),
        nmds=(),
        currencies=(
            CurrencyInputs(
                currency="GHS",
                zero_cc=flat_curve("0.10", params),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(8000),
                bb_liabilities_rep=Decimal(7600),
            ),
            CurrencyInputs(
                currency="USD",
                zero_cc=flat_curve("0.05", params),
                fx_to_reporting=Decimal(15),
                bb_assets_rep=Decimal(1950),
                bb_liabilities_rep=Decimal(1300),
            ),
            CurrencyInputs(
                currency="EUR",
                zero_cc=(),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(50),
                bb_liabilities_rep=Decimal(100),
            ),
        ),
        tier1=Decimal(700),
    )


def test_sf4_materiality_excludes_the_small_currency() -> None:
    params = sf_parameters()

    result = sf_run(_multi_currency_inputs(params), params)

    scope = {row.currency: row for row in result.currencies}
    assert scope["GHS"].asset_share_pct == Decimal("80.000000")
    assert scope["GHS"].liability_share_pct == Decimal("84.444444")
    assert scope["USD"].asset_share_pct == Decimal("19.500000")
    assert scope["USD"].liability_share_pct == Decimal("14.444444")
    assert scope["EUR"].asset_share_pct == Decimal("0.500000")
    assert scope["EUR"].liability_share_pct == Decimal("1.111111")
    assert [row.currency for row in result.currencies if row.material] == ["GHS", "USD"]
    assert result.excluded_currencies == ("EUR",)


def test_sf4_gains_never_pay_for_losses() -> None:
    params = sf_parameters()

    result = sf_run(_multi_currency_inputs(params), params)

    for code, (native, reporting) in SF4_USD.items():
        usd = next(
            row for row in _scenario(result, code).by_currency if row.currency == "USD"
        )
        assert usd.delta_eve_native == Decimal(native), code
        assert usd.delta_eve_reporting == Decimal(reporting), code
    for code, expected in SF4_LOSS.items():
        assert _scenario(result, code).loss == Decimal(expected), code
    # Netting the two currencies would have reported a GAIN of 10.597716 under
    # the parallel-down shock. The framework reports a loss of 132.398381.
    assert _scenario(result, "parallel_down").net == Decimal("-10.597716")
    assert result.measures.all_scenarios.measure == Decimal("132.398381")
    assert result.measures.mandatory.measure == Decimal("132.398381")
    assert result.measures.all_scenarios.worst_scenario == "parallel_down"


def test_sf6_delta_nii_over_the_governed_horizon() -> None:
    params = sf_parameters()

    result = sf_run(_multi_currency_inputs(params), params)

    up = {row.currency: row for row in _scenario(result, "parallel_up").by_currency}
    down = {row.currency: row for row in _scenario(result, "parallel_down").by_currency}
    assert up["GHS"].delta_nii_native == Decimal("44.874000")
    assert down["GHS"].delta_nii_native == Decimal("-44.874000")
    assert up["USD"].delta_nii_native == Decimal("-1.994400")
    assert up["USD"].delta_nii_reporting == Decimal("-29.916000")
    assert down["USD"].delta_nii_reporting == Decimal("29.916000")


def test_sf4_table8_carries_both_parallel_shocks_and_their_maximum() -> None:
    params = sf_parameters()

    result = sf_run(_multi_currency_inputs(params), params)

    rows = {row.code: row for row in result.table8}
    assert list(rows) == ["parallel_up", "parallel_down", "maximum"]
    assert rows["parallel_up"].label == "Parallel up"
    assert rows["parallel_down"].delta_eve == Decimal("132.398381")
    assert rows["maximum"].delta_eve == Decimal("132.398381")
    assert rows["maximum"].delta_nii == Decimal("14.958000")
    assert rows["maximum"].delta_eve_prior is None


# --- SF-7 --------------------------------------------------------------------

SF7_DELTA_EVE: dict[str, str] = {
    "parallel_up": "-28.080246",
    "parallel_down": "45.103148",
    "steepener": "-38.534172",
    "flattener": "30.456969",
    "short_up": "14.234496",
    "short_down": "-14.554305",
}


def _sf7_instruments() -> tuple[InstrumentTerms, ...]:
    return (
        InstrumentTerms(
            ref="LOAN-1",
            family="LOAN",
            currency="GHS",
            side="asset",
            principal=Decimal(1000),
            rate_pct=Decimal(24),
            maturity=date(2028, 12, 31),
            amortisation="bullet",
            frequency_months=12,
        ),
        InstrumentTerms(
            ref="PLACE-1",
            family="INTERBANK_PLACEMENT",
            currency="GHS",
            side="asset",
            principal=Decimal(500),
            rate_pct=Decimal(20),
            rate_type="FLOATING",
            next_reset=date(2027, 3, 31),
            amortisation="bullet",
            frequency_months=0,
        ),
        InstrumentTerms(
            ref="TD-1",
            family="DEPOSIT_TERM",
            currency="GHS",
            side="liability",
            principal=Decimal(800),
            rate_pct=Decimal(18),
            maturity=date(2027, 6, 30),
            amortisation="bullet",
            frequency_months=0,
        ),
    )


def _sf7_inputs(params: sp.SfParameters) -> SfInputs:
    ladder, tallies = ladder_from_instruments(params, AS_OF, _sf7_instruments())
    return SfInputs(
        as_of=AS_OF,
        reporting_currency="GHS",
        ladders=(ladder,),
        nmds=(
            NmdBalance(
                currency="GHS",
                category="retail_transactional",
                product="CURRENT",
                balance=Decimal(1000),
                core_estimate=Decimal("0.93"),
                core_maturity_years=Decimal(5),
                history_years=Decimal(10),
            ),
        ),
        currencies=(
            CurrencyInputs(
                currency="GHS",
                zero_cc=flat_curve("0.10", params),
                fx_to_reporting=Decimal(1),
                bb_assets_rep=Decimal(1500),
                bb_liabilities_rep=Decimal(1800),
            ),
        ),
        tier1=Decimal(1000),
        tallies=tallies,
    )


def test_sf7_the_ladder_built_from_instrument_terms() -> None:
    """Slotting, accrual and the deposit cap, reached from the terms themselves."""
    params = sf_parameters()

    result = sf_run(_sf7_inputs(params), params)

    net = {row.bucket_key: row.net for row in result.ladder_base}
    assert net["b01"] == Decimal("-100.000000")
    assert net["b03"] == Decimal("524.657534")
    assert net["b04"] == Decimal("-871.408219")
    assert net["b06"] == Decimal("240.000000")
    assert net["b08"] == Decimal("1240.000000")
    assert net["b11"] == Decimal("-900.000000")


def test_sf7_end_to_end_delta_eve_and_the_outlier_verdict() -> None:
    params = sf_parameters()

    result = sf_run(_sf7_inputs(params), params)

    assert _scenario(result, "parallel_up").by_currency[0].eve_base_native == Decimal(
        "263.630350"
    )
    for code, expected in SF7_DELTA_EVE.items():
        assert _scenario(result, code).by_currency[0].delta_eve_native == Decimal(expected), code
    assert result.measures.all_scenarios.measure == Decimal("45.103148")
    assert result.measures.mandatory.measure == Decimal("45.103148")
    assert result.pct_tier1 == Decimal("4.510315")
    assert result.outlier is False


def test_sf7_delta_nii_counts_repricing_principal_only() -> None:
    """The coupons inside the horizon are not principal, so they do not reprice."""
    params = sf_parameters()

    result = sf_run(_sf7_inputs(params), params)

    up = _scenario(result, "parallel_up").by_currency[0]
    down = _scenario(result, "parallel_down").by_currency[0]
    assert up.delta_nii_native == Decimal("8.238150")
    assert down.delta_nii_native == Decimal("-8.238150")


def test_sf7_every_substituted_assumption_is_counted() -> None:
    params = sf_parameters()

    result = sf_run(_sf7_inputs(params), params)

    assert result.assumption_tallies["accrual_start_unknown"] == 2
    assert result.assumption_tallies["no_spread_leg"] == 1
    assert result.assumption_tallies["nmd_core_cap_binding"] == 1
