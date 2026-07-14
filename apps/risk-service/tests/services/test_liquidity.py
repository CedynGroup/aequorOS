from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import CalculationForecastPeriod
from app.services.liquidity import calculate_metrics


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


def test_zero_liquidity_uses_do_not_create_false_credit_reliance() -> None:
    result = calculate_metrics([_period(1, cash="100", inflows="0", outflows="0")])

    metrics = {item.key: item for item in result.metrics}
    assert metrics["minimum_sources_coverage"].value == Decimal("999.0000")
    assert metrics["credit_reliance"].value == Decimal("0.0000")
    assert result.concerns == []


def test_rejects_non_sequential_forecast_outputs() -> None:
    with pytest.raises(ValueError, match="sequential forecast output"):
        calculate_metrics([_period(2, cash="100", inflows="50", outflows="25")])
