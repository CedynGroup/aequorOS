"""Hand-verified golden tests for the pure capital engine.

The fixtures mirror the Sample Bank Ltd seed's latest reporting period
(2026-03, factor 1.0, canonical amounts) and the Bank of Ghana CRD baseline
parameters. Every expected value is derived independently inside the test with
explicit Decimal literals so the goldens are never self-referential.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.domain.capital.engine import (
    GREEN_BUFFER_PP,
    CapitalComputationError,
    CapitalFact,
    CapitalParams,
    MissingParameterError,
    UnsupportedShockError,
    classify_capital_ratio,
    compute_capital_ratios,
    compute_rwa,
    run_capital_stress,
)

MONEY = Decimal("0.0001")
RATIO = Decimal("0.000001")
FOUR_DP = Decimal("0.0001")
M = Decimal("1000000")


def _bs(category: str, millions: str, side: str) -> CapitalFact:
    return CapitalFact(
        fact_group="balance_sheet", category=category, amount=Decimal(millions) * M, side=side
    )


def _loan(category: str, millions: str, code: str) -> CapitalFact:
    return CapitalFact(
        fact_group="loan_exposure",
        category=category,
        amount=Decimal(millions) * M,
        risk_weight_code=code,
    )


def _off(category: str, millions: str, ccf: str, code: str) -> CapitalFact:
    return CapitalFact(
        fact_group="off_balance",
        category=category,
        amount=Decimal(millions) * M,
        ccf_pct=Decimal(ccf),
        risk_weight_code=code,
    )


def _market(category: str, millions: str) -> CapitalFact:
    return CapitalFact(fact_group="market_risk", category=category, amount=Decimal(millions) * M)


def _income(year: int, millions: str) -> CapitalFact:
    return CapitalFact(
        fact_group="operational_income",
        category=f"gross_income_{year}",
        amount=Decimal(millions) * M,
        income_year=year,
    )


def _component(category: str, millions: str, tier: str, deduction: bool = False) -> CapitalFact:
    return CapitalFact(
        fact_group="capital_component",
        category=category,
        amount=Decimal(millions) * M,
        capital_tier=tier,
        is_deduction=deduction,
    )


def sample_bank_latest_facts() -> tuple[CapitalFact, ...]:
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
        _loan("corporate_unrated", "560", "RW100"),
        _loan("sme_retail", "280", "RW75"),
        _loan("retail_other", "250", "RW75"),
        _loan("residential_mortgage", "200", "RW35"),
        _loan("commercial_real_estate", "60", "RW100"),
        _loan("past_due_90", "50", "RW150"),
        _off("committed_retail", "80", "50", "RW75"),
        _off("committed_corporate", "240", "50", "RW100"),
        _market("net_long_fx", "45"),
        _market("net_short_fx", "12"),
        _income(2023, "340"),
        _income(2024, "380"),
        _income(2025, "400"),
        _component("paid_up_capital", "150", "CET1"),
        _component("retained_earnings", "95", "CET1"),
        _component("statutory_reserves", "45", "CET1"),
        _component("other_reserves", "10", "CET1"),
        _component("intangibles", "25", "CET1", deduction=True),
        _component("deferred_tax_assets", "15", "CET1", deduction=True),
        _component("perpetual_instruments", "20", "AT1"),
        _component("subordinated_debt", "45", "T2"),
        _component("general_provisions", "15", "T2"),
    )


def bog_capital_params() -> CapitalParams:
    return CapitalParams(
        risk_weights={
            "RW0": Decimal("0"),
            "RW20": Decimal("20"),
            "RW35": Decimal("35"),
            "RW50": Decimal("50"),
            "RW75": Decimal("75"),
            "RW100": Decimal("100"),
            "RW150": Decimal("150"),
        },
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("8"),
        rwa_multiplier_pct=Decimal("1250"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("10"),
        leverage_min_pct=Decimal("3"),
        car_early_warning_pct=Decimal("10.5"),
        car_critical_pct=Decimal("9"),
    )


MILD_SHOCKS = {
    "quarterly_rwa_growth_pct": Decimal("1.5"),
    "quarterly_income_m": Decimal("16"),
    "quarterly_credit_loss_m": Decimal("1.4"),
    "fx_rwa_multiplier": Decimal("1.0"),
}
MODERATE_SHOCKS = {
    "quarterly_rwa_growth_pct": Decimal("2.5"),
    "quarterly_income_m": Decimal("12"),
    "quarterly_credit_loss_m": Decimal("6.3"),
    "fx_rwa_multiplier": Decimal("1.25"),
}
SEVERE_SHOCKS = {
    "quarterly_rwa_growth_pct": Decimal("4.0"),
    "quarterly_income_m": Decimal("2"),
    "quarterly_credit_loss_m": Decimal("30.8"),
    "fx_rwa_multiplier": Decimal("1.6"),
}


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _expected_quarter_car(
    quarter: int, growth_pct: Decimal, retention_m: Decimal, fx_multiplier: Decimal
) -> Decimal:
    """Independent Decimal derivation of one stressed quarter's CAR."""
    cet1 = Decimal("260000000") + Decimal(quarter) * retention_m * M
    credit = _money(Decimal("1402500000") * (Decimal(1) + growth_pct / 100) ** quarter)
    market = _money(Decimal("45000000") * fx_multiplier) if quarter >= 1 else Decimal("45000000")
    gp_cap = _money(credit * Decimal("1.25") / 100)
    tier2 = Decimal("45000000") + min(Decimal("15000000"), gp_cap)
    total_capital = cet1 + Decimal("20000000") + tier2
    total_rwa = credit + market + Decimal("700000000")
    return (total_capital / total_rwa * 100).quantize(RATIO, rounding=ROUND_HALF_UP)


