"""Live READ surfaces gate through the one ``live_access`` map.

The live summary, the alerts feed and window analytics must all serve exactly
the modules their caller holds an exact aggregated VIEW binding for. These
hermetic pins drive every surface through ``live_access.allowed_modules`` —
the single engine-to-authorization map — with a fully authorized caller and
hold each one to the identical closed vocabulary under its own surface name,
so no surface can drift onto a private or divergent gate list. The snapshot
service uses a separate lookup. Its API-level behavior — bound module served,
unbound module denied — and alert and window omissions are exercised by
``tests/live/api/test_live_read_scope_integration.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module
from app.identity.public import Bank
from app.liquidity.service import window_analytics
from app.live.models.live import LIVE_MODULES
from app.live.service import alerts, live_access, live_view
from tests.support.helpers import ORG_1, USER_1

_GRANTED = SimpleNamespace(allowed=True)

#: Each read surface's entry point, keyed by the ``surface`` label it must
#: present to the evaluator. ``live_summary`` and ``bank_alerts`` answer with
#: rows; ``window_analytics`` answers with series and daily aggregates.
_SURFACES: dict[str, Callable[..., object]] = {
    "live_summary": live_view.get_live_summary,
    "bank_alerts": alerts.get_bank_alerts,
    "window_analytics": partial(
        window_analytics.compute_window,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 3, 31),
    ),
}

_SURFACE_MODULES: dict[str, ModuleType] = {
    "live_summary": live_view,
    "bank_alerts": alerts,
    "window_analytics": window_analytics,
}


def _bank() -> Bank:
    return Bank(
        id="BK-LIVE-READ-SCOPE",
        organization_id=ORG_1,
        name="Live Read Scope Test Bank",
        short_name="LRST",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )


def _context() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _rows_free_session(captured: list[object]) -> Session:
    """A real (unbound) session whose read methods yield no rows, recording queries."""
    session = Session()

    def _scalars(statement: object, *_args: object, **_kwargs: object) -> object:
        captured.append(statement)
        return iter(())

    def _scalar(statement: object, *_args: object, **_kwargs: object) -> None:
        captured.append(statement)

    session.scalars = _scalars  # pyright: ignore[reportAttributeAccessIssue] - read-stub boundary
    session.scalar = _scalar  # pyright: ignore[reportAttributeAccessIssue] - read-stub boundary
    return session


def test_every_read_surface_gates_the_same_closed_engine_map() -> None:
    """A fully authorized caller is offered the identical closed map everywhere."""
    for surface, entry in _SURFACES.items():
        captured: list[object] = []
        db = _rows_free_session(captured)
        with (
            patch.object(_SURFACE_MODULES[surface], "_get_bank_or_404", return_value=_bank()),
            patch.object(
                live_access, "allowed_modules", wraps=live_access.allowed_modules
            ) as allowed,
            patch.object(
                live_access.scoped_authorization,
                "evaluate_bank_permission",
                return_value=_GRANTED,
            ),
        ):
            entry(db, _context(), _bank().id)
        assert allowed.call_args.kwargs["surface"] == surface, surface
        assert list(allowed.call_args.args[3]) == list(live_access.FRESHNESS_VIEW_MODULES), surface
        rendered = " ".join(str(statement) for statement in captured)
        assert "module IN" in rendered, surface
        assert "module !=" not in rendered, surface


def test_the_gate_covers_the_whole_live_module_vocabulary() -> None:
    """Every live-plane module name is gated; none is unmapped.

    The closed map names an authorization module for every member of
    ``LIVE_MODULES``, so a surface that asks for the full map cannot serve an
    ungated module.
    """
    assert set(live_access.FRESHNESS_VIEW_MODULES) == set(LIVE_MODULES)
    assert all(live_access.module_for(name) is not None for name in LIVE_MODULES)


def test_named_authority_mappings_reused_not_reinvented() -> None:
    """Rating reads ride MARKETS authority and capital reads CAPITAL authority.

    The shared map assigns implied-rating figures to MARKETS and capital
    figures to CAPITAL, matching the freshness mapping every surface reuses.
    """
    assert live_access.module_for("rating") is Module.MARKETS
    assert live_access.module_for("capital") is Module.CAPITAL
