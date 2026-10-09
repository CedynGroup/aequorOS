"""Hand-verified tests for the 3-year enterprise stress projection (Phase 2 item 2).

The base leg is proven byte-identical to the independently-tested forecasting
engine (so the reuse is correct, not self-referential); the stress leg is proven
directionally and through its hand-derived per-year ECL multipliers.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.capital.ecl import EclAssumption
from app.domain.forecasting.engine import ForecastFact, project
from app.domain.stress.appendix_ii import build_appendix_ii
from app.domain.stress.credit_bottom_up import CreditExposure
from app.domain.stress.management_actions import RecognitionCaps
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    ProjectionInputError,
    project_enterprise,
)
from app.domain.stress.translation import MacroPathPoint
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_forecast_params,
    credit_basis_facts,
    sample_bank_latest_facts,
    severe_paths,
)


def _inputs(paths, **overrides) -> EnterpriseProjectionInputs:
    defaults = {
        "scenario_code": "SEVERE-2027",
        "scenario_paths": paths,
        "facts": sample_bank_latest_facts(),
        "params": bog_forecast_params(),
        "plan": BASE_ASSUMPTIONS,
        "horizon_years": 3,
    }
    defaults.update(overrides)
    return EnterpriseProjectionInputs(**defaults)  # type: ignore[arg-type]


def test_horizon_below_three_years_is_rejected() -> None:
    with pytest.raises(ProjectionInputError) as exc:
        project_enterprise(_inputs(severe_paths(2), horizon_years=2))
    assert exc.value.code == "horizon_too_short"


def test_base_leg_is_identical_to_the_forecasting_engine() -> None:
    """The base leg must reproduce the validated forecasting engine exactly."""
    projection = project_enterprise(_inputs(base_paths()))
    forecast = project(sample_bank_latest_facts(), bog_forecast_params(), BASE_ASSUMPTIONS, years=3)
    # Year 0 (as-of) ties to the forecast's year 0.
    assert projection.current.ratios.car_pct == forecast.years[0].car_pct
    assert projection.current.lcr_pct == forecast.years[0].lcr_pct
    for index, projected in enumerate(projection.base):
        reference = forecast.years[index + 1]
        assert projected.ratios.car_pct == reference.car_pct
        assert projected.ratios.cet1_ratio_pct == reference.cet1_ratio_pct
        assert projected.ratios.tier1_ratio_pct == reference.tier1_ratio_pct
        assert projected.lcr_pct == reference.lcr_pct
        assert projected.nsfr_pct == reference.nsfr_pct
        assert projected.pnl.net_income == reference.net_income
        assert projected.balance_sheet.total_assets == reference.total_assets


def test_base_scenario_collapses_stress_onto_base() -> None:
    """stress == base everywhere ⇒ the two legs are identical (zero stress delta)."""
    projection = project_enterprise(_inputs(base_paths()))
    for base_year, stress_year in zip(projection.base, projection.stress, strict=True):
        assert base_year.ratios.car_pct == stress_year.ratios.car_pct
        assert base_year.pnl.net_income == stress_year.pnl.net_income
        assert base_year.pnl.credit_losses == stress_year.pnl.credit_losses
        assert base_year.lcr_pct == stress_year.lcr_pct
        assert stress_year.pd_multiplier == Decimal("1")
        assert stress_year.lgd_multiplier == Decimal("1")


def test_severe_stress_raises_impairment_and_lowers_profit() -> None:
    projection = project_enterprise(_inputs(severe_paths()))
    for base_year, stress_year in zip(projection.base, projection.stress, strict=True):
        # Perfect-foresight, single-scenario multipliers wired into the cost of
        # risk: pd = 1.21, lgd = 1.195 each year (constant deltas).
        assert stress_year.pd_multiplier == Decimal("1.21")
        assert stress_year.lgd_multiplier == Decimal("1.195")
        assert stress_year.pnl.credit_losses > base_year.pnl.credit_losses
        assert stress_year.pnl.net_income < base_year.pnl.net_income


def test_perfect_foresight_uses_each_years_own_macro() -> None:
    """Different per-year macro ⇒ different per-year PD multipliers (¶48 foresight)."""

    # Year 1 GDP trough (delta −0.05), year 3 partial recovery (delta −0.02).
    def gdp(year: int, stress: str) -> MacroPathPoint:
        return MacroPathPoint("gdp_growth", year, Decimal("0.05"), Decimal(stress))

    # The capital elasticity register also reads unemployment and gse_index, and
    # ``translate`` now refuses a scenario that omits a variable it reads (P0-9).
    # Both are authored FLAT, contributing exactly zero, so every PD multiplier
    # asserted below is the same number this test has always asserted.
    def flat(variable: str, year: int, level: str) -> MacroPathPoint:
        return MacroPathPoint(variable, year, Decimal(level), Decimal(level))

    paths = (
        gdp(0, "0.05"),
        gdp(1, "0.00"),  # delta −0.05 ⇒ pd 1.15
        gdp(2, "0.01"),  # delta −0.04 ⇒ pd 1.12
        gdp(3, "0.03"),  # delta −0.02 ⇒ pd 1.06
        *(flat("unemployment", year, "0.06") for year in range(4)),
        *(flat("gse_index", year, "5000") for year in range(4)),
        # The projection also drives the liquidity register (the year-1
        # mark-to-market haircut), so its variables are authored flat too.
        *(flat("interest_rate", year, "0.20") for year in range(4)),
        *(flat("inflation", year, "0.15") for year in range(4)),
        *(flat("fx_usd_ghs", year, "12.5") for year in range(4)),
    )
    projection = project_enterprise(_inputs(paths))
    assert projection.stress[0].pd_multiplier == Decimal("1.15")
    assert projection.stress[1].pd_multiplier == Decimal("1.12")
    assert projection.stress[2].pd_multiplier == Decimal("1.06")


def test_minima_are_assessed_against_all_floors() -> None:
    # A paid-up floor above the bank's 150M paid-up capital breaches immediately.
    projection = project_enterprise(_inputs(severe_paths(), paid_up_min=Decimal("400000000")))
    assert projection.stress_stays_above_all_minima is False
    assert projection.first_breach_year == 1
    assert "paid_up" in projection.binding_minima
    for year in projection.stress:
        assert year.minima.paid_up_ok is False
        # The capital ratios themselves clear the CRD minima in this scenario.
        assert year.minima.car_ok is True
        assert year.minima.cet1_ok is True


def test_minima_all_ok_when_floors_are_met() -> None:
    projection = project_enterprise(_inputs(severe_paths(), paid_up_min=Decimal("0")))
    assert projection.stress_stays_above_all_minima is True
    assert projection.first_breach_year is None
    assert projection.binding_minima == ()


def test_projection_spans_the_full_horizon() -> None:
    projection = project_enterprise(_inputs(severe_paths(4), horizon_years=4))
    assert projection.horizon_years == 4
    assert len(projection.base) == 4
    assert len(projection.stress) == 4
    assert [year.year for year in projection.stress] == [1, 2, 3, 4]


def _overlay_book() -> tuple[CreditExposure, ...]:
    return tuple(
        CreditExposure(
            fact.category,
            "corporates",
            fact.amount,
            Decimal("2"),
            Decimal("45"),
            Decimal("100"),
            credit_category=f"{fact.category}:{fact.risk_weight_code}",
        )
        for fact in sample_bank_latest_facts()
        if fact.fact_group == "loan_exposure" and fact.category == "corporate_unrated"
    )


def test_credit_migration_erodes_car_from_rwa_not_only_pnl() -> None:
    """BoG CRD (June 2018) ¶98: migration raises affected credit RWA independently of P&L."""
    baseline = project_enterprise(_inputs(severe_paths(), facts=credit_basis_facts()))
    uplifted = project_enterprise(
        _inputs(severe_paths(), facts=credit_basis_facts(), credit_exposures=_overlay_book())
    )
    for base_year, up_year in zip(baseline.stress, uplifted.stress, strict=True):
        assert up_year.rwa.credit_rwa > base_year.rwa.credit_rwa
        assert up_year.ratios.car_pct < base_year.ratios.car_pct
    assert uplifted.base == baseline.base
    assert uplifted.current == baseline.current


def test_flat_credit_scenario_is_a_no_op() -> None:
    """BoG CRD (June 2018) ¶98: neutral exposure stress preserves every capital line."""
    baseline = project_enterprise(_inputs(base_paths(), facts=credit_basis_facts()))
    unit = project_enterprise(
        _inputs(base_paths(), facts=credit_basis_facts(), credit_exposures=_overlay_book())
    )
    assert unit == baseline


def _staged_loss_inputs(
    stages: tuple[tuple[int, Decimal], ...],
    paths: tuple[MacroPathPoint, ...],
    *,
    tax_pct: Decimal = Decimal("0"),
    nim_pct: Decimal = Decimal("0"),
) -> EnterpriseProjectionInputs:
    """Isolate credit loss using a prescribed operational-RWA share for zero-income plans."""
    loans = sum((amount for _, amount in stages), Decimal("0"))
    facts: list[ForecastFact] = []
    for fact in sample_bank_latest_facts():
        if fact.fact_group == "loan_exposure":
            continue
        adjusted_fact = fact
        if (fact.fact_group == "balance_sheet" and fact.category.startswith("securities_")) or (
            fact.fact_group == "securities" and not fact.cash_derived
        ):
            adjusted_fact = replace(fact, amount=Decimal("0"))
        elif fact.category == "loans_gross":
            adjusted_fact = replace(fact, amount=loans)
        facts.append(adjusted_fact)
    params = bog_forecast_params()
    return replace(
        _inputs(paths),
        params=replace(
            params,
            capital=replace(params.capital, rwa_pct_of_credit_rwa={"operational": Decimal("10")}),
        ),
        facts=(
            *facts,
            ForecastFact("loan_exposure", "corporate_unrated", loans, risk_weight_code="RW100"),
            *(
                ForecastFact("ecl_exposure", f"corporate_unrated:stage{stage}", amount)
                for stage, amount in stages
            ),
        ),
        ecl_assumptions=tuple(
            EclAssumption("ALL", stage, Decimal("2"), Decimal("40")) for stage, _ in stages
        ),
        plan=replace(
            BASE_ASSUMPTIONS,
            loan_growth_pct=Decimal("0"),
            deposit_growth_pct=Decimal("0"),
            nim_pct=nim_pct,
            cost_to_income_pct=Decimal("0"),
            credit_loss_rate_pct=Decimal("1"),
            fx_depreciation_pct=Decimal("0"),
            dividend_payout_pct=Decimal("0"),
            fee_income_pct_assets=Decimal("0"),
            tax_rate_pct=tax_pct,
            securities_shift_pp=Decimal("0"),
        ),
    )


@pytest.mark.parametrize(
    "balance",
    (Decimal("5000000.000060"), Decimal("5000000.000040")),
)
@pytest.mark.parametrize("source_complete", (True, None))
def test_annual_coverage_accepts_independently_rounded_stage_buckets(
    balance: Decimal,
    source_complete: bool | None,
) -> None:
    """Basis: Prudential staged EAD; four-place bucket rounding is not partial coverage."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    inputs = _staged_loss_inputs(((1, balance), (2, balance)), paths)
    inputs = replace(
        inputs,
        facts=tuple(
            replace(fact, ecl_coverage_complete=source_complete)
            if fact.fact_group == "ecl_exposure"
            else fact
            for fact in inputs.facts
        ),
    )
    result = project_enterprise(inputs)
    assert result.current.balance_sheet.loans == Decimal("10000000.0001")
    for base, stress in zip(result.base, result.stress, strict=True):
        assert stress.pnl.incremental_credit_losses == Decimal("6000.0000")
        assert stress.pnl.credit_losses - base.pnl.credit_losses == Decimal("6000.0000")
        assert base.ratios.cet1_capital - stress.ratios.cet1_capital == (
            Decimal("6000.0000") * stress.year
        )


