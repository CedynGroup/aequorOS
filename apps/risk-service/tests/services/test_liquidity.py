from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import CalculationForecastPeriod
from app.services.liquidity import SOURCES_COVERAGE_THRESHOLD, calculate_metrics


def _period(  # noqa: PLR0913
    number: int,
    *,
    cash: str,
    inflows: str,
    outflows: str,
    draw: str = "0",
    repayment: str = "0",
) -> CalculationForecastPeriod:
    return CalculationForecastPeriod(
        organization_id=uuid4(),
        case_id=uuid4(),
        run_id=uuid4(),
        period_number=number,
        period_end=date(2026 + number, 6, 30),
        currency="USD",
        total_assets=Decimal("1000"),
        total_liabilities=Decimal("500"),
        total_equity=Decimal("500"),
        cash=Decimal(cash),
        projected_inflows=Decimal(inflows),
        projected_outflows=Decimal(outflows),
        credit_draw=Decimal(draw),
        debt_repayment=Decimal(repayment),
        components={},
    )


def test_calculates_liquidity_metrics_and_findings_deterministically() -> None:
    result = calculate_metrics(
        [
            _period(1, cash="50", inflows="80", outflows="100", draw="20", repayment="20"),
            _period(2, cash="-30", inflows="70", outflows="100", repayment="20"),
        ]
    )

    metrics = {item.key: item for item in result.metrics}
    assert metrics["minimum_cash_balance"].value == Decimal("-30.0000")
    assert metrics["peak_liquidity_gap"].value == Decimal("30.0000")
    assert metrics["minimum_sources_coverage"].value == Decimal("0.5833")
    assert metrics["credit_reliance"].value == Decimal("0.0833")
    assert metrics["cash_runway_periods"].value == Decimal(1)
    assert [item["rule_id"] for item in result.concerns] == [
        "liquidity.negative_cash",
        "liquidity.sources_coverage",
    ]
    assert result.concerns[0]["severity"] == "high"
    coverage_concern = result.concerns[1]
    assert f"{SOURCES_COVERAGE_THRESHOLD}x coverage" in coverage_concern["rationale"]


@pytest.mark.parametrize("outflows", ["0", "-10"])
def test_non_positive_liquidity_uses_make_ratios_unavailable(outflows: str) -> None:
    result = calculate_metrics([_period(1, cash="100", inflows="0", outflows=outflows)])

    metrics = {item.key: item for item in result.metrics}
    for key in ("minimum_sources_coverage", "credit_reliance"):
        assert metrics[key].value is None
        assert metrics[key].availability == "unavailable"
        diagnostic = metrics[key].diagnostic
        assert diagnostic is not None
        assert f"period 1 uses {Decimal(outflows):.4f}" in diagnostic
        assert "ratio is undefined" in diagnostic
    assert result.concerns == []


def test_credit_reliance_tracks_every_contributing_forecast_period() -> None:
    result = calculate_metrics(
        [
            _period(1, cash="100", inflows="10", outflows="100", draw="50"),
            _period(2, cash="100", inflows="10", outflows="50", draw="25"),
        ]
    )

    concern = next(
        item for item in result.concerns if item["rule_id"] == "liquidity.credit_reliance"
    )
    assert [period.period_number for period in concern["periods"]] == [1, 2]


@pytest.mark.parametrize("outflows", ["0", "-10"])
def test_mixed_non_positive_uses_make_credit_reliance_unavailable(outflows: str) -> None:
    result = calculate_metrics(
        [
            _period(1, cash="100", inflows="10", outflows="100", draw="50"),
            _period(2, cash="100", inflows="10", outflows=outflows, draw="0"),
        ]
    )

    metric = next(item for item in result.metrics if item.key == "credit_reliance")
    assert metric.value is None
    assert metric.availability == "unavailable"
    assert metric.diagnostic is not None
    assert f"period 2 uses {Decimal(outflows):.4f}" in metric.diagnostic
    assert all(concern["rule_id"] != "liquidity.credit_reliance" for concern in result.concerns)


def test_peak_gap_metric_and_evidence_use_the_largest_shortfall_period() -> None:
    result = calculate_metrics(
        [
            _period(1, cash="-10", inflows="50", outflows="60"),
            _period(2, cash="-100", inflows="40", outflows="70"),
        ]
    )

    metrics = {item.key: item for item in result.metrics}
    peak_gap = metrics["peak_liquidity_gap"]
    assert peak_gap.value == Decimal("100.0000")
    assert peak_gap.period_number == 2
    assert result.concerns[0]["period"].period_number == 2
    assert [period.period_number for period in result.concerns[0]["periods"]] == [1, 2]
    assert "forecast period 2" in result.concerns[0]["rationale"]


def test_negative_cash_evidence_deduplicates_a_shared_period() -> None:
    result = calculate_metrics([_period(1, cash="-10", inflows="50", outflows="60")])

    assert [period.period_number for period in result.concerns[0]["periods"]] == [1]


def test_rejects_non_sequential_forecast_outputs() -> None:
    with pytest.raises(ValueError, match="sequential forecast output"):
        calculate_metrics([_period(2, cash="100", inflows="50", outflows="25")])
