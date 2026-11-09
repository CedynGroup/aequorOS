"""Live READ gate lists match the freshness authorization map.

The official-snapshot ladder, the alerts feed and the window analytics must
all serve exactly the modules their caller holds an exact aggregated VIEW
binding for. These hermetic pins hold the live summary, alerts and window
gate lists to the engine-to-authorization-module mapping in ``live_access``.
The snapshot service uses a separate lookup. Its API-level behavior — bound
module served, unbound module denied — and alert and window omissions are
exercised by
``tests/live/api/test_live_read_scope_integration.py``.
"""

from __future__ import annotations

from app.core.authorization import Module
from app.liquidity.service import window_analytics
from app.live.models.live import LIVE_MODULES
from app.live.service import alerts, live_access, live_view


def test_every_read_surface_gates_the_same_closed_engine_map() -> None:
    """The summary, alerts and window gate lists match the freshness map."""
    for surface_map in (
        live_view._GATED_ENGINE_MODULES,
        alerts._GATED_ENGINE_MODULES,
        window_analytics._GATED_ENGINE_MODULES,
    ):
        assert dict(surface_map) == live_access.FRESHNESS_VIEW_MODULES


def test_the_gate_covers_the_whole_live_module_vocabulary() -> None:
    """Every live-plane module name is gated; none is unmapped.

    The closed map and all three surface gate lists name an authorization
    module for every member of ``LIVE_MODULES``.
    """
    assert {engine for engine, _ in live_view._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert {engine for engine, _ in alerts._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert {engine for engine, _ in window_analytics._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert all(live_access.module_for(name) is not None for name in LIVE_MODULES)


def test_named_authority_mappings_reused_not_reinvented() -> None:
    """Rating reads ride MARKETS authority and capital reads CAPITAL authority.

    The gate lists assign implied-rating figures to MARKETS and capital
    figures to CAPITAL, matching the freshness map.
    """
    assert live_access.module_for("rating") is Module.MARKETS
    assert live_access.module_for("capital") is Module.CAPITAL
    for surface_map in (
        live_view._GATED_ENGINE_MODULES,
        alerts._GATED_ENGINE_MODULES,
        window_analytics._GATED_ENGINE_MODULES,
    ):
        assert dict(surface_map)["rating"] is Module.MARKETS
        assert dict(surface_map)["capital"] is Module.CAPITAL
