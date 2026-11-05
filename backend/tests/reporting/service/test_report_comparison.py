"""Report-comparison engine: deltas, favorability, both modes, and isolation.

The assertions pin the substantive behaviour a governance user relies on — a
stronger capital ratio reads *favorable*, a fatter risk figure reads *adverse*, a
raw balance stays *neutral* — plus the resolution rules (version vs period) and
the tenant boundary.

This module's other-suite cases live in ``test_report_comparison_integration.py``.
"""

from __future__ import annotations

from app.reporting.service import report_comparison
from app.reporting.service.report_comparison import favorable_direction


def test_favorable_direction_registry() -> None:
    assert favorable_direction("car_pct") == "higher_better"
    assert favorable_direction("lcr_pct") == "higher_better"
    assert favorable_direction("cet1_ratio_pct") == "higher_better"
    assert favorable_direction("npl_ratio_pct") == "lower_better"
    assert favorable_direction("total_rwa_ghs") == "lower_better"
    assert favorable_direction("nop_pct_tier1") == "lower_better"
    # Raw balances / unknown keys carry no favorable direction.
    assert favorable_direction("total_assets_ghs") == "neutral"
    assert favorable_direction("deposits_ghs") == "neutral"
    assert favorable_direction("something_unmapped") == "neutral"
    # Family fallback: an unlisted RWA amount is still risk.
    assert favorable_direction("sovereign_rwa_ghs") == "lower_better"
    # Signed ΔEVE is judged on magnitude, never on the raw direction.
    assert favorable_direction("worst_eve_change_pct_tier1") == "magnitude_lower_better"
    assert favorable_direction("worst_eve_change_ghs") == "magnitude_lower_better"
    assert favorable_direction("delta_eve_pct_tier1") == "magnitude_lower_better"
    assert "worst_eve_change_pct_tier1" not in report_comparison.LOWER_BETTER
    # Earnings-at-risk is signed ΔNII judged on |value| by the limit test, so
    # the four EaR keys carry magnitude semantics and the old ``ear_`` prefix
    # fallback (which read a gain as adverse) is gone.
    for key in ("ear_up_200_ghs", "ear_down_200_ghs", "ear_up_450_ghs", "ear_down_450_ghs"):
        assert favorable_direction(key) == "magnitude_lower_better"
        assert key not in report_comparison.LOWER_BETTER
    assert favorable_direction("ear_unlisted_ghs") == "neutral"
