"""Hand-verified golden tests for the pure liquidity engine.

The fixtures mirror the Sample Bank Ltd seed's latest reporting period
(2026-03, factor 1.0, canonical amounts) and the Bank of Ghana CRD baseline
parameters. Every expected ratio is derived independently inside the test with
explicit Decimal literals so the goldens are never self-referential.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.liquidity.engine import (
    LiquidityFact,
    LiquidityParams,
    MissingParameterError,
    UnsupportedShockError,
    apply_liquidity_stress,
    compute_lcr,
    compute_nsfr,
)

MONEY = Decimal("0.0001")
RATIO = Decimal("0.000001")
FOUR_DP = Decimal("0.0001")
M = Decimal("1000000")


def _bs(category: str, millions: str, side: str) -> LiquidityFact:
    return LiquidityFact(
        fact_group="balance_sheet",
        category=category,
        amount=Decimal(millions) * M,
        side=side,
    )


def _sec(category: str, millions: str, *, cash_derived: bool = False) -> LiquidityFact:
    return LiquidityFact(
        fact_group="securities",
        category=category,
        amount=Decimal(millions) * M,
        hqla_level="L1",
        cash_derived=cash_derived,
    )


def _loan(category: str, millions: str) -> LiquidityFact:
    return LiquidityFact(
        fact_group="loan_exposure", category=category, amount=Decimal(millions) * M
    )


def _off(category: str, millions: str) -> LiquidityFact:
    return LiquidityFact(fact_group="off_balance", category=category, amount=Decimal(millions) * M)


def _inflow(category: str, millions: str) -> LiquidityFact:
    return LiquidityFact(fact_group="lcr_inflow", category=category, amount=Decimal(millions) * M)


def sample_bank_latest_facts() -> tuple[LiquidityFact, ...]:
    return (
        _bs("cash_vault", "45", "asset"),
        _bs("bog_required_reserves", "175", "asset"),
        _bs("bog_excess_reserves", "70", "asset"),
        _bs("securities_bog_bills", "260", "asset"),
        _bs("securities_gog_bonds", "360", "asset"),
        _bs("loans_gross", "1400", "asset"),
        _bs("other_assets", "90", "asset"),
        _bs("retail_deposits_stable", "700", "liability"),
        _bs("retail_deposits_less_stable", "440", "liability"),
        _bs("wholesale_operational", "240", "liability"),
        _bs("wholesale_non_op_sme", "200", "liability"),
        _bs("wholesale_non_op_corporate", "320", "liability"),
        _bs("secured_funding_l1", "60", "liability"),
        _bs("term_borrowings_gt_1y", "100", "liability"),
        _bs("capital_total", "340", "equity"),
        _loan("corporate_unrated", "560"),
        _loan("sme_retail", "280"),
        _loan("retail_other", "250"),
        _loan("residential_mortgage", "200"),
        _loan("commercial_real_estate", "60"),
        _loan("past_due_90", "50"),
        _sec("bog_bills", "260"),
        _sec("gog_bonds", "360"),
        _sec("cash_vault_hqla", "45", cash_derived=True),
        _sec("bog_excess_reserves_hqla", "70", cash_derived=True),
        _off("committed_retail", "80"),
        _off("committed_corporate", "240"),
        _inflow("retail_loan_repayments", "60"),
        _inflow("corporate_sme_repayments", "90"),
        _inflow("interbank_maturing", "45"),
    )


def bog_baseline_params() -> LiquidityParams:
    return LiquidityParams(
        outflow_rates={
            "retail_deposits_stable": Decimal("5"),
            "retail_deposits_less_stable": Decimal("10"),
            "wholesale_operational": Decimal("25"),
            "wholesale_non_op_sme": Decimal("40"),
            "wholesale_non_op_corporate": Decimal("100"),
            "secured_funding_l1": Decimal("0"),
            "term_borrowings_gt_1y": Decimal("0"),
            "committed_retail": Decimal("10"),
            "committed_corporate": Decimal("30"),
        },
        inflow_rates={
            "retail_loan_repayments": Decimal("50"),
            "corporate_sme_repayments": Decimal("50"),
            "interbank_maturing": Decimal("100"),
        },
        asf_weights={
            "capital_total": Decimal("100"),
            "retail_deposits_stable": Decimal("95"),
            "retail_deposits_less_stable": Decimal("90"),
            "wholesale_operational": Decimal("50"),
            "wholesale_non_op_sme": Decimal("90"),
            "wholesale_non_op_corporate": Decimal("50"),
            "secured_funding_l1": Decimal("0"),
            "term_borrowings_gt_1y": Decimal("100"),
        },
        rsf_weights={
            "cash_vault": Decimal("0"),
            "bog_required_reserves": Decimal("0"),
            "bog_excess_reserves": Decimal("0"),
            "securities_bog_bills": Decimal("5"),
            "securities_gog_bonds": Decimal("5"),
            "corporate_unrated": Decimal("85"),
            "sme_retail": Decimal("85"),
            "retail_other": Decimal("85"),
            "residential_mortgage": Decimal("65"),
            "commercial_real_estate": Decimal("85"),
            "past_due_90": Decimal("100"),
            "other_assets": Decimal("100"),
            "off_balance_commitments": Decimal("5"),
        },
        inflow_cap_pct=Decimal("75"),
        lcr_min_pct=Decimal("100"),
        lcr_amber_floor_pct=Decimal("90"),
        nsfr_min_pct=Decimal("100"),
        nsfr_amber_floor_pct=Decimal("90"),
    )


IDIOSYNCRATIC_SHOCKS = {
    "runoff:retail_deposits_stable": Decimal("15"),
    "runoff:retail_deposits_less_stable": Decimal("20"),
    "runoff:wholesale_operational": Decimal("40"),
    "runoff:wholesale_non_op_sme": Decimal("60"),
    "runoff:wholesale_non_op_corporate": Decimal("100"),
    "runoff:committed_retail": Decimal("20"),
    "runoff:committed_corporate": Decimal("50"),
    "inflow_multiplier": Decimal("0.75"),
    "hqla_securities_haircut_pct": Decimal("0"),
    "asf:retail_deposits_stable": Decimal("90"),
    "asf:retail_deposits_less_stable": Decimal("80"),
    "asf:wholesale_operational": Decimal("40"),
    "asf:wholesale_non_op_sme": Decimal("80"),
    "asf:wholesale_non_op_corporate": Decimal("40"),
}
MARKET_WIDE_SHOCKS = {
    "runoff:retail_deposits_stable": Decimal("7.5"),
    "runoff:retail_deposits_less_stable": Decimal("15"),
    "runoff:wholesale_operational": Decimal("30"),
    "runoff:wholesale_non_op_sme": Decimal("50"),
    "runoff:wholesale_non_op_corporate": Decimal("100"),
    "runoff:committed_retail": Decimal("10"),
    "runoff:committed_corporate": Decimal("40"),
    "inflow_multiplier": Decimal("0.90"),
    "hqla_securities_haircut_pct": Decimal("8"),
    "rsf:securities_weight_override": Decimal("10"),
}
COMBINED_SHOCKS = {
    **{
        key: value
        for key, value in IDIOSYNCRATIC_SHOCKS.items()
        if key.startswith(("runoff:", "asf:"))
    },
    "inflow_multiplier": Decimal("0.67"),
    "hqla_securities_haircut_pct": Decimal("8"),
    "rsf:securities_weight_override": Decimal("10"),
}


def test_baseline_lcr_golden() -> None:
    result = compute_lcr(sample_bank_latest_facts(), bog_baseline_params())

    assert result.hqla_total == Decimal("735000000").quantize(MONEY)
    # 35 + 44 + 60 + 80 + 320 + 0 + 0 + 8 + 72 = 619 M
    assert result.outflows_total == Decimal("619000000").quantize(MONEY)
    assert result.gross_inflows_total == Decimal("120000000").quantize(MONEY)
    assert result.inflow_cap_amount == Decimal("464250000").quantize(MONEY)
    assert result.inflow_cap_applied is False
    assert result.capped_inflows_total == Decimal("120000000").quantize(MONEY)
    assert result.net_outflows_total == Decimal("499000000").quantize(MONEY)

    expected = (Decimal("735000000") / Decimal("499000000") * 100).quantize(RATIO)
    assert result.lcr_pct == expected
    assert result.lcr_pct.quantize(FOUR_DP) == Decimal("147.2946")
    assert result.status == "green"
    assert result.all_hqla_level1 is True


def test_baseline_lcr_line_items_carry_exposure_rate_and_weighted() -> None:
    result = compute_lcr(sample_bank_latest_facts(), bog_baseline_params())
    outflows = {item.line_code: item for item in result.line_items if item.section == "outflow"}
    assert len(outflows) == 9
    corporate = outflows["wholesale_non_op_corporate"]
    assert corporate.exposure_amount == Decimal("320000000").quantize(MONEY)
    assert corporate.rate_pct == Decimal("100")
    assert corporate.weighted_amount == Decimal("320000000").quantize(MONEY)
    committed = outflows["committed_corporate"]
    assert committed.exposure_amount == Decimal("240000000").quantize(MONEY)
    assert committed.rate_pct == Decimal("30")
    assert committed.weighted_amount == Decimal("72000000").quantize(MONEY)
    hqla = {item.line_code: item for item in result.line_items if item.section == "hqla"}
    assert set(hqla) == {"bog_bills", "gog_bonds", "cash_vault_hqla", "bog_excess_reserves_hqla"}


def test_baseline_nsfr_golden() -> None:
    result = compute_nsfr(sample_bank_latest_facts(), bog_baseline_params())

    # 340 + 665 + 396 + 120 + 180 + 160 + 0 + 100 = 1961 M
    assert result.asf_total == Decimal("1961000000").quantize(MONEY)
    # 13 + 18 + 476 + 238 + 212.5 + 130 + 51 + 50 + 90 + 16 = 1294.5 M
    assert result.rsf_total == Decimal("1294500000").quantize(MONEY)

    expected = (Decimal("1961000000") / Decimal("1294500000") * 100).quantize(RATIO)
    assert result.nsfr_pct == expected
    assert result.nsfr_pct.quantize(FOUR_DP) == Decimal("151.4871")
    assert result.status == "green"


def test_idiosyncratic_stress_golden() -> None:
    facts, params = apply_liquidity_stress(
        "idiosyncratic", sample_bank_latest_facts(), bog_baseline_params(), IDIOSYNCRATIC_SHOCKS
    )
    lcr = compute_lcr(facts, params)
    nsfr = compute_nsfr(facts, params)

    # 105 + 88 + 96 + 120 + 320 + 16 + 120 = 865 M
    assert lcr.outflows_total == Decimal("865000000").quantize(MONEY)
    assert lcr.gross_inflows_total == Decimal("90000000").quantize(MONEY)
    assert lcr.inflow_cap_applied is False
    assert lcr.net_outflows_total == Decimal("775000000").quantize(MONEY)
    assert lcr.hqla_total == Decimal("735000000").quantize(MONEY)
    expected_lcr = (Decimal("735000000") / Decimal("775000000") * 100).quantize(RATIO)
    assert lcr.lcr_pct == expected_lcr
    assert lcr.lcr_pct.quantize(FOUR_DP) == Decimal("94.8387")
    assert lcr.status == "amber"

    # 340 + 630 + 352 + 96 + 160 + 128 + 0 + 100 = 1806 M
    assert nsfr.asf_total == Decimal("1806000000").quantize(MONEY)
    assert nsfr.rsf_total == Decimal("1294500000").quantize(MONEY)
    expected_nsfr = (Decimal("1806000000") / Decimal("1294500000") * 100).quantize(RATIO)
    assert nsfr.nsfr_pct == expected_nsfr
    assert nsfr.nsfr_pct.quantize(FOUR_DP) == Decimal("139.5133")
    assert nsfr.status == "green"


def test_market_wide_stress_golden() -> None:
    facts, params = apply_liquidity_stress(
        "market_wide", sample_bank_latest_facts(), bog_baseline_params(), MARKET_WIDE_SHOCKS
    )
    lcr = compute_lcr(facts, params)
    nsfr = compute_nsfr(facts, params)

    # 45 + 70 + (620 x 0.92) = 685.4 M — haircut hits bog_bills/gog_bonds only.
    assert lcr.hqla_total == Decimal("685400000").quantize(MONEY)
    # 52.5 + 66 + 72 + 100 + 320 + 8 + 96 = 714.5 M
    assert lcr.outflows_total == Decimal("714500000").quantize(MONEY)
    assert lcr.gross_inflows_total == Decimal("108000000").quantize(MONEY)
    assert lcr.net_outflows_total == Decimal("606500000").quantize(MONEY)
    expected_lcr = (Decimal("685400000") / Decimal("606500000") * 100).quantize(RATIO)
    assert lcr.lcr_pct == expected_lcr
    assert lcr.lcr_pct.quantize(FOUR_DP) == Decimal("113.0091")
    assert lcr.status == "green"

    assert nsfr.asf_total == Decimal("1961000000").quantize(MONEY)
    # 1294.5 - 31 + 62 = 1325.5 M (10% securities RSF override on unstressed values)
    assert nsfr.rsf_total == Decimal("1325500000").quantize(MONEY)
    expected_nsfr = (Decimal("1961000000") / Decimal("1325500000") * 100).quantize(RATIO)
    assert nsfr.nsfr_pct == expected_nsfr
    assert nsfr.nsfr_pct.quantize(FOUR_DP) == Decimal("147.9442")
    assert nsfr.status == "green"


def test_combined_stress_golden() -> None:
    facts, params = apply_liquidity_stress(
        "combined", sample_bank_latest_facts(), bog_baseline_params(), COMBINED_SHOCKS
    )
    lcr = compute_lcr(facts, params)
    nsfr = compute_nsfr(facts, params)

    assert lcr.outflows_total == Decimal("865000000").quantize(MONEY)
    assert lcr.gross_inflows_total == Decimal("80400000").quantize(MONEY)
    assert lcr.net_outflows_total == Decimal("784600000").quantize(MONEY)
    assert lcr.hqla_total == Decimal("685400000").quantize(MONEY)
    expected_lcr = (Decimal("685400000") / Decimal("784600000") * 100).quantize(RATIO)
    assert lcr.lcr_pct == expected_lcr
    assert lcr.lcr_pct.quantize(FOUR_DP) == Decimal("87.3566")
    assert lcr.status == "red"

    assert nsfr.asf_total == Decimal("1806000000").quantize(MONEY)
    assert nsfr.rsf_total == Decimal("1325500000").quantize(MONEY)
    expected_nsfr = (Decimal("1806000000") / Decimal("1325500000") * 100).quantize(RATIO)
    assert nsfr.nsfr_pct == expected_nsfr
    assert nsfr.nsfr_pct.quantize(FOUR_DP) == Decimal("136.2505")
    assert nsfr.status == "green"


def test_hqla_haircut_spares_cash_derived_rows() -> None:
    facts, _params = apply_liquidity_stress(
        "market_wide", sample_bank_latest_facts(), bog_baseline_params(), MARKET_WIDE_SHOCKS
    )
    securities = {fact.category: fact.amount for fact in facts if fact.fact_group == "securities"}
    assert securities["bog_bills"] == Decimal("239200000").quantize(MONEY)
    assert securities["gog_bonds"] == Decimal("331200000").quantize(MONEY)
    assert securities["cash_vault_hqla"] == Decimal("45000000")
    assert securities["bog_excess_reserves_hqla"] == Decimal("70000000")
    # NSFR reads balance-sheet securities rows; the haircut must not touch them.
    balance_sheet = {
        fact.category: fact.amount for fact in facts if fact.fact_group == "balance_sheet"
    }
    assert balance_sheet["securities_bog_bills"] == Decimal("260000000")
    assert balance_sheet["securities_gog_bonds"] == Decimal("360000000")


def test_missing_outflow_rate_for_nonzero_balance_raises() -> None:
    params = bog_baseline_params()
    trimmed_rates = {
        category: rate
        for category, rate in params.outflow_rates.items()
        if category != "wholesale_operational"
    }
    with pytest.raises(MissingParameterError) as excinfo:
        compute_lcr(sample_bank_latest_facts(), replace(params, outflow_rates=trimmed_rates))
    assert excinfo.value.category == "wholesale_operational"


def test_missing_rate_for_zero_balance_is_skipped() -> None:
    facts = (
        *sample_bank_latest_facts(),
        _bs("dormant_funding", "0", "liability"),
    )
    result = compute_lcr(facts, bog_baseline_params())
    assert result.outflows_total == Decimal("619000000").quantize(MONEY)
    assert "dormant_funding" not in {item.line_code for item in result.line_items}


def test_unsupported_shock_key_raises() -> None:
    with pytest.raises(UnsupportedShockError) as excinfo:
        apply_liquidity_stress(
            "idiosyncratic",
            sample_bank_latest_facts(),
            bog_baseline_params(),
            {"unknown:shock": Decimal("1")},
        )
    assert excinfo.value.shock_key == "unknown:shock"


def test_status_classification_happens_after_quantization() -> None:
    # Raw ratio 99.9999996% quantizes to 100.000000 at 6 dp, so the status is green.
    facts = (
        LiquidityFact(
            fact_group="balance_sheet",
            category="wholesale_non_op_corporate",
            amount=Decimal("9000000"),
            side="liability",
        ),
        LiquidityFact(
            fact_group="securities",
            category="bog_bills",
            amount=Decimal("8999999.964"),
            hqla_level="L1",
        ),
    )
    result = compute_lcr(facts, bog_baseline_params())
    assert result.lcr_pct == Decimal("100.000000")
    assert result.status == "green"