@pytest.mark.parametrize("gap", (Decimal("-0.0003"), Decimal("0.0003")))
@pytest.mark.parametrize("source_complete", (True, None))
def test_annual_coverage_refuses_gaps_beyond_bucket_quantization(
    gap: Decimal,
    source_complete: bool | None,
) -> None:
    """Basis: Prudential staged EAD; two buckets permit at most a 0.0002 EAD difference."""
    inputs = _staged_loss_inputs(((1, Decimal("5000000")), (2, Decimal("5000000"))), base_paths())
    inputs = replace(
        inputs,
        facts=tuple(
            replace(
                fact,
                amount=fact.amount + gap if fact.fact_group == "loan_exposure" else fact.amount,
                ecl_coverage_complete=(
                    source_complete
                    if fact.fact_group == "ecl_exposure"
                    else fact.ecl_coverage_complete
                ),
            )
            for fact in inputs.facts
        ),
    )
    with pytest.raises(ProjectionInputError) as exc:
        project_enterprise(inputs)
    assert exc.value.code == "ecl_coverage_incomplete"


@pytest.mark.parametrize("gap", (Decimal("-0.0002"), Decimal("0.0002")))
def test_annual_coverage_accepts_the_bucket_quantization_bound(gap: Decimal) -> None:
    """Basis: Prudential staged EAD; a two-bucket rounding tolerance is inclusive."""
    inputs = _staged_loss_inputs(((1, Decimal("5000000")), (2, Decimal("5000000"))), base_paths())
    inputs = replace(
        inputs,
        facts=tuple(
            replace(fact, amount=fact.amount + gap) if fact.fact_group == "loan_exposure" else fact
            for fact in inputs.facts
        ),
    )
    result = project_enterprise(inputs)
    assert result.current.balance_sheet.loans == Decimal("10000000") + gap
    assert all(row.pnl.incremental_credit_losses == Decimal("0") for row in result.base)