def test_baseline_rwa_golden() -> None:
    result = compute_rwa(sample_bank_latest_facts(), bog_capital_params())

    credit = {item.line_code: item for item in result.line_items if item.section == "credit_rwa"}
    assert len(credit) == 12
    assert credit["corporate_unrated"].weighted_amount == Decimal("560000000").quantize(MONEY)
    assert credit["sme_retail"].weighted_amount == Decimal("210000000").quantize(MONEY)
    assert credit["retail_other"].weighted_amount == Decimal("187500000").quantize(MONEY)
    assert credit["residential_mortgage"].weighted_amount == Decimal("70000000").quantize(MONEY)
    assert credit["commercial_real_estate"].weighted_amount == Decimal("60000000").quantize(MONEY)
    assert credit["past_due_90"].weighted_amount == Decimal("75000000").quantize(MONEY)
    assert credit["other_assets"].weighted_amount == Decimal("90000000").quantize(MONEY)
    # Off-balance lines expose the CCF-adjusted EAD.
    assert credit["committed_retail"].exposure_amount == Decimal("40000000").quantize(MONEY)
    assert credit["committed_retail"].rate_pct == Decimal("75")
    assert credit["committed_retail"].weighted_amount == Decimal("30000000").quantize(MONEY)
    assert credit["committed_corporate"].exposure_amount == Decimal("120000000").quantize(MONEY)
    assert credit["committed_corporate"].weighted_amount == Decimal("120000000").quantize(MONEY)
    # Zero-weight transparency rows.
    assert credit["bog_bills"].exposure_amount == Decimal("260000000").quantize(MONEY)
    assert credit["bog_bills"].weighted_amount == Decimal("0").quantize(MONEY)
    assert credit["gog_bonds"].exposure_amount == Decimal("360000000").quantize(MONEY)
    assert credit["cash_and_reserves"].exposure_amount == Decimal("290000000").quantize(MONEY)
    assert credit["cash_and_reserves"].weighted_amount == Decimal("0").quantize(MONEY)
    assert result.credit_rwa == Decimal("1402500000").quantize(MONEY)

    # Market: 8% x max(45M, 12M) = 3.6M charge x 12.5 = 45M RWA.
    assert result.fx_net_long == Decimal("45000000").quantize(MONEY)
    assert result.fx_net_short == Decimal("12000000").quantize(MONEY)
    assert result.fx_charge == Decimal("3600000").quantize(MONEY)
    assert result.market_rwa == Decimal("45000000").quantize(MONEY)
    market = {item.line_code: item for item in result.line_items if item.section == "market_rwa"}
    assert set(market) == {"net_long_fx", "net_short_fx", "fx_charge", "fx_rwa"}
    assert market["fx_rwa"].weighted_amount == Decimal("45000000").quantize(MONEY)

    # Operational BIA: (340+380+400) x 15 / 300 = 56M exactly, x 12.5 = 700M.
    assert result.gross_income_positive_total == Decimal("1120000000").quantize(MONEY)
    assert result.positive_income_years == 3
    assert result.bia_charge == Decimal("56000000").quantize(MONEY)
    assert result.operational_rwa == Decimal("700000000").quantize(MONEY)
    operational = {
        item.line_code: item for item in result.line_items if item.section == "operational_rwa"
    }
    assert set(operational) == {
        "gross_income_2023",
        "gross_income_2024",
        "gross_income_2025",
        "bia_charge",
        "operational_rwa",
    }

    assert result.total_rwa == Decimal("2147500000").quantize(MONEY)


