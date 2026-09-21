"""Hand-verified tests for the pure enterprise-stress orchestrator (Phase 2 item 1).

Every expected value is derived independently with explicit Decimal literals
against the documented composition coefficients, so the goldens are never
engine-self-referential.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.domain.capital.ecl import EclAssumption, EclExposure
from app.domain.fx.engine import FxPosition
from app.domain.icaap.pillar2 import fx as icaap_fx
from app.domain.irr.engine import IrrPosition
from app.domain.stress import orchestrator
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
    fx_pillar2_addon,
    run_enterprise_stress,
)
from app.domain.stress.translation import MacroPathPoint
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


def test_a_pre_provision_loss_erodes_capital_instead_of_being_clamped_to_zero() -> None:
    """WS-A3 open item 3, closed: the operating loss now reaches the capital path.

    ``compose_capital_shocks`` used to compute the income leg as
    ``max(preprovision, 0) x factor x tax``, so a bank that is loss-making BEFORE
    provisions entered the stress as if it had broken even and its operating loss
    never eroded capital. That is fail-open, and it flattered the most distressed
    institutions hardest.

    Both asymmetries the clamp stood in for are resolved conservatively: the loss
    carries NO tax shield (a distressed bank cannot be assumed to realise the
    deferred tax asset), and the income-compression factor — which is <= 1 and
    would otherwise SHRINK the loss under stress — is not applied to it.
    """
    loss = Decimal("-80000000")
    composition = compose_capital_shocks(
        scenario_paths=severe_paths(),
        baseline_annual_preprovision_income=loss,
        baseline_annual_credit_loss=_BASELINE_CREDIT_LOSS,
        baseline_credit_allowance=_BASELINE_ALLOWANCE,
    )
    # -80,000,000 / 4 / 1e6 = -20 per quarter, on BOTH legs: pre-tax, uncompressed.
    quarterly = Decimal("-20.0000")
    assert composition.stressed["quarterly_income_m"] == quarterly
    assert composition.baseline["quarterly_income_m"] == quarterly
    # The severe scenario still compresses income in general ...
    assert composition.income_stress_factor == Decimal("0.75")
    # ... it just never makes a loss smaller.
    assert composition.stressed["quarterly_income_m"] <= composition.baseline["quarterly_income_m"]

    # A profitable bank is untouched: same arithmetic, same figures as before.
    profit = compose_capital_shocks(
        scenario_paths=severe_paths(),
        baseline_annual_preprovision_income=_BASELINE_PREPROVISION_INCOME,
        baseline_annual_credit_loss=_BASELINE_CREDIT_LOSS,
        baseline_credit_allowance=_BASELINE_ALLOWANCE,
    )
    assert profit.stressed["quarterly_income_m"] == Decimal("25.3125")
    assert profit.baseline["quarterly_income_m"] == Decimal("33.7500")


def test_compose_uses_the_ecl_engine_when_staged_exposures_are_supplied() -> None:
    exposures = (EclExposure(segment="corporate", stage=1, ead=Decimal("100000000")),)
    assumptions = (
        EclAssumption(segment="corporate", stage=1, pd_pct=Decimal("2"), lgd_pct=Decimal("45")),
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
    assert outcome.liquidity is not None
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
    assert outcome.liquidity is not None
    assert outcome.liquidity.stressed_lcr.lcr_pct < outcome.liquidity.baseline_lcr.lcr_pct
    assert outcome.liquidity.lcr_erosion_pp < Decimal("0")
    # The coupling reports both axes against their floors.
    coupling = outcome.coupling
    assert coupling is not None
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


def test_non_parallel_irr_shocks_move_economic_value() -> None:
    def paths(policy_delta: str, sovereign_delta: str) -> list[MacroPathPoint]:
        sovereign = Decimal(sovereign_delta)
        points = [
            MacroPathPoint(
                variable=point.variable,
                year_index=point.year_index,
                base_value=point.base_value,
                stress_value=(
                    point.stress_value + sovereign
                    if point.variable == "gog_yield"
                    else point.stress_value
                ),
            )
            for point in base_macro_paths()
        ]
        points.extend(
            MacroPathPoint(
                variable="policy_rate",
                year_index=year,
                base_value=Decimal("0.14"),
                stress_value=Decimal("0.14") + Decimal(policy_delta),
            )
            for year in (1, 2, 3)
        )
        return points

    irr = IrrStressInputs(
        positions=_irr_positions(),
        curve=_curve(),
        tier1=Decimal("300000000"),
    )
    short = run_enterprise_stress(
        _inputs(paths("0.025", "0"), scenario_code="short_up_250", irr=irr)
    )
    rotation = run_enterprise_stress(
        _inputs(paths("-0.0065", "0.009"), scenario_code="steepener", irr=irr)
    )

    assert short.irr is not None
    assert short.irr.parallel_bp == Decimal("0")
    assert short.irr.delta_eve != Decimal("0")
    assert rotation.irr is not None
    assert rotation.irr.parallel_bp == Decimal("0")
    assert rotation.irr.delta_eve != Decimal("0")


def test_run_is_reproducible() -> None:
    first = run_enterprise_stress(_inputs(severe_paths())).serialize()
    second = run_enterprise_stress(_inputs(severe_paths())).serialize()
    assert first == second


# --- Phase 4: per-risk sections wired into the enterprise run ----------------


def _phase4_overrides() -> dict[str, object]:
    return {
        "bottom_up_credit": BottomUpCreditInputs(
            exposures=(
                CreditExposure(
                    "L1",
                    "corporates",
                    Decimal("100000000"),
                    Decimal("2"),
                    Decimal("45"),
                    Decimal("100"),
                ),
                CreditExposure(
                    "L2",
                    "banks",
                    Decimal("30000000"),
                    Decimal("1"),
                    Decimal("30"),
                    Decimal("20"),
                    is_foreign_currency=True,
                ),
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
    assert {"bottom_up_credit", "concentration", "operational", "contingent_leverage"} <= set(first)


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


# --- D-038: ONE FX Pillar 2 definition, shared with the ICAAP register --------
#
# The overlay that feeds Appendix II Table 5 used to restate
# ``Tier 1 × max(stressed NOP% − base NOP%, 0)`` — the change in a RATIO, which
# ``app/domain/icaap/pillar2/fx.py`` records as "not a loss (audit M8)". A
# single filed ICAAP therefore carried two different definitions of the FX
# add-on and the source-consistency control compared them against each other
# (audit W3). These tests pin the extraction three ways: the value, the SIGN
# behaviour the old formula got wrong, and that the function really is shared.

_FX_TIER1 = Decimal("300000000")


def _fx_run(positions, paths=None):
    return run_enterprise_stress(
        _inputs(
            paths or severe_paths(),
            fx=FxStressInputs(
                positions=positions,
                tier1=_FX_TIER1,
                single_limit_pct=Decimal("10"),
                aggregate_limit_pct=Decimal("20"),
            ),
        )
    )


def _short_usd() -> tuple[FxPosition, ...]:
    return (
        FxPosition(
            currency="USD",
            net_ghs=Decimal("-40000000"),
            spot_ghs=Decimal("12.5"),
            net_ccy=Decimal("-3200000"),
            assets_ccy=Decimal("1800000"),
            liabilities_ccy=Decimal("5000000"),
            net_derivatives_ccy=Decimal("0"),
        ),
    )


def test_a_long_book_that_gains_as_the_cedi_falls_carries_no_fx_addon() -> None:
    """The case the retired formula got backwards.

    A 20% cedi depreciation makes a LONG foreign-currency book worth more, so
    there is no loss to hold capital against. The retired formula charged
    ``Tier 1 × ΔNOP%`` = GHS 8,000,001 anyway, purely because the position grew
    against an unchanged Tier 1.
    """
    outcome = _fx_run(_fx_positions())
    assert outcome.fx is not None
    retired_formula = _FX_TIER1 * (
        outcome.fx.stressed_nop_pct_tier1 - outcome.fx.base_nop_pct_tier1
    ) / Decimal("100")
    assert retired_formula == Decimal("8000001.000000")
    assert outcome.fx.revaluation_loss == Decimal("0.0000")
    assert outcome.fx.pillar2_addon == Decimal("0.0000")


def test_a_short_book_loses_and_the_addon_is_the_loss_net_of_pillar_one() -> None:
    """40m short, a 20% depreciation ⇒ an 8m loss, of which Pillar 1 holds 4.5m."""
    outcome = _fx_run(_short_usd())
    assert outcome.fx is not None
    assert outcome.fx.shock_pct == Decimal("20.00")
    # 40,000,000 short × 20%.
    assert outcome.fx.revaluation_loss == Decimal("8000000.0000")
    # market RWA 45,000,000 × the fixture's 10% CAR minimum.
    assert outcome.capital.baseline.rwa.market_rwa == Decimal("45000000.0000")
    assert outcome.fx.pillar1_fx_capital == Decimal("4500000.0000")
    assert outcome.fx.pillar2_addon == Decimal("3500000.0000")


def test_the_addon_is_produced_by_the_icaap_function_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sharing proved by behaviour, not by grep: replace the ICAAP function and
    the stress overlay's figure follows it."""
    sentinel = icaap_fx.FxAddOn(
        loss=icaap_fx.RevaluationLoss(
            by_currency={},
            depreciation_loss=Decimal("7"),
            appreciation_loss=None,
            worst_direction=icaap_fx.DEPRECIATION,
            worst_loss=Decimal("7"),
        ),
        pillar1_fx_capital=Decimal("3"),
        addon=Decimal("4"),
    )
    monkeypatch.setattr(
        orchestrator.icaap_fx, "fx_revaluation_addon", lambda *_args, **_kwargs: sentinel
    )

    outcome = _fx_run(_short_usd())

    assert outcome.fx is not None
    assert outcome.fx.pillar2_addon == Decimal("4")
    assert outcome.fx.revaluation_loss == Decimal("7")
    assert outcome.fx.pillar1_fx_capital == Decimal("3")


def test_a_cedi_appreciation_override_is_shocked_the_other_way() -> None:
    """The shipped mapping floors ``ghs_usd_shock_pct`` at zero, so the stress
    framework can only model a depreciation today; the shared helper still
    handles the other sign rather than reading an appreciation as a fall."""
    appreciation = fx_pillar2_addon(
        _fx_positions(),  # 40m LONG USD
        Decimal("-10"),
        Decimal("45000000"),
        bog_capital_params(),
    )
    # A long book loses when the cedi strengthens: 40m × 10% = 4m, inside the
    # 4.5m Pillar 1 charge, so no add-on — but the loss is measured, not zero.
    assert appreciation.loss.worst_direction == "appreciation"
    assert appreciation.loss.worst_loss == Decimal("4000000.0000")
    assert appreciation.pillar1_fx_capital == Decimal("4500000.0000")
    assert appreciation.addon == Decimal("0.0000")
