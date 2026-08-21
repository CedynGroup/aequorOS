"""Hand-verified tests for the operational-risk scenario simulation (Phase 4 item 3).

Goldens are computed against the documented default severities (income-loss %
of gross income + fixed incident cost; outflow % of the liquidity base).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.stress.operational import (
    OPERATIONAL_SCENARIOS,
    OperationalInputs,
    OperationalSeverity,
    compute_operational,
)

_GROSS_INCOME = Decimal("100000000")
_LIQUIDITY_BASE = Decimal("50000000")


def _inputs(**overrides: object) -> OperationalInputs:
    defaults: dict[str, object] = {
        "annual_gross_income": _GROSS_INCOME,
        "liquidity_base": _LIQUIDITY_BASE,
        "total_capital": Decimal("200000000"),
        "total_rwa": Decimal("1000000000"),
    }
    defaults.update(overrides)
    return OperationalInputs(**defaults)  # type: ignore[arg-type]


def test_all_seven_prescribed_scenarios_are_simulated() -> None:
    result = compute_operational(_inputs())
    assert [row.scenario for row in result.scenarios] == list(OPERATIONAL_SCENARIOS)
    assert len(result.scenarios) == 7


def test_per_scenario_losses_are_hand_derived() -> None:
    result = compute_operational(_inputs())
    by_name = {row.scenario: row for row in result.scenarios}
    # cloud outage: 100M×4% + 2M = 6M; outflow 50M×3% = 1.5M.
    assert by_name["cloud_outage"].loss_ghs == Decimal("6000000.0000")
    assert by_name["cloud_outage"].liquidity_outflow_ghs == Decimal("1500000.0000")
    # cyber/data corruption: 100M×12% + 15M = 27M.
    assert by_name["cyber_data_corruption"].loss_ghs == Decimal("27000000.0000")
    # payments outage: 100M×8% + 6M = 14M.
    assert by_name["payments_outage"].loss_ghs == Decimal("14000000.0000")


def test_worst_scenario_drives_capital_and_aggregate_is_reported() -> None:
    result = compute_operational(_inputs())
    # Worst single event = cyber/data corruption at 27M (the capital hit).
    assert result.worst_scenario == "cyber_data_corruption"
    assert result.worst_loss_ghs == Decimal("27000000.0000")
    assert result.pillar2_operational_charge == Decimal("27000000.0000")
    # Combined tail = sum of all seven losses = 82.5M.
    assert result.aggregate_loss_ghs == Decimal("82500000.0000")
    # Worst liquidity outflow = civil strife at 50M×12% = 6M.
    assert result.worst_liquidity_outflow_ghs == Decimal("6000000.0000")


def test_capital_coupling_expresses_a_car_impact() -> None:
    result = compute_operational(_inputs())
    # base CAR = 200M/1000M = 20%; stressed = (200M−27M)/1000M = 17.3%.
    assert result.base_car_pct == Decimal("20.000000")
    assert result.stressed_car_pct == Decimal("17.300000")
    assert result.car_impact_pp == Decimal("-2.700000")


def test_car_impact_is_none_without_capital_bases() -> None:
    result = compute_operational(
        OperationalInputs(annual_gross_income=_GROSS_INCOME, liquidity_base=_LIQUIDITY_BASE)
    )
    assert result.base_car_pct is None
    assert result.stressed_car_pct is None
    assert result.worst_loss_ghs == Decimal("27000000.0000")


def test_severity_override_changes_the_worst_scenario() -> None:
    override = {
        "cloud_outage": OperationalSeverity(
            scenario="cloud_outage",
            income_loss_pct=Decimal("50"),
            fixed_cost_ghs=Decimal("100000000"),
            liquidity_outflow_pct=Decimal("30"),
            duration_days=30,
        )
    }
    result = compute_operational(_inputs(severities=override))
    # cloud outage now 100M×50% + 100M = 150M, the new worst.
    assert result.worst_scenario == "cloud_outage"
    assert result.worst_loss_ghs == Decimal("150000000.0000")


def test_reproducible() -> None:
    first = compute_operational(_inputs()).serialize()
    second = compute_operational(_inputs()).serialize()
    assert first == second