@pytest.mark.parametrize("balance", (Decimal("5000000.000060"), Decimal("5000000.000040")))
def test_annual_rounding_tolerance_never_overrides_incomplete_source_coverage(
    balance: Decimal,
) -> None:
    """Basis: Prudential staged EAD; an incomplete source verdict always blocks stress."""
    inputs = _staged_loss_inputs(((1, balance), (2, balance)), base_paths())
    inputs = replace(
        inputs,
        facts=tuple(
            replace(fact, ecl_coverage_complete=False)
            if fact.fact_group == "ecl_exposure"
            else fact
            for fact in inputs.facts
        ),
    )
    with pytest.raises(ProjectionInputError) as exc:
        project_enterprise(inputs)
    assert exc.value.code == "ecl_coverage_incomplete"


@pytest.mark.parametrize(
    ("staged_ead", "source_complete", "has_register"),
    (
        (Decimal("1000000"), None, False),
        (Decimal("1000000"), False, False),
        (Decimal("1000000"), None, True),
        (Decimal("1000000"), True, True),
        (Decimal("10000000"), False, True),
        (Decimal("10000000"), True, False),
        (Decimal("11000000"), True, True),
    ),
)
def test_annual_staged_losses_require_complete_coverage_and_a_register(
    staged_ead: Decimal,
    source_complete: bool | None,
    has_register: bool,
) -> None:
    """Basis: Prudential stress; partial staging must never reduce the assessed loan book."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    inputs = _staged_loss_inputs(((1, Decimal("10000000")),), paths)
    inputs = replace(
        inputs,
        facts=tuple(
            replace(fact, amount=staged_ead, ecl_coverage_complete=source_complete)
            if fact.fact_group == "ecl_exposure"
            else fact
            for fact in inputs.facts
        ),
        ecl_assumptions=inputs.ecl_assumptions if has_register else (),
    )
    with pytest.raises(ProjectionInputError) as exc:
        project_enterprise(inputs)
    assert exc.value.code == "ecl_coverage_incomplete"


def test_annual_unstaged_book_uses_the_whole_book_proxy_without_a_register() -> None:
    """Basis: Conservative prudential proxy; all 10M EAD incurs the 6,000 macro increment."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    inputs = _staged_loss_inputs(((1, Decimal("10000000")),), paths)
    result = project_enterprise(
        replace(
            inputs,
            facts=tuple(fact for fact in inputs.facts if fact.fact_group != "ecl_exposure"),
            ecl_assumptions=(),
        )
    )
    for base, stress in zip(result.base, result.stress, strict=True):
        assert stress.pd_multiplier == Decimal("1.06")
        assert stress.pnl.incremental_credit_losses == Decimal("6000")
        assert stress.pnl.credit_losses - base.pnl.credit_losses == Decimal("6000")


