"""Desk dataset entitlement tiers (spec §10).

This module's other-suite cases live in
``test_market_desk_entitlements_integration.py``.
"""

from __future__ import annotations

from app.market_data.service.market_desk import entitlements


def test_org_tier_resolves_the_highest_fully_held_tier() -> None:
    assert entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["core"])) == "core"
    assert entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["standard"])) == "standard"
    assert entitlements.org_tier(set(entitlements.ENTITLEMENT_TIERS["premium"])) == "premium"
