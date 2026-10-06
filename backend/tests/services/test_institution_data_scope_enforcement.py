"""Exercise shared gates with an actually matching narrowed binding and control."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.models import AuthorizationBinding, Bank, User
from app.services import authorization, scoped_authorization
from app.services.regulatory_reporting import family_access
from tests.api.helpers import ORG_1, USER_1

BANK_ID = "BK-SCOPE001"


@pytest.mark.parametrize("kind", [DataScope.BRANCH, DataScope.REGION])
def test_shared_and_family_gates_refuse_a_matching_narrowed_binding(
    db_session: Session, kind: DataScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    bank = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="Scoped fixture",
        short_name="Scope",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()

    def grant(data_scope: DataScope) -> TenantContext:
        authorization.create_role_binding(
            db_session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                BANK_ID,
                ModuleScope.CREDIT,
                SensitivityScope.ALL,
                data_scope,
                ("B1",) if data_scope is not DataScope.ALL else (),
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "scope-test"),
            reason="Scope enforcement",
        )
        user = db_session.get(User, USER_1)
        assert user is not None
        return TenantContext(
            organization_id=ORG_1,
            actor_user_id=USER_1,
            authorization_version=user.authorization_version,
        )

    ctx = grant(kind)
    kwargs = {
        "permission": Permission.VIEW,
        "module": Module.CREDIT,
        "sensitivity": Sensitivity.AGGREGATED,
        "surface": "scope_regression",
    }
    decision = scoped_authorization.evaluate_bank_permission(db_session, ctx, bank, **kwargs)
    assert decision is not None and not decision.allowed
    assert decision.reason == "institution_grain_requires_whole_institution"
    assert decision.matching_binding_ids  # the binding matched; the DATA scope denied
    for require in (
        scoped_authorization.require_bank_permission,
        scoped_authorization.require_bank_permission_prefetched,
    ):
        with pytest.raises(HTTPException) as denied:
            require(db_session, ctx, BANK_ID, **kwargs)
        assert denied.value.status_code == 403
    # The sole explicit opt-out is permission to apply the scope, never permission
    # to serve institution figures. The HTTP/BI suites prove its row filtering.
    allowed = scoped_authorization.evaluate_bank_permission(
        db_session, ctx, bank, require_whole_institution=False, **kwargs
    )
    assert allowed is not None and allowed.allowed
    gate = family_access.FamilyGate(Module.CREDIT, Sensitivity.AGGREGATED)
    monkeypatch.setitem(family_access.GATED, "scope_test", gate)
    assert not family_access.can_view(db_session, ctx, bank, "scope_test")
    monkeypatch.setitem(family_access.GATED, "credit", gate)
    assert "credit" in family_access.hidden_families(db_session, ctx, bank)
    ctx = grant(DataScope.ALL)
    assert family_access.can_view(db_session, ctx, bank, "scope_test")
    assert "credit" not in family_access.hidden_families(db_session, ctx, bank)
    assert "liquidity" in family_access.hidden_families(db_session, ctx, bank)
    for require in (
        scoped_authorization.require_bank_permission,
        scoped_authorization.require_bank_permission_prefetched,
    ):
        assert require(db_session, ctx, BANK_ID, **kwargs).id == BANK_ID
    user = db_session.get(User, USER_1)
    assert user is not None
    user.is_active = False
    db_session.flush()
    assert "credit" in family_access.hidden_families(db_session, ctx, bank)
