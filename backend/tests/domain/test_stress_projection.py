"""Hand-verified tests for the 3-year enterprise stress projection (Phase 2 item 2).

The base leg is proven byte-identical to the independently-tested forecasting
engine (so the reuse is correct, not self-referential); the stress leg is proven
directionally and through its hand-derived per-year ECL multipliers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.forecasting.engine import project
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    ProjectionInputError,
    money,
    project_enterprise,
)
from app.domain.stress.translation import MacroPathPoint
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_forecast_params,
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
    forecast = project(
        sample_bank_latest_facts(), bog_forecast_params(), BASE_ASSUMPTIONS, years=3
    )
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

    paths = (
        gdp(0, "0.05"),
        gdp(1, "0.00"),  # delta −0.05 ⇒ pd 1.15
        gdp(2, "0.01"),  # delta −0.04 ⇒ pd 1.12
        gdp(3, "0.03"),  # delta −0.02 ⇒ pd 1.06
    )
    projection = project_enterprise(_inputs(paths))
    assert projection.stress[0].pd_multiplier == Decimal("1.15")
    assert projection.stress[1].pd_multiplier == Decimal("1.12")
    assert projection.stress[2].pd_multiplier == Decimal("1.06")


def test_minima_are_assessed_against_all_floors() -> None:
    # A paid-up floor above the bank's 150M paid-up capital breaches immediately.
    projection = project_enterprise(
        _inputs(severe_paths(), paid_up_min=Decimal("400000000"))
    )
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


def test_credit_rwa_uplift_erodes_car_from_rwa_not_only_pnl() -> None:
    """Phase 4: the bottom-up migration + FX uplift raises stress RWA → lower CAR."""
    baseline = project_enterprise(_inputs(severe_paths()))
    uplifted = project_enterprise(
        _inputs(severe_paths(), credit_rwa_uplift={1: Decimal("1.2"), 2: Decimal("1.2"),
                                                   3: Decimal("1.2")})
    )
    for base_year, up_year in zip(baseline.stress, uplifted.stress, strict=True):
        # Stress-leg credit RWA scales by the uplift factor; total RWA follows.
        assert up_year.rwa.credit_rwa == money(base_year.rwa.credit_rwa * Decimal("1.2"))
        assert up_year.rwa.total_rwa > base_year.rwa.total_rwa
        # Higher RWA with unchanged capital ⇒ a strictly lower CAR (RWA erosion).
        assert up_year.ratios.car_pct < base_year.ratios.car_pct
    # The base leg and the as-of snapshot are never touched by the overlay.
    for base_year, up_year in zip(baseline.base, uplifted.base, strict=True):
        assert up_year.rwa.total_rwa == base_year.rwa.total_rwa
        assert up_year.ratios.car_pct == base_year.ratios.car_pct
    assert uplifted.current.rwa.total_rwa == baseline.current.rwa.total_rwa


def test_unit_credit_rwa_uplift_is_a_no_op() -> None:
    baseline = project_enterprise(_inputs(severe_paths()))
    unit = project_enterprise(
        _inputs(severe_paths(), credit_rwa_uplift={1: Decimal("1"), 2: Decimal("1"),
                                                   3: Decimal("1")})
    )
    for base_year, unit_year in zip(baseline.stress, unit.stress, strict=True):
        assert unit_year.rwa.total_rwa == base_year.rwa.total_rwa
        assert unit_year.ratios.car_pct == base_year.ratios.car_pct
