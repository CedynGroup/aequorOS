"""Tests for test_rule_applicability."""

from decimal import Decimal

import pytest

from app.policy.domain.regulatory_instruments import forecast_status


@pytest.mark.parametrize("liquidity_in_force", [False, True])
@pytest.mark.parametrize(
    ("car", "lcr", "nsfr", "capital_status"),
    [
        ("8", "110", "110", "red"),
        ("20", "80", "110", "green"),
        ("20", "110", "80", "green"),
        ("13.25", "110", "110", "amber"),
    ],
)
def test_legacy_constituents_retain_capital_and_assess_only_applicable_liquidity(
    liquidity_in_force: bool, car: str, lcr: str, nsfr: str, capital_status: str
) -> None:
    metrics = {"year5_car_pct": car, "year5_lcr_pct": lcr, "year5_nsfr_pct": nsfr}
    thresholds = {
        "car_min": Decimal("13"),
        "lcr_min": Decimal("100"),
        "nsfr_min": Decimal("100"),
        "lcr_amber_floor": Decimal("90"),
    }
    expected = "red" if liquidity_in_force and (lcr == "80" or nsfr == "80") else capital_status
    assert forecast_status(metrics, liquidity_in_force, thresholds) == expected