def test_operational_bia_excludes_non_positive_years() -> None:
    facts = tuple(
        replace(fact, amount=Decimal("-20000000"))
        if fact.fact_group == "operational_income" and fact.income_year == 2023
        else fact
        for fact in sample_bank_latest_facts()
    )
    result = compute_rwa(facts, bog_capital_params())
    # (380+400) x 15 / 200 = 58.5M exactly.
    assert result.positive_income_years == 2
    assert result.bia_charge == Decimal("58500000").quantize(MONEY)
    assert result.operational_rwa == Decimal("731250000").quantize(MONEY)
    operational = {
        item.line_code: item for item in result.line_items if item.section == "operational_rwa"
    }
    assert operational["gross_income_2023"].weighted_amount == Decimal("0")
    assert "Excluded" in operational["gross_income_2023"].description


def test_baseline_capital_structure_golden() -> None:
    facts = sample_bank_latest_facts()
    params = bog_capital_params()
    rwa = compute_rwa(facts, params)
    result = compute_capital_ratios(facts, rwa, params)

    # CET1 = 150 + 95 + 45 + 10 - 25 - 15 = 260M.
    assert result.cet1_capital == Decimal("260000000").quantize(MONEY)
    assert result.at1_capital == Decimal("20000000").quantize(MONEY)
    assert result.tier1_capital == Decimal("280000000").quantize(MONEY)
    # Tier 2 = 45 + 15; the 1.25% x 1402.5M = 17,531,250 cap does not bind.
    assert result.general_provisions_amount == Decimal("15000000").quantize(MONEY)
    assert result.general_provisions_cap == Decimal("17531250").quantize(MONEY)
    assert result.gp_cap_applied is False
    assert result.tier2_capital == Decimal("60000000").quantize(MONEY)
    assert result.total_capital == Decimal("340000000").quantize(MONEY)

    # Cross-module consistency: total capital equals the balance-sheet equity row.
    capital_total_fact = next(
        fact
        for fact in facts
        if fact.fact_group == "balance_sheet" and fact.category == "capital_total"
    )
    assert result.total_capital == capital_total_fact.amount

    components = {
        item.line_code: item for item in result.line_items if item.section == "capital_component"
    }
    assert len(components) == 9
    assert components["cet1:intangibles"].weighted_amount == Decimal("-25000000").quantize(MONEY)
    assert components["cet1:deferred_tax_assets"].weighted_amount == (
        Decimal("-15000000").quantize(MONEY)
    )
    assert components["t2:general_provisions"].weighted_amount == (
        Decimal("15000000").quantize(MONEY)
    )