def test_annual_stage3_only_book_has_no_incremental_charge() -> None:
    """Basis: Prudential stress; stage 3 retains plan losses without an incremental charge."""
    paths = tuple(
        replace(point, stress_value=Decimal("0"))
        if point.variable == "gdp_growth" and point.year_index > 0
        else point
        for point in base_paths()
    )
    result = project_enterprise(_staged_loss_inputs(((3, Decimal("10000000")),), paths))
    expected_losses = (Decimal("95000"), Decimal("90250"), Decimal("85737.5"))
    initial_cet1 = result.current.ratios.cet1_capital
    total_loss = Decimal("0")
    for row, expected in zip(result.stress, expected_losses, strict=True):
        total_loss += expected
        assert row.pd_multiplier == Decimal("1.15")
        assert row.lgd_multiplier == Decimal("1.075")
        assert row.pnl.incremental_credit_losses == Decimal("0")
        assert row.pnl.credit_losses == expected
        assert row.pnl.net_income == -expected
        assert row.ratios.cet1_capital == initial_cet1 - total_loss


def test_annual_mixed_stage_charge_grows_with_general_ead_only() -> None:
    """Basis: Prudential stress; growing stage 1+2 EAD alone drives the macro increment."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    inputs = _staged_loss_inputs(
        ((1, Decimal("4000000")), (2, Decimal("2000000")), (3, Decimal("4000000"))), paths
    )
    inputs = replace(inputs, plan=replace(inputs.plan, loan_growth_pct=Decimal("10")))
    result = project_enterprise(inputs)
    for base, stress, expected in zip(
        result.base,
        result.stress,
        (Decimal("3960"), Decimal("4356"), Decimal("4791.6")),
        strict=True,
    ):
        assert stress.pnl.incremental_credit_losses == expected
        assert stress.pnl.credit_losses - base.pnl.credit_losses == expected
        assert base.pnl.incremental_credit_losses == Decimal("0")


@pytest.mark.parametrize(
    ("tax_pct", "nim_pct", "expected_tax"),
    (
        (Decimal("25"), Decimal("10"), Decimal("225000")),
        (Decimal("25"), Decimal("1"), Decimal("0")),
        (Decimal("25"), Decimal("0"), Decimal("0")),
        (Decimal("0"), Decimal("10"), Decimal("0")),
        (Decimal("100"), Decimal("10"), Decimal("900000")),
    ),
)
def test_annual_incremental_loss_reaches_cet1_without_a_plan_tax_shield(
    tax_pct: Decimal,
    nim_pct: Decimal,
    expected_tax: Decimal,
) -> None:
    """Basis: Conservative prudential stress; ungoverned plan tax cannot shield extra loss."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    result = project_enterprise(
        _staged_loss_inputs(((1, Decimal("10000000")),), paths, tax_pct=tax_pct, nim_pct=nim_pct)
    )
    for base, stress in zip(result.base, result.stress, strict=True):
        assert base.pnl.credit_losses == Decimal("100000")
        assert stress.pnl.credit_losses == Decimal("106000")
        assert stress.pnl.incremental_credit_losses == Decimal("6000")
        assert stress.pnl.tax == base.pnl.tax == expected_tax
        assert base.pnl.net_income - stress.pnl.net_income == Decimal("6000")
        assert base.ratios.cet1_capital - stress.ratios.cet1_capital == (
            Decimal("6000") * stress.year
        )


