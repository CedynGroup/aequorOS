"""Hand-verified tests for the pure enterprise-stress orchestrator (Phase 2 item 1).

Every expected value is derived independently with explicit Decimal literals
against the documented composition coefficients, so the goldens are never
engine-self-referential.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.capital.ecl import EclAssumption, EclExposure
from app.domain.fx.engine import FxPosition
from app.domain.irr.engine import IrrPosition
from app.domain.stress.concentration import ConcentrationExposure, ConcentrationInputs
from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    DerivativePosition,
)
from app.domain.stress.credit_bottom_up import BottomUpCreditInputs, CreditExposure
from app.domain.stress.operational import OperationalConfig
from app.domain.stress.orchestrator import (
    EnterpriseStressInputs,
    FxStressInputs,
    IrrStressInputs,
    compose_capital_shocks,
    run_enterprise_stress,
)
from tests.domain.stress_fixtures import base_paths as base_macro_paths
from tests.domain.stress_fixtures import (
    bog_capital_params,
    bog_liquidity_params,
    capital_facts,
    liquidity_facts,
    severe_paths,
)

_BASELINE_PREPROVISION_INCOME = Decimal("180000000")
_BASELINE_CREDIT_LOSS = Decimal("14000000")
_BASELINE_ALLOWANCE = Decimal("15000000")


def _inputs(paths, **overrides) -> EnterpriseStressInputs:
    defaults = {
        "scenario_code": "SEVERE-2027",
        "scenario_paths": paths,
        "capital_facts": capital_facts(),
        "capital_params": bog_capital_params(),
        "liquidity_facts": liquidity_facts(),
        "liquidity_params": bog_liquidity_params(),
        "baseline_annual_preprovision_income": _BASELINE_PREPROVISION_INCOME,
        "baseline_annual_credit_loss": _BASELINE_CREDIT_LOSS,
        "baseline_credit_allowance": _BASELINE_ALLOWANCE,
    }
    defaults.update(overrides)
    return EnterpriseStressInputs(**defaults)  # type: ignore[arg-type]


def test_capital_path_composition_is_hand_derived() -> None:
    composition = compose_capital_shocks(
        scenario_paths=severe_paths(),
        baseline_annual_preprovision_income=_BASELINE_PREPROVISION_INCOME,
        baseline_annual_credit_loss=_BASELINE_CREDIT_LOSS,
        baseline_credit_allowance=_BASELINE_ALLOWANCE,
    )
    # translate("capital") for the severe scenario: pd = 1.21, lgd = 1.195.
    assert composition.pd_multiplier == Decimal("1.21")
    assert composition.lgd_multiplier == Decimal("1.195")
    # quarterly_rwa_growth_pct = 5 × fx_frac(0.20) + 2 × pd_uplift(0.21) = 1.42.
    assert composition.stressed["quarterly_rwa_growth_pct"] == Decimal("1.4200")
    # fx_rwa_multiplier = 1 + 1 × 0.20.
    assert composition.stressed["fx_rwa_multiplier"] == Decimal("1.2000")
    # income factor = 1 − 3.0×0.05 − 2.0×0.05 = 0.75; after-tax 180M×0.75×0.75/4/M.
    assert composition.income_stress_factor == Decimal("0.75")
    assert composition.stressed["quarterly_income_m"] == Decimal("25.3125")
    assert composition.baseline["quarterly_income_m"] == Decimal("33.7500")
    # allowance proxy: ecl_stress = 15M × 1.21 × 1.195 = 21,689,250; incr = 6,689,250.
    assert composition.ecl_source == "allowance_proxy"
    assert composition.ecl_base == Decimal("15000000.0000")
    assert composition.ecl_stress == Decimal("21689250.0000")
    assert composition.annual_incremental_credit_loss == Decimal("6689250.0000")
    # quarterly credit loss = (14,000,000 + 6,689,250) / 4 / 1e6 = 5.1723 (4dp).
    assert composition.stressed["quarterly_credit_loss_m"] == Decimal("5.1723")
    assert composition.baseline["quarterly_credit_loss_m"] == Decimal("3.5000")


def test_compose_uses_the_ecl_engine_when_staged_exposures_are_supplied() -> None:
    exposures = (EclExposure(segment="corporate", stage=1, ead=Decimal("100000000")),)
    assumptions = (
        EclAssumption(
            segment="corporate", stage=1, pd_pct=Decimal("2"), lgd_pct=Decimal("45")
        ),
    )
    composition = compose_capital_shocks(
        scenario_paths=severe_paths(),
        baseline_annual_preprovision_income=_BASELINE_PREPROVISION_INCOME,
        ecl_exposures=exposures,
        ecl_assumptions=assumptions,
    )
    assert composition.ecl_source == "ecl_engine"
    # base ECL = 100M × 2% × 45% = 900,000.
    assert composition.ecl_base == Decimal("900000.0000")
    # stress ECL = 100M × (2%×1.21) × (45%×1.195) = 100M × 0.0242 × 0.53775.
    assert composition.ecl_stress == Decimal("1301355.0000")
    assert composition.annual_incremental_credit_loss == Decimal("401355.0000")


def test_base_scenario_produces_zero_enterprise_delta() -> None:
    outcome = run_enterprise_stress(
        _inputs(
            base_macro_paths(),
            irr=IrrStressInputs(
                positions=_irr_positions(), curve=_curve(), tier1=Decimal("300000000")
            ),
            fx=FxStressInputs(
                positions=_fx_positions(),
                tier1=Decimal("300000000"),
                single_limit_pct=Decimal("10"),
                aggregate_limit_pct=Decimal("20"),
            ),
        )
    )
    # Composed stress keys equal the neutral baseline keys.
    assert outcome.capital.composition.stressed == outcome.capital.composition.baseline
    assert outcome.capital.car_erosion_pp == Decimal("0.000000")
    assert outcome.liquidity.lcr_erosion_pp == Decimal("0.000000")
    assert outcome.liquidity.nsfr_erosion_pp == Decimal("0.000000")
    assert outcome.irr is not None
    assert outcome.irr.delta_eve == Decimal("0.0000")
    assert outcome.fx is not None
    assert outcome.fx.base_nop_pct_tier1 == outcome.fx.stressed_nop_pct_tier1


def test_severe_scenario_couples_solvency_and_liquidity() -> None:
    outcome = run_enterprise_stress(_inputs(severe_paths()))
    # Both a baseline and a stressed four-quarter capital path are produced.
    assert len(outcome.capital.baseline.path) == 5
    assert len(outcome.capital.stressed.path) == 5
    # Stress erodes solvency capital vs the expected baseline path.
    assert outcome.capital.stressed_car_end_pct < outcome.capital.baseline_car_end_pct
    assert outcome.capital.car_erosion_pp < Decimal("0")
    # Stress erodes liquidity (run-off + HQLA haircut + reduced inflows).
    assert outcome.liquidity.stressed_lcr.lcr_pct < outcome.liquidity.baseline_lcr.lcr_pct
    assert outcome.liquidity.lcr_erosion_pp < Decimal("0")
    # The coupling reports both axes against their floors.
    coupling = outcome.coupling
    assert coupling.car_min_pct == bog_capital_params().car_min_pct
    assert coupling.lcr_min_pct == bog_liquidity_params().lcr_min_pct
    assert coupling.stressed_car_end_pct == outcome.capital.stressed_car_end_pct
    assert coupling.stressed_lcr_pct == outcome.liquidity.stressed_lcr.lcr_pct


def test_irr_and_fx_move_under_the_severe_scenario() -> None:
    outcome = run_enterprise_stress(
        _inputs(
            severe_paths(),
            irr=IrrStressInputs(
                positions=_irr_positions(), curve=_curve(), tier1=Decimal("300000000")
            ),
            fx=FxStressInputs(
                positions=_fx_positions(),
                tier1=Decimal("300000000"),
                single_limit_pct=Decimal("10"),
                aggregate_limit_pct=Decimal("20"),
            ),
        )
    )
    assert outcome.irr is not None
    # +500bp parallel shift (interest_rate delta 0.05 × 10000).
    assert outcome.irr.parallel_bp == Decimal("500.0000")
    # A positive-duration-gap book loses economic value when rates rise.
    assert outcome.irr.delta_eve < Decimal("0")
    assert outcome.fx is not None
    # 20% cedi depreciation inflates the open FX position vs Tier 1.
    assert outcome.fx.shock_pct == Decimal("20.00")
    assert outcome.fx.stressed_nop_pct_tier1 > outcome.fx.base_nop_pct_tier1


def test_run_is_reproducible() -> None:
    first = run_enterprise_stress(_inputs(severe_paths())).serialize()
    second = run_enterprise_stress(_inputs(severe_paths())).serialize()
    assert first == second


# --- Phase 4: per-risk sections wired into the enterprise run ----------------


def _phase4_overrides() -> dict[str, object]:
    return {
        "bottom_up_credit": BottomUpCreditInputs(
            exposures=(
                CreditExposure("L1", "corporates", Decimal("100000000"), Decimal("2"),
                               Decimal("45"), Decimal("100")),
                CreditExposure("L2", "banks", Decimal("30000000"), Decimal("1"),
                               Decimal("30"), Decimal("20"), is_foreign_currency=True),
            )
        ),
        "concentration": ConcentrationInputs(
            exposures=(
                ConcentrationExposure("L1", Decimal("100000000"), "cp:Big", sector="agri"),
                ConcentrationExposure("L2", Decimal("30000000"), "cp:Bank", sector="fi"),
            ),
            base_credit_rwa=Decimal("130000000"),
        ),
        "operational": OperationalConfig(annual_gross_income=Decimal("120000000")),
        "contingent_leverage": ContingentLeverageInputs(
            base_leverage_exposure=Decimal("2000000000"),
            tier1=Decimal("300000000"),
            derivatives=(DerivativePosition("D1", Decimal("100000000"), Decimal("20000000")),),
        ),
    }


def test_phase4_sections_populate_under_a_severe_scenario() -> None:
    outcome = run_enterprise_stress(_inputs(severe_paths(), **_phase4_overrides()))
    assert outcome.bottom_up_credit is not None
    assert outcome.bottom_up_credit.stressed_credit_rwa > outcome.bottom_up_credit.base_credit_rwa
    assert outcome.bottom_up_credit.credit_rwa_uplift_factor > Decimal("1")
    assert outcome.concentration is not None
    assert outcome.concentration.total_incremental_loss > Decimal("0")
    assert outcome.operational is not None
    assert outcome.operational.worst_loss_ghs > Decimal("0")
    # Operational is coupled to capital (a CAR impact is expressed).
    assert outcome.operational.car_impact_pp is not None
    assert outcome.contingent_leverage is not None
    assert (
        outcome.contingent_leverage.stressed_leverage_exposure
        > outcome.contingent_leverage.base_leverage_exposure
    )


def test_phase4_sections_absent_by_default() -> None:
    outcome = run_enterprise_stress(_inputs(severe_paths()))
    assert outcome.bottom_up_credit is None
    assert outcome.concentration is None
    assert outcome.operational is None
    assert outcome.contingent_leverage is None


def test_phase4_bottom_up_is_neutral_under_a_base_scenario() -> None:
    outcome = run_enterprise_stress(_inputs(base_macro_paths(), **_phase4_overrides()))
    assert outcome.bottom_up_credit is not None
    assert outcome.bottom_up_credit.credit_rwa_uplift_factor == Decimal("1")
    assert outcome.bottom_up_credit.incremental_expected_loss == Decimal("0")


def test_phase4_run_serializes_and_is_reproducible() -> None:
    first = run_enterprise_stress(_inputs(severe_paths(), **_phase4_overrides())).serialize()
    second = run_enterprise_stress(_inputs(severe_paths(), **_phase4_overrides())).serialize()
    assert first == second
    # The full serialized outcome (Phase-4 sections included) is JSON-safe.
    json.dumps(first)
    assert {"bottom_up_credit", "concentration", "operational", "contingent_leverage"} <= set(
        first
    )


def _curve() -> dict[Decimal, Decimal]:
    return {
        Decimal("0.003"): Decimal("20"),
        Decimal("0.014"): Decimal("20"),
        Decimal("0.06"): Decimal("20"),
        Decimal("0.17"): Decimal("21"),
        Decimal("0.38"): Decimal("21"),
        Decimal("0.75"): Decimal("22"),
        Decimal("1.9"): Decimal("22"),
        Decimal("4.0"): Decimal("23"),
        Decimal("7.0"): Decimal("24"),
    }


def _irr_positions() -> tuple[IrrPosition, ...]:
    # Long-dated assets, short-dated liabilities → positive duration gap.
    return (
        IrrPosition(
            side="asset",
            bucket="3-5y",
            amount=Decimal("500000000"),
            rate_pct=Decimal("23"),
            fixed_or_float="fixed",
            midpoint_years=Decimal("4.0"),
            source="loans",
        ),
        IrrPosition(
            side="liability",
            bucket="1-3m",
            amount=Decimal("450000000"),
            rate_pct=Decimal("21"),
            fixed_or_float="fixed",
            midpoint_years=Decimal("0.17"),
            source="deposits",
        ),
    )


def _fx_positions() -> tuple[FxPosition, ...]:
    return (
        FxPosition(
            currency="USD",
            net_ghs=Decimal("40000000"),
            spot_ghs=Decimal("12.5"),
            net_ccy=Decimal("3200000"),
            assets_ccy=Decimal("5000000"),
            liabilities_ccy=Decimal("1800000"),
            net_derivatives_ccy=Decimal("0"),
        ),
    )