def test_baseline_ratios_golden() -> None:
    facts = sample_bank_latest_facts()
    params = bog_capital_params()
    rwa = compute_rwa(facts, params)
    result = compute_capital_ratios(facts, rwa, params)

    # Leverage exposure = 2400M on-balance assets + 160M CCF-adjusted off-balance.
    assert result.leverage_exposure == Decimal("2560000000").quantize(MONEY)

    expected_cet1 = (Decimal("260000000") / Decimal("2147500000") * 100).quantize(RATIO)
    expected_tier1 = (Decimal("280000000") / Decimal("2147500000") * 100).quantize(RATIO)
    expected_car = (Decimal("340000000") / Decimal("2147500000") * 100).quantize(RATIO)
    expected_leverage = (Decimal("280000000") / Decimal("2560000000") * 100).quantize(RATIO)
    assert result.cet1_ratio_pct == expected_cet1
    assert result.tier1_ratio_pct == expected_tier1
    assert result.car_pct == expected_car
    assert result.leverage_ratio_pct == expected_leverage
    assert result.cet1_ratio_pct == Decimal("12.107101")
    assert result.tier1_ratio_pct == Decimal("13.038417")
    assert result.car_pct == Decimal("15.832363")
    assert result.leverage_ratio_pct == Decimal("10.937500")
    assert result.cet1_status == "green"
    assert result.tier1_status == "green"
    assert result.car_status == "green"
    assert result.leverage_status == "green"

    ratios = {item.line_code: item for item in result.line_items if item.section == "ratio"}
    assert set(ratios) == {"cet1_ratio", "tier1_ratio", "car", "leverage_ratio"}
    assert ratios["car"].rate_pct == Decimal("15.832363")
    assert ratios["car"].exposure_amount == Decimal("2147500000").quantize(MONEY)
    assert ratios["car"].weighted_amount == Decimal("340000000").quantize(MONEY)


def test_car_green_floor_is_exactly_the_early_warning_threshold() -> None:
    params = bog_capital_params()
    assert params.car_min_pct + GREEN_BUFFER_PP == params.car_early_warning_pct
    assert classify_capital_ratio(Decimal("10.500000"), params.car_min_pct) == "green"
    assert classify_capital_ratio(Decimal("10.499999"), params.car_min_pct) == "amber"
    assert classify_capital_ratio(Decimal("10.000000"), params.car_min_pct) == "amber"
    assert classify_capital_ratio(Decimal("9.999999"), params.car_min_pct) == "red"


def test_mild_stress_golden() -> None:
    result = run_capital_stress(
        "mild", sample_bank_latest_facts(), bog_capital_params(), MILD_SHOCKS
    )
    assert [row.quarter for row in result.path] == [0, 1, 2, 3, 4]
    q4 = result.path[4]
    # CET1_4 = 260 + 4 x (16 - 1.4) = 318.4M; credit = 1402.5M x 1.015^4;
    # market 45M (multiplier 1.0); operational 700M.
    assert q4.cet1_capital == Decimal("318400000").quantize(MONEY)
    assert q4.total_capital == Decimal("398400000").quantize(MONEY)
    assert q4.credit_rwa == _money(Decimal("1402500000") * Decimal("1.015") ** 4)
    assert q4.market_rwa == Decimal("45000000").quantize(MONEY)
    assert q4.operational_rwa == Decimal("700000000").quantize(MONEY)
    expected_car = _expected_quarter_car(4, Decimal("1.5"), Decimal("14.6"), Decimal("1.0"))
    assert q4.car == expected_car
    assert q4.car.quantize(FOUR_DP) == Decimal("17.8370")
    assert all(not trigger.fired for trigger in result.triggers)
    assert all(trigger.first_quarter is None for trigger in result.triggers)


def test_moderate_stress_golden() -> None:
    result = run_capital_stress(
        "moderate", sample_bank_latest_facts(), bog_capital_params(), MODERATE_SHOCKS
    )
    q4 = result.path[4]
    # CET1_4 = 260 + 4 x (12 - 6.3) = 282.8M; credit x 1.025^4; market 56.25M.
    assert q4.cet1_capital == Decimal("282800000").quantize(MONEY)
    assert q4.market_rwa == Decimal("56250000").quantize(MONEY)
    expected_car = _expected_quarter_car(4, Decimal("2.5"), Decimal("5.7"), Decimal("1.25"))
    assert q4.car == expected_car
    assert q4.car.quantize(FOUR_DP) == Decimal("15.7442")
    assert all(not trigger.fired for trigger in result.triggers)


