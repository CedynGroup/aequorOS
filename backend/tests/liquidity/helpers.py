from __future__ import annotations

from decimal import Decimal

from app.domain.liquidity.engine import LiquidityFact, LiquidityParams


def inputs() -> tuple[tuple[LiquidityFact, ...], LiquidityParams]:
    facts = (
        LiquidityFact("securities", "bond", Decimal("100"), hqla_level="L1"),
        LiquidityFact("balance_sheet", "deposit", Decimal("100"), side="liability"),
        LiquidityFact("balance_sheet", "asset", Decimal("100"), side="asset"),
    )
    params = LiquidityParams(
        outflow_rates={"deposit": Decimal("10")},
        inflow_rates={},
        asf_weights={"deposit": Decimal("95")},
        rsf_weights={"asset": Decimal("50")},
        inflow_cap_pct=Decimal("75"),
        lcr_min_pct=Decimal("100"),
        lcr_amber_floor_pct=Decimal("90"),
        nsfr_min_pct=Decimal("100"),
        nsfr_amber_floor_pct=Decimal("90"),
        hqla_haircut_pct={"L1": Decimal("0")},
        hqla_level2_cap_pct=None,
        hqla_level2b_cap_pct=None,
    )
    return facts, params
