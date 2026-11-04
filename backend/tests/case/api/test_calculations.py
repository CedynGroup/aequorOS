"""Tests for test_calculations."""

from __future__ import annotations

import pytest

from app.case.service import calculations


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_assumption_is_rejected(value: str) -> None:
    with pytest.raises(calculations.CalculationInputError) as raised:
        calculations._decimal_assumption(value, "revenue_growth_rate")

    assert raised.value.code == "invalid_assumption"
    assert raised.value.details == {
        "assumption": "revenue_growth_rate",
        "corrective_action": "Enter a finite numeric value and review the assumption again.",
    }