def test_severe_stress_golden() -> None:
    result = run_capital_stress(
        "severe", sample_bank_latest_facts(), bog_capital_params(), SEVERE_SHOCKS
    )
    q3 = result.path[3]
    q4 = result.path[4]
    # CET1_4 = 260 + 4 x (2 - 30.8) = 144.8M; credit = 1402.5M x 1.04^4 =
    # 1,640,726,630.40; market 72M.
    assert q4.cet1_capital == Decimal("144800000").quantize(MONEY)
    assert q4.credit_rwa == Decimal("1640726630.40").quantize(MONEY)
    assert q4.market_rwa == Decimal("72000000").quantize(MONEY)
    expected_car = _expected_quarter_car(4, Decimal("4.0"), Decimal("-28.8"), Decimal("1.6"))
    assert q4.car == expected_car
    assert q4.car.quantize(FOUR_DP) == Decimal("9.3173")
    # No trigger fires before Q4: the Q3 CAR is still above the early warning floor.
    assert q3.car == _expected_quarter_car(4 - 1, Decimal("4.0"), Decimal("-28.8"), Decimal("1.6"))
    assert q3.car > Decimal("10.5")

    triggers = {trigger.code: trigger for trigger in result.triggers}
    assert triggers["early_warning"].fired is True
    assert triggers["early_warning"].first_quarter == 4
    assert triggers["early_warning"].threshold_pct == Decimal("10.5")
    assert triggers["early_warning"].action == (
        "Suspend variable compensation and halt non-essential capital expenditure."
    )
    assert triggers["breach"].fired is True
    assert triggers["breach"].first_quarter == 4
    assert triggers["breach"].action == (
        "Halt dividend distributions, reduce RWA via portfolio sale, and activate the "
        "Tier 2 issuance plan."
    )
    assert triggers["critical"].fired is False
    assert triggers["critical"].first_quarter is None
    assert triggers["critical"].action == (
        "Notify the central bank and initiate the underwritten emergency rights issue."
    )


def test_missing_risk_weight_parameter_raises() -> None:
    params = bog_capital_params()
    trimmed = {code: pct for code, pct in params.risk_weights.items() if code != "RW150"}
    with pytest.raises(MissingParameterError) as excinfo:
        compute_rwa(sample_bank_latest_facts(), replace(params, risk_weights=trimmed))
    assert excinfo.value.name == "RW150"


def test_missing_risk_weight_code_on_fact_raises() -> None:
    facts = tuple(
        replace(fact, risk_weight_code=None)
        if fact.fact_group == "loan_exposure" and fact.category == "sme_retail"
        else fact
        for fact in sample_bank_latest_facts()
    )
    with pytest.raises(MissingParameterError) as excinfo:
        compute_rwa(facts, bog_capital_params())
    assert excinfo.value.name == "risk_weight_code:sme_retail"


def test_no_positive_income_years_raises() -> None:
    facts = tuple(
        replace(fact, amount=Decimal("0")) if fact.fact_group == "operational_income" else fact
        for fact in sample_bank_latest_facts()
    )
    with pytest.raises(CapitalComputationError):
        compute_rwa(facts, bog_capital_params())


def test_unsupported_stress_shock_raises() -> None:
    with pytest.raises(UnsupportedShockError) as excinfo:
        run_capital_stress(
            "severe",
            sample_bank_latest_facts(),
            bog_capital_params(),
            {**SEVERE_SHOCKS, "unknown:shock": Decimal("1")},
        )
    assert excinfo.value.shock_key == "unknown:shock"


def test_missing_required_stress_shock_raises() -> None:
    shocks = {key: value for key, value in SEVERE_SHOCKS.items() if key != "fx_rwa_multiplier"}
    with pytest.raises(MissingParameterError) as excinfo:
        run_capital_stress("severe", sample_bank_latest_facts(), bog_capital_params(), shocks)
    assert excinfo.value.name == "stress_shock:severe:fx_rwa_multiplier"


def test_status_classification_happens_after_quantization() -> None:
    # A raw CAR of 10.4999996% quantizes to 10.500000 at 6 dp, so the status is green.
    facts = sample_bank_latest_facts()
    params = bog_capital_params()
    rwa = compute_rwa(facts, params)
    car = (Decimal("10.4999996")).quantize(RATIO, rounding=ROUND_HALF_UP)
    assert car == Decimal("10.500000")
    assert classify_capital_ratio(car, params.car_min_pct) == "green"
    # And the engine's own statuses are computed on already-quantized ratios.
    result = compute_capital_ratios(facts, rwa, params)
    assert result.car_pct == result.car_pct.quantize(RATIO)
