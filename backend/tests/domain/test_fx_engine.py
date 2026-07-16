"""Hand-verified golden tests for the pure FX risk engine.

The net-open-position, scenario and hedge fixtures mirror the Sample Bank Ltd
seed's latest reporting period (2026-03, factor 1.0, canonical amounts). The VaR
and stressed-VaR expectations are derived from small hand-constructed return sets
where the 1st-percentile loss is computed by hand, so the goldens are never a
straight echo of the engine's own output.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.fx.engine import (
    FxHedge,
    FxPosition,
    MissingParameterError,
    assess_hedges,
    compute_nop,
    compute_stressed_var,
    compute_var,
    run_fx_scenarios,
)

M = Decimal("1000000")
TIER1 = Decimal("280") * M
SINGLE_LIMIT = Decimal("10")
AGGREGATE_LIMIT = Decimal("20")
CONFIDENCE = Decimal("99")

# (currency, net_ghs_millions, spot). Longs USD/GBP/NGN/ZAR sum to +45M, shorts
# EUR/XOF to -12M, matching the seed's market_risk tie-out at factor 1.0.
_CANONICAL = (
    ("USD", "30", "12.5"),
    ("EUR", "-7", "14.0"),
    ("GBP", "9", "15.0"),
    ("NGN", "3", "0.008"),
    ("ZAR", "3", "0.6"),
    ("XOF", "-5", "0.02"),
)


def _pos(currency: str, net_ghs_m: str, spot: str) -> FxPosition:
    net_ghs = Decimal(net_ghs_m) * M
    net_ccy = (net_ghs / Decimal(spot)).quantize(Decimal("0.0001"))
    return FxPosition(
        currency=currency,
        net_ghs=net_ghs,
        spot_ghs=Decimal(spot),
        net_ccy=net_ccy,
        assets_ccy=net_ccy,
        liabilities_ccy=Decimal("0"),
        net_derivatives_ccy=Decimal("0"),
    )


def _canonical_positions() -> list[FxPosition]:
    return [_pos(currency, net_ghs_m, spot) for currency, net_ghs_m, spot in _CANONICAL]


def test_nop_tie_out_golden() -> None:
    nop = compute_nop(_canonical_positions(), TIER1, SINGLE_LIMIT, AGGREGATE_LIMIT)
    assert nop.sum_long == Decimal("45") * M
    assert nop.sum_short == Decimal("12") * M
    assert nop.overall_nop == Decimal("45") * M
    # 45 / 280 * 100 = 16.0714285714... -> 6 dp half-up.
    assert nop.nop_pct_tier1 == Decimal("16.071429")
    assert nop.within_aggregate_limit is True

    # USD is the largest single currency: 30 / 280 * 100 = 10.714286% > 10% limit.
    assert nop.single_ccy_max_currency == "USD"
    assert nop.single_ccy_max_pct == Decimal("10.714286")
    assert nop.within_single_limit is False

    by_ccy = {currency.currency: currency for currency in nop.currencies}
    assert by_ccy["USD"].side == "long"
    assert by_ccy["EUR"].side == "short"
    assert by_ccy["EUR"].abs_pct_tier1 == Decimal("2.500000")
    assert [item.line_code for item in nop.line_items] == ["EUR", "GBP", "NGN", "USD", "XOF", "ZAR"]
    assert all(item.section == "fx_position" for item in nop.line_items)


def test_var_golden_five_day_hand_check() -> None:
    positions = [_pos("USD", "30", "12.5"), _pos("EUR", "-7", "14.0")]
    returns: dict[str, list[float]] = {
        "USD": [0.01, -0.02, 0.005, -0.03, 0.00],
        "EUR": [0.02, 0.00, -0.01, 0.01, -0.015],
    }
    # Portfolio P&L_t = 30M*USD_t + (-7M)*EUR_t:
    #   [160000, -600000, 220000, -970000, 105000]; sorted worst (rank 1) = -970000.
    var = compute_var(positions, returns, CONFIDENCE)
    assert var.portfolio_var == Decimal("970000")
    assert var.observations == 5
    standalone = {item.currency: item.standalone_var for item in var.currency_vars}
    # USD alone worst day -30M*0.03 = -900000; EUR alone worst +(-7M)*0.02 = -140000.
    assert standalone["USD"] == Decimal("900000")
    assert standalone["EUR"] == Decimal("140000")
    assert var.standalone_total == Decimal("1040000")
    # Diversification benefit = (900000 + 140000) - 970000 = 70000.
    assert var.diversification_benefit == Decimal("70000")
    assert [item.line_code for item in var.line_items][:2] == [
        "portfolio_var",
        "diversification_benefit",
    ]
    assert all(item.section == "fx_var" for item in var.line_items)


def test_stressed_var_uses_only_the_crisis_window() -> None:
    positions = [_pos("USD", "30", "12.5")]
    # Day 7 (outside the crisis window) carries the single worst return so that a
    # full-window VaR (1800000) differs from the crisis-slice VaR (1500000).
    returns: dict[str, list[float]] = {
        "USD": [0.02, 0.02, -0.04, -0.02, -0.05, -0.01, 0.03, -0.06],
    }
    base = compute_var(positions, returns, CONFIDENCE)
    assert base.portfolio_var == Decimal("1800000")

    # Crisis window [2, 5] -> returns [-0.04, -0.02, -0.05, -0.01]; worst -0.05.
    # Crisis VaR = 30M * 0.05 = 1500000; with a 0.3 uplift -> 1950000.
    stressed = compute_stressed_var(positions, returns, CONFIDENCE, (2, 5), Decimal("0.3"))
    assert stressed == Decimal("1950000")
    # A full-window stressed figure would instead be 1800000 * 1.3 = 2340000.
    assert stressed != Decimal("2340000")


def test_stressed_var_clamps_window_to_short_history() -> None:
    # A bank that uploaded only four observations cannot reach the configured
    # [2, 5] crisis slice: the window is clamped to the available tail (all four
    # observations here) rather than failing the whole FX analysis.
    positions = [_pos("USD", "30", "12.5")]
    returns: dict[str, list[float]] = {"USD": [0.02, -0.05, -0.02, -0.04]}
    stressed = compute_stressed_var(positions, returns, CONFIDENCE, (2, 5), Decimal("0.3"))
    # Clamped window [0, 3]; worst return -0.05 -> 30M * 0.05 = 1500000 * 1.3.
    assert stressed == Decimal("1950000")
    # With the full history present the configured window is used unchanged.
    long_returns: dict[str, list[float]] = {
        "USD": [0.02, 0.02, -0.04, -0.02, -0.05, -0.01, 0.03, -0.06],
    }
    assert compute_stressed_var(
        positions, long_returns, CONFIDENCE, (2, 5), Decimal("0.3")
    ) == Decimal("1950000")


def test_hedge_effectiveness_classification() -> None:
    hedges = [
        FxHedge(
            "FXH-USD-01", "forward", "USD/GHS", Decimal("4.5") * M, Decimal("0.94"), Decimal("1.02")
        ),
        FxHedge(
            "FXH-EUR-02",
            "cross_currency_swap",
            "EUR/GHS",
            Decimal("-2.1") * M,
            Decimal("0.88"),
            Decimal("0.91"),
        ),
        FxHedge(
            "FXH-GBP-03", "option", "GBP/GHS", Decimal("1.3") * M, Decimal("0.72"), Decimal("0.95")
        ),
    ]
    result = assess_hedges(hedges, Decimal("80"), Decimal("80"), Decimal("125"))
    assert result.effective_count == 2
    assert result.ineffective_count == 1
    assert result.total_count == 3
    # 4.5 - 2.1 + 1.3 = 3.7M aggregate mark-to-market.
    assert result.aggregate_mtm_ghs == Decimal("3700000")
    by_id = {hedge.hedge_id: hedge for hedge in result.hedges}
    assert by_id["FXH-USD-01"].effective is True
    assert by_id["FXH-EUR-02"].effective is True
    # The option fails the R-squared leg (72% < 80%) even though its offset passes.
    assert by_id["FXH-GBP-03"].effective is False
    assert by_id["FXH-GBP-03"].r2_pass is False
    assert by_id["FXH-GBP-03"].offset_pass is True


def test_offset_band_failure_marks_hedge_ineffective() -> None:
    hedges = [
        FxHedge("FXH-01", "forward", "USD/GHS", Decimal("1") * M, Decimal("0.95"), Decimal("1.40")),
    ]
    result = assess_hedges(hedges, Decimal("80"), Decimal("80"), Decimal("125"))
    assert result.effective_count == 0
    assert result.hedges[0].r2_pass is True
    assert result.hedges[0].offset_pass is False


def test_scenario_nop_after_cedi_depreciation() -> None:
    positions = _canonical_positions()
    scenarios = run_fx_scenarios(
        positions,
        TIER1,
        {
            "baseline": Decimal("0"),
            "mild_depreciation": Decimal("10"),
            "severe_depreciation": Decimal("20"),
            "cedi_crisis": Decimal("30"),
        },
        SINGLE_LIMIT,
        AGGREGATE_LIMIT,
    )
    by_code = {scenario.scenario_code: scenario for scenario in scenarios}
    assert by_code["baseline"].nop_ghs == Decimal("45") * M
    # A 20% cedi depreciation lifts the NOP to 45M * 1.2 = 54M -> 19.285714% of Tier 1.
    assert by_code["severe_depreciation"].nop_ghs == Decimal("54") * M
    assert by_code["severe_depreciation"].nop_pct_tier1 == Decimal("19.285714")
    assert by_code["severe_depreciation"].within_aggregate_limit is True
    # The 30% crisis shock breaches the 20% aggregate limit: 58.5M / 280M = 20.892857%.
    assert by_code["cedi_crisis"].nop_ghs == Decimal("58.5") * M
    assert by_code["cedi_crisis"].nop_pct_tier1 == Decimal("20.892857")
    assert by_code["cedi_crisis"].within_aggregate_limit is False


def test_missing_return_history_raises() -> None:
    positions = [_pos("USD", "30", "12.5"), _pos("EUR", "-7", "14.0")]
    returns: dict[str, list[float]] = {"USD": [0.01, -0.02, 0.0]}
    with pytest.raises(MissingParameterError) as excinfo:
        compute_var(positions, returns, CONFIDENCE)
    assert "EUR" in excinfo.value.name
