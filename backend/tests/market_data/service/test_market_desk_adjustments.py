"""Track-1 research adjustments (Option B): digest, apply, draft-only write.

This module's other-suite cases live in ``test_market_desk_adjustments_integration.py``.
"""

from __future__ import annotations

from app.market_data.service.market_desk import determinations


def test_package_digest_stable_for_identical_package() -> None:
    a = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "1"}],
    )
    b = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "1"}],
    )
    c = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "2"}],
    )
    assert a == b
    assert a != c
