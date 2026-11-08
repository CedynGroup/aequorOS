"""The live READ scope: one closed module map governs every engine surface.

The official-snapshot ladder, the alerts feed and the window analytics must
all serve exactly the modules their caller holds an exact aggregated VIEW
binding for, through the closed engine-to-authorization-module mapping in
``live_access``. These hermetic pins hold every surface's gate list to that
map, so a new live module fails visibly until it is mapped and the surfaces
cannot drift apart. The end-to-end behavior — bound module served, unbound
module omitted, controls denied — is exercised by
``tests/live/api/test_live_read_scope_integration.py``.
"""

from __future__ import annotations

from app.core.authorization import Module
from app.liquidity.service import window_analytics
from app.live.models.live import LIVE_MODULES
from app.live.service import alerts, live_access, live_view


def test_every_read_surface_gates_the_same_closed_engine_map() -> None:
    """The snapshot, alerts and window gate lists are the freshness map."""
    for surface_map in (
        live_view._GATED_ENGINE_MODULES,
        alerts._GATED_ENGINE_MODULES,
        window_analytics._GATED_ENGINE_MODULES,
    ):
        assert dict(surface_map) == live_access.FRESHNESS_VIEW_MODULES


def test_the_gate_covers_the_whole_live_module_vocabulary() -> None:
    """Every live-plane module name is gated; none is unmapped.

    A future ``LIVE_MODULES`` addition fails here until the closed map and all
    three surface gate lists name its authorization module.
    """
    assert {engine for engine, _ in live_view._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert {engine for engine, _ in alerts._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert {engine for engine, _ in window_analytics._GATED_ENGINE_MODULES} == set(LIVE_MODULES)
    assert all(live_access.module_for(name) is not None for name in LIVE_MODULES)


def test_named_authority_mappings_reused_not_reinvented() -> None:
    """Rating reads ride MARKETS authority and capital reads CAPITAL authority.

    The implied-rating figures are the existing precedent; the capital entry
    keeps the official-snapshot ladder on the same authority as the summary.
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
