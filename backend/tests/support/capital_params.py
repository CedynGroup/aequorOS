"""Shared Bank of Ghana capital parameters helper.

Extracted from the capital engine test module so suites elsewhere can use
the canonical CRD baseline parameters without importing a collected test
module.
"""

from __future__ import annotations

from decimal import Decimal

from app.capital.domain.engine import CapitalParams


def bog_capital_params() -> CapitalParams:
    return CapitalParams(
        risk_weights={
            "RW0": Decimal("0"),
            "RW20": Decimal("20"),
            "RW35": Decimal("35"),
            "RW50": Decimal("50"),
            "RW75": Decimal("75"),
            "RW100": Decimal("100"),
            "RW150": Decimal("150"),
        },
        bia_alpha_pct=Decimal("15"),
        fx_charge_pct=Decimal("8"),
        rwa_multiplier_pct=Decimal("1250"),
        tier2_gp_cap_pct_credit_rwa=Decimal("1.25"),
        cet1_min_pct=Decimal("6.5"),
        tier1_min_pct=Decimal("8"),
        car_min_pct=Decimal("10"),
        leverage_min_pct=Decimal("3"),
        car_early_warning_pct=Decimal("10.5"),
        car_critical_pct=Decimal("9"),
    )
