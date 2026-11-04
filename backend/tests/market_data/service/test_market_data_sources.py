"""Per-bank market-data source preference: service-level behaviour.

Covers ``resolve_source_systems`` mapping, preference CRUD + defaults, the
preference-aware arbitration filter and its graceful-fallback flag, the
side-by-side ``/planes`` resolution, the forward grid, org isolation, and the
regression guard that a default (``source_systems=None``) getter is
byte-identical to today.

This module's other-suite cases live in ``test_market_data_sources_integration.py``.
"""

from __future__ import annotations

from app.market_data.service import market_data_sources


def test_resolve_source_systems_mapping() -> None:
    present = {"AEQUOR_DESK", "BLOOMBERG", "REFINITIV", "MANUAL_UPLOAD", "API_PUSH"}
    assert market_data_sources.resolve_source_systems("aequor", present) == ("AEQUOR_DESK",)
    assert market_data_sources.resolve_source_systems("vendor", present) == (
        "BLOOMBERG",
        "REFINITIV",
    )
    # bank = present minus aequor/vendor, sorted.
    assert market_data_sources.resolve_source_systems("bank", present) == (
        "API_PUSH",
        "MANUAL_UPLOAD",
    )
    # aequor/vendor are fixed even when absent from the bank's data.
    assert market_data_sources.resolve_source_systems("vendor", set()) == (
        "BLOOMBERG",
        "REFINITIV",
    )
    assert market_data_sources.resolve_source_systems("bank", set()) == ()
