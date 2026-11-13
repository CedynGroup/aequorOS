"""One closed map for every live read: ``live_access`` owns module authority.

Issue #177 part A. The live summary, alerts and window analytics must obtain
their permitted-module set from ``live_access`` (``FRESHNESS_VIEW_MODULES``)
and filter with a positive include (``module IN allowed``), never a per-engine
exclusion tuple; a caller with no permitted modules gets ``is_stale=False``
without the bank-wide fact/batch probe; and the snapshot ladder's single
service gate must deny unknown module names. These hermetic pins hold the
wiring to that contract without a database; the end-to-end behavior is pinned
by ``tests/live/api/test_live_engine_authorization_integration.py``.
"""

from __future__ import annotations

import inspect
from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module
from app.identity.public import Bank
from app.liquidity.service import window_analytics
from app.live.api import manage_live_engine
from app.live.models.live import LIVE_MODULES
from app.live.service import alerts, live_access, live_view
from tests.support.helpers import ORG_1, USER_1

if TYPE_CHECKING:
    from collections.abc import Iterator


def _bank() -> Bank:
    return Bank(
        id="BK-LIVE-READ-MAP",
        organization_id=ORG_1,
        name="Live Read Map Test Bank",
        short_name="LRMT",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )


def _context() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _rows_free_session(captured: list[object]) -> Session:
    """A real (unbound) session whose read methods yield no rows, recording queries.

    The class-level patches never touch a database: ``scalars`` yields nothing
    and ``scalar`` returns ``None``, while every statement is kept for the
    include-list pins.
    """
    session = Session()

    def _scalars(statement: object, *_args: object, **_kwargs: object) -> Iterator[object]:
        captured.append(statement)
        return iter(())

    def _scalar(statement: object, *_args: object, **_kwargs: object) -> None:
        captured.append(statement)

    session.scalars = _scalars  # pyright: ignore[reportAttributeAccessIssue] - read-stub boundary
    session.scalar = _scalar  # pyright: ignore[reportAttributeAccessIssue] - read-stub boundary
    return session


def test_no_surface_keeps_a_private_engine_gate_map() -> None:
    """The three duplicate ``_GATED_ENGINE_MODULES`` copies are gone."""
    for surface in (live_view, alerts, window_analytics):
        assert not hasattr(surface, "_GATED_ENGINE_MODULES"), surface.__name__


def test_the_shared_map_covers_the_live_module_vocabulary() -> None:
    """Every live-plane module name is mapped to an authorization module."""
    assert set(live_access.FRESHNESS_VIEW_MODULES) == set(LIVE_MODULES)
    assert live_access.module_for("rating") is Module.MARKETS
    assert live_access.module_for("capital") is Module.CAPITAL
    assert all(isinstance(module, Module) for module in live_access.FRESHNESS_VIEW_MODULES.values())


def test_summary_filters_with_the_positive_include_and_skips_the_stale_probe() -> None:
    """A zero-module caller gets ``is_stale=False`` and no bank-wide probe."""
    captured: list[object] = []
    db = _rows_free_session(captured)
    bank = _bank()
    with (
        patch.object(live_view, "get_bank_or_404", return_value=bank),
        patch.object(live_view, "_latest_period", return_value=None),
        patch.object(live_access, "allowed_modules", return_value=[]) as allowed,
        patch.object(live_view, "_cache_is_behind_the_book") as probe,
    ):
        summary = live_view.get_live_summary(db, _context(), bank.id)
    assert summary.is_stale is False
    assert summary.modules == []
    assert allowed.call_args.kwargs["surface"] == "live_summary"
    probe.assert_not_called()
    rendered = " ".join(str(statement) for statement in captured)
    assert "module IN" in rendered
    assert "module !=" not in rendered


def test_alerts_filter_with_the_positive_include_list() -> None:
    """The alerts feed asks live_access and filters ``module IN allowed``."""
    captured: list[object] = []
    db = _rows_free_session(captured)
    bank = _bank()
    with (
        patch.object(alerts, "get_bank_or_404", return_value=bank),
        patch.object(live_access, "allowed_modules", return_value=["liquidity"]) as allowed,
    ):
        feed = alerts.get_bank_alerts(db, _context(), bank.id)
    assert feed.total == 0
    assert allowed.call_args.kwargs["surface"] == "bank_alerts"
    rendered = str(captured[0])
    assert "module IN" in rendered
    assert "module !=" not in rendered


def test_window_analytics_filter_with_the_positive_include_list() -> None:
    """Window ratios and daily aggregates filter ``module IN allowed``."""
    captured: list[object] = []
    db = _rows_free_session(captured)
    bank = _bank()
    with (
        patch.object(window_analytics, "get_bank_or_404", return_value=bank),
        patch.object(live_access, "allowed_modules", return_value=["liquidity"]) as allowed,
    ):
        window = window_analytics.compute_window(
            db,
            _context(),
            bank.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
        )
    assert window.ratios == []
    assert window.daily == []
    assert allowed.call_args.kwargs["surface"] == "window_analytics"
    rendered = " ".join(str(statement) for statement in captured)
    assert "module IN" in rendered
    assert "module !=" not in rendered


def test_unknown_snapshot_module_denies_without_the_evaluator() -> None:
    """An unknown module name is never served ungated; the evaluator is untouched."""
    db = _rows_free_session([])
    bank = _bank()
    with (
        patch.object(live_view, "get_bank_or_404", return_value=bank),
        patch.object(live_view.scoped_authorization, "evaluate_bank_permission") as evaluate,
        pytest.raises(HTTPException) as refused,
    ):
        live_view.list_live_snapshots(db, _context(), bank.id, module="mystery")
    assert refused.value.status_code == 403
    evaluate.assert_not_called()


def test_snapshot_gate_is_service_side_only() -> None:
    """The route's liquidity/IRRBB branch is gone; the service uses the map."""
    assert "require_bank_permission" not in inspect.getsource(manage_live_engine)
    assert "module_for" in inspect.getsource(live_view.list_live_snapshots)
