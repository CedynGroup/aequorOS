"""Tests for test_behavioral_liquidity."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.liquidity.schemas.liquidity_cfp import CfpContent
from app.liquidity.service.behavioral_liquidity import (
    _segment_metrics,
)


def test_behavioral_liquidity_metrics_require_evidence() -> None:
    # Annotated to the parameter's element type: the rate leg is optional there,
    # and ``list`` is invariant, so an inferred ``float`` leg would not be accepted.
    points: list[tuple[date, float, int, float | None]] = [
        (date(2025, month, 28), 1_000.0 - month * 10, 100 - month, 8.0 + month * 0.1)
        for month in range(1, 13)
    ]
    short_rates = {observed: 20.0 + index * 0.2 for index, (observed, *_rest) in enumerate(points)}

    result = _segment_metrics("product", "SAVINGS", points, short_rates)

    assert result.data_status == "partial"
    assert result.observed_monthly_runoff_pct == pytest.approx(1.123596, abs=0.0001)
    assert result.latest_withdrawal_pct == pytest.approx(1.123596, abs=0.0001)
    assert result.position_attrition_pct == pytest.approx(1.123596, abs=0.0001)
    assert result.deposit_beta is not None
    assert result.repricing_lag_months in range(4)
    assert result.seasonal_deviation_pct is None
    assert any("same calendar month" in reason for reason in result.reasons)


def test_cfp_behavioral_scenarios_must_reference_actions() -> None:
    with pytest.raises(ValidationError, match="must link to an action"):
        CfpContent.model_validate(
            {
                "action_plans": [
                    {"side": "liability", "action": "Draw committed line", "owner": "Treasury"}
                ],
                "behavioral_liquidity_scenarios": [
                    {
                        "name": "Deposit acceleration",
                        "activation_horizon": "up_to_1m",
                        "linked_action": "Unrelated action",
                        "deposit_runoff_uplift_pct": "15",
                        "funding_cost_uplift_bps": "250",
                    }
                ],
            }
        )