def test_annual_tax_and_gross_loss_reach_appendix_ii() -> None:
    """Basis: Prudential Appendix II; a 6,000 incremental loss carries no 1,500 tax relief."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.09"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    result = project_enterprise(
        _staged_loss_inputs(
            ((1, Decimal("10000000")),), paths, tax_pct=Decimal("25"), nim_pct=Decimal("10")
        )
    )
    appendix = build_appendix_ii(
        result,
        paths,
        currency="GHS",
        car_target_pct=Decimal("13"),
        recognition_caps=RecognitionCaps(Decimal("1.5"), Decimal("2")),
    )
    row = next(row for row in appendix.table3_profit_and_loss.rows if row.label == "stress_y1")
    assert row.impairment_losses == Decimal("106.000")
    assert row.profit_before_tax == Decimal("894.000")
    assert row.tax == Decimal("225.000")
    assert row.profit_after_tax == Decimal("669.000")
    assert row.adjusted_retained_earnings_for_car == Decimal("669.000")


def test_annual_benign_macro_never_credits_lower_ecl_to_capital() -> None:
    """Basis: Conservative prudential stress; a lower modelled cost of risk gives no credit."""
    paths = tuple(
        replace(point, stress_value=Decimal("0.03"))
        if point.variable == "unemployment" and point.year_index > 0
        else point
        for point in base_paths()
    )
    result = project_enterprise(_staged_loss_inputs(((2, Decimal("10000000")),), paths))
    for base, stress in zip(result.base, result.stress, strict=True):
        assert stress.pd_multiplier == Decimal("1")
        assert stress.pnl.incremental_credit_losses == Decimal("0")
        assert stress.pnl == base.pnl
        assert stress.ratios.cet1_capital == base.ratios.cet1_capital


@pytest.mark.parametrize("rate_shock", [False, True])
def test_net_credit_basis_tracks_growth_and_year_one_haircut_on_both_legs(rate_shock: bool) -> None:
    """BoG CRD (June 2018) ¶98, ¶107: projected credit follows the matching asset book."""
    facts = list(sample_bank_latest_facts())
    for fact in tuple(facts):
        if fact.fact_group == "loan_exposure":
            code = "RW100" if fact.category == "sme_retail" else fact.risk_weight_code
            amount = (
                fact.amount - Decimal("60000000")
                if fact.category == "corporate_unrated"
                else fact.amount
            )
            facts.append(
                ForecastFact(
                    "credit_exposure", f"{fact.category}:{code}", amount, risk_weight_code=code
                )
            )
    facts.append(
        ForecastFact(
            "credit_exposure", "other_assets:RW100", Decimal("90000000"), risk_weight_code="RW100"
        )
    )
    facts.append(
        ForecastFact(
            "credit_exposure",
            "securities:domestic_sovereign:gog:RW20",
            Decimal("620000000"),
            risk_weight_code="RW20",
        )
    )
    plan = replace(
        BASE_ASSUMPTIONS,
        loan_growth_pct=Decimal("10"),
        deposit_growth_pct=Decimal("20"),
        securities_shift_pp=Decimal("0"),
    )
    paths = tuple(
        replace(point, stress_value=point.base_value + Decimal("0.05"))
        if rate_shock and point.variable == "interest_rate" and point.year_index > 0
        else point
        for point in base_paths()
    )
    projection = project_enterprise(_inputs(paths, facts=facts, plan=plan))
    loan_rwa = Decimal("1172500000")
    off_balance_rwa = Decimal("150000000")
    for leg, haircut in (
        (projection.base, Decimal("1")),
        (projection.stress, Decimal("0.825") if rate_shock else Decimal("1")),
    ):
        for year in leg:
            expected = (
                (loan_rwa + off_balance_rwa) * Decimal("1.1") ** year.year
                + Decimal("124000000") * Decimal("1.2") ** year.year * haircut
                + Decimal("90000000")
            )
            assert year.rwa.credit_rwa == expected.quantize(Decimal("0.0001"))
    assert projection.current.rwa.credit_rwa == loan_rwa + off_balance_rwa + Decimal("214000000")
