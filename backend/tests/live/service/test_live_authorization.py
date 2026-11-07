"""The freshness VIEW authority mapping: exact bindings, default deny.

Hermetic pins for the access helper's own contract — the mapping covers every
freshness module name (so a future ``_MODULES`` addition fails visibly until
it is mapped), unknown names deny without touching the evaluator, and a
deny-closed evaluator result (``None`` or ``not allowed``) is a denial. The
service-level filtering behavior is exercised end to end by
``tests/live/api/test_live_engine_authorization_integration.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module
from app.identity.public import Bank
from app.live.service import freshness, live_access
from tests.support.helpers import ORG_1, USER_1


def _bank() -> Bank:
    return Bank(
        organization_id=ORG_1,
        name="Freshness Authz Test Bank",
        short_name="FATB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )


def _context() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def test_mapping_covers_every_freshness_module() -> None:
    assert set(freshness._MODULES) == set(live_access.FRESHNESS_VIEW_MODULES)
    assert live_access.module_for("rating") is Module.MARKETS


def test_unknown_module_denies_without_evaluator_call() -> None:
    with patch.object(live_access.scoped_authorization, "evaluate_bank_permission") as evaluate:
        assert (
            live_access.can_view_module(
                Session(), TenantContext(organization_id=ORG_1), _bank(), "mystery", surface="t"
            )
            is False
        )
        evaluate.assert_not_called()


def test_deny_closed_on_missing_or_refused_decision() -> None:
    db, context, bank = Session(), _context(), _bank()
    with patch.object(
        live_access.scoped_authorization, "evaluate_bank_permission", return_value=None
    ) as evaluate:
        assert live_access.can_view_module(db, context, bank, "fx", surface="t") is False
        evaluate.assert_called_once()
    refused = SimpleNamespace(allowed=False)
    with patch.object(
        live_access.scoped_authorization, "evaluate_bank_permission", return_value=refused
    ):
        assert live_access.can_view_module(db, context, bank, "fx", surface="t") is False


def test_allowed_modules_preserves_caller_order_and_filters() -> None:
    decision = SimpleNamespace(allowed=True)
    with patch.object(
        live_access.scoped_authorization, "evaluate_bank_permission", return_value=decision
    ):
        allowed = live_access.allowed_modules(
            Session(), _context(), _bank(), ("fx", "mystery", "irr"), surface="t"
        )
    assert allowed == ["fx", "irr"]
