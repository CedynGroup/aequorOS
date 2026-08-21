from __future__ import annotations

from app.services import cashflow_forecast


def test_adverse_and_severe_scenarios_reduce_behavioural_cash_flow() -> None:
    baseline, _ = cashflow_forecast._scenario_adjustments([100.0, -100.0], "baseline")
    adverse, _ = cashflow_forecast._scenario_adjustments([100.0, -100.0], "adverse")
    severe, _ = cashflow_forecast._scenario_adjustments([100.0, -100.0], "severe")

    assert baseline == [0.0, 0.0]
    assert adverse[0] < 0 and adverse[1] < 0
    assert severe[0] < adverse[0] and severe[1] < adverse[1]


def test_simulated_quantiles_are_ordered_and_reproducible() -> None:
    first = cashflow_forecast._simulated_quantiles(
        [100.0, -50.0], 20.0, mode="lstm", scenario="adverse"
    )
    second = cashflow_forecast._simulated_quantiles(
        [100.0, -50.0], 20.0, mode="lstm", scenario="adverse"
    )

    assert first == second
    assert all(p5 < p50 < p95 for p5, p50, p95 in first)