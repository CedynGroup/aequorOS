"""Factory contracts are exercised against real persistence and HTTP gates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.identity.models.authorization import AuthorizationBinding
from app.identity.models.bank import Bank
from app.identity.service import authorization
from app.models import Jurisdiction, RefreshToken
from tests.support.factories import role_scope as actors

GRANTOR = authorization.GrantorRef(GrantorType.SYSTEM, "role-scope-factory-test")


@pytest.fixture
def scope_banks(db_session: Session) -> tuple[Bank, Bank, Bank]:
    tenant = actors.organization(db_session, name="Synthetic authority tenant")
    other = actors.organization(db_session, name="Other synthetic tenant")
    jurisdiction = db_session.get(Jurisdiction, "GH")
    assert jurisdiction is not None
    first = actors.bank(
        db_session,
        organization_id=tenant.id,
        bank_id="BK-FCTRY001",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    sibling = actors.bank(
        db_session,
        organization_id=tenant.id,
        bank_id="BK-FCTRY002",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    foreign = actors.bank(
        db_session,
        organization_id=other.id,
        bank_id="BK-FCTRY003",
        jurisdiction=jurisdiction,
        institution_type="universal_bank",
    )
    return first, sibling, foreign


def test_new_principals_have_no_inherited_authority(
    db_client: TestClient, db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, _ = scope_banks
    human = actors.human(db_session, organization_id=first.organization_id, legacy_role="admin")
    machine = actors.machine(
        db_session, organization_id=first.organization_id, legacy_role="viewer"
    )
    assert human.role == "admin"
    assert human.auth_provider == "oidc"
    assert machine.auth_provider == "service"
    assert not list(
        db_session.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.principal_user_id.in_([human.id, machine.id])
            )
        )
    )
    session = actors.tenant_session(db_session, user=human)
    response = db_client.get("/api/v1/banks", headers=actors.bearer(session.access_token))
    assert response.status_code == 200, response.text
    assert response.json()["banks"] == []
    denied = db_client.get(f"/api/v1/banks/{first.id}", headers=actors.bearer(session.access_token))
    assert denied.status_code == 404, denied.text
    with pytest.raises(ValueError, match="integration-key"):
        actors.tenant_session(db_session, user=machine)
    assert not list(
        db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == machine.id))
    )


def test_grant_keeps_real_session_invalidation_and_bank_isolation(
    db_client: TestClient, db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, sibling, foreign = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="viewer")
    old = actors.tenant_session(db_session, user=user)
    row = actors.RoleBindingFactory(db_session, GRANTOR).create(
        principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
        bundle=RoleBundle.VIEWER,
        scope=actors.institution_scope(
            bank_id=first.id,
            module=ModuleScope.LIQUIDITY,
            sensitivity=SensitivityScope.CONFIDENTIAL,
            data_scope=DataScope.ALL,
        ),
        reason="Read only the first institution",
    )
    db_session.commit()
    assert user.authorization_version == 2
    assert row.institution_id == first.id
    assert row.role_bundle == "viewer"
    assert row.module_scope == "liq"
    assert row.sensitivity_scope == "confidential"
    assert row.granted_by_id == "role-scope-factory-test"
    stale = db_client.get("/api/v1/banks", headers=actors.bearer(old.access_token))
    assert stale.status_code == 401, stale.text
    refreshed = db_client.post("/api/v1/auth/refresh", json={"refresh_token": old.refresh_token})
    assert refreshed.status_code == 401, refreshed.text
    fresh = actors.tenant_session(db_session, user=user)
    for bank_id, expected in ((first.id, 200), (sibling.id, 404), (foreign.id, 404)):
        response = db_client.get(
            f"/api/v1/banks/{bank_id}", headers=actors.bearer(fresh.access_token)
        )
        assert response.status_code == expected, response.text
    rows = list(db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id)))
    assert len(rows) == 2
    assert sum(row.revoked_at is not None for row in rows) == 1
    assert {row.revoked_reason for row in rows if row.revoked_at is not None} == {
        "authorization_changed"
    }


@pytest.mark.parametrize(
    "probe",
    [
        (Module.LIQUIDITY, Sensitivity.CONFIDENTIAL, Permission.VIEW, True),
        (Module.LIQUIDITY, Sensitivity.RESTRICTED, Permission.VIEW, False),
        (Module.REGULATORY, Sensitivity.CONFIDENTIAL, Permission.VIEW, False),
        (Module.LIQUIDITY, Sensitivity.CONFIDENTIAL, Permission.EXPORT, False),
    ],
)
def test_complete_scope_has_no_implied_module_sensitivity_or_export(
    db_session: Session,
    scope_banks: tuple[Bank, Bank, Bank],
    probe: tuple[Module, Sensitivity, Permission, bool],
) -> None:
    module, sensitivity, permission, expected = probe
    first, _, _ = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="admin")
    principal = PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN)
    actors.RoleBindingFactory(db_session, GRANTOR).create(
        principal=principal,
        bundle=RoleBundle.VIEWER,
        scope=actors.institution_scope(
            bank_id=first.id,
            module=ModuleScope.LIQUIDITY,
            sensitivity=SensitivityScope.CONFIDENTIAL,
            data_scope=DataScope.ALL,
        ),
        reason="One explicit liquidity read sentence",
    )
    decision = authorization.evaluate_permission(
        db_session,
        principal,
        permission,
        ResourceLocator(
            first.organization_id, InstitutionScope.INSTITUTION, first.id, module, sensitivity
        ),
    )
    assert decision.allowed is expected


def test_partial_sentences_do_not_compose(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, _ = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="analyst")
    principal = PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN)
    for module, sensitivity in (
        (ModuleScope.LIQUIDITY, SensitivityScope.AGGREGATED),
        (ModuleScope.REGULATORY, SensitivityScope.CONFIDENTIAL),
    ):
        actors.RoleBindingFactory(db_session, GRANTOR).create(
            principal=principal,
            bundle=RoleBundle.ANALYST,
            scope=actors.institution_scope(
                bank_id=first.id,
                module=module,
                sensitivity=sensitivity,
                data_scope=DataScope.ALL,
            ),
            reason="Two independently incomplete sentences",
        )
    denied = authorization.evaluate_permission(
        db_session,
        principal,
        Permission.RUN,
        ResourceLocator(
            first.organization_id,
            InstitutionScope.INSTITUTION,
            first.id,
            Module.LIQUIDITY,
            Sensitivity.CONFIDENTIAL,
        ),
    )
    assert not denied.allowed
    assert denied.matching_binding_ids == ()


def test_cross_tenant_scope_is_rejected_without_creating_a_binding(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, foreign = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="viewer")
    with pytest.raises(authorization.AuthorizationInvariantError, match="institution does not"):
        actors.RoleBindingFactory(db_session, GRANTOR).create(
            principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
            bundle=RoleBundle.VIEWER,
            scope=actors.institution_scope(
                bank_id=foreign.id,
                module=ModuleScope.LIQUIDITY,
                sensitivity=SensitivityScope.CONFIDENTIAL,
                data_scope=DataScope.ALL,
            ),
            reason="Cross-tenant refusal control",
        )
    assert user.authorization_version == 1
    assert not list(
        db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == user.id)
        )
    )


@pytest.mark.parametrize("data_scope", [DataScope.BRANCH, DataScope.REGION])
def test_credit_narrowing_survives_the_factory(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank], data_scope: DataScope
) -> None:
    first, _, _ = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="viewer")
    row = actors.RoleBindingFactory(db_session, GRANTOR).create(
        principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
        bundle=RoleBundle.VIEWER,
        scope=actors.institution_scope(
            bank_id=first.id,
            module=ModuleScope.CREDIT,
            sensitivity=SensitivityScope.RESTRICTED,
            data_scope=data_scope,
            data_scope_values=("synthetic-slice",),
        ),
        reason="Explicit Credit-only data slice",
    )
    assert row.data_scope_kind == data_scope.value
    assert row.data_scope_values == ["synthetic-slice"]
    effective = authorization.effective_data_scope(
        db_session, organization_id=first.organization_id, binding_ids=(row.id,)
    )
    assert not effective.whole_institution


def test_machine_factory_retains_the_real_principal_type_and_bundle_checks(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, _ = scope_banks
    user = actors.machine(db_session, organization_id=first.organization_id, legacy_role="viewer")
    factory = actors.RoleBindingFactory(db_session, GRANTOR)
    scope = actors.institution_scope(
        bank_id=first.id,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.RESTRICTED,
        data_scope=DataScope.ALL,
    )
    with pytest.raises(authorization.AuthorizationInvariantError, match="principal type"):
        factory.create(
            principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
            bundle=RoleBundle.VIEWER,
            scope=scope,
            reason="A service identity cannot masquerade as a human",
        )
    principal = PrincipalLocator(first.organization_id, user.id, PrincipalType.MACHINE)
    factory.create(
        principal=principal,
        bundle=RoleBundle.INTEGRATION_WRITER,
        scope=scope,
        reason="One explicit machine ingest binding",
    )
    resource = ResourceLocator(
        first.organization_id,
        InstitutionScope.INSTITUTION,
        first.id,
        Module.DATA,
        Sensitivity.RESTRICTED,
    )
    assert authorization.evaluate_permission(
        db_session, principal, Permission.INGEST, resource
    ).allowed
    assert not authorization.evaluate_permission(
        db_session, principal, Permission.VIEW, resource
    ).allowed


def test_factory_grant_and_invalidation_remain_in_the_callers_transaction(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, _ = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="viewer")
    db_session.commit()
    transaction = db_session.begin_nested()
    actors.RoleBindingFactory(db_session, GRANTOR).create(
        principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
        bundle=RoleBundle.VIEWER,
        scope=actors.organization_scope(
            module=ModuleScope.LIQUIDITY, sensitivity=SensitivityScope.CONFIDENTIAL
        ),
        reason="Rollback must undo both the binding and its invalidation",
    )
    assert user.authorization_version == 2
    transaction.rollback()
    db_session.refresh(user)
    assert user.authorization_version == 1
    assert not list(
        db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == user.id)
        )
    )


def test_factory_never_converts_unsupported_narrowing_to_whole_institution(
    db_session: Session, scope_banks: tuple[Bank, Bank, Bank]
) -> None:
    first, _, _ = scope_banks
    user = actors.human(db_session, organization_id=first.organization_id, legacy_role="viewer")
    with pytest.raises(authorization.AuthorizationInvariantError):
        actors.RoleBindingFactory(db_session, GRANTOR).create(
            principal=PrincipalLocator(first.organization_id, user.id, PrincipalType.HUMAN),
            bundle=RoleBundle.VIEWER,
            scope=actors.institution_scope(
                bank_id=first.id,
                module=ModuleScope.LIQUIDITY,
                sensitivity=SensitivityScope.CONFIDENTIAL,
                data_scope=DataScope.BRANCH,
                data_scope_values=("synthetic-branch",),
            ),
            reason="Unsupported Liquidity narrowing must remain refused",
        )
    assert user.authorization_version == 1
    assert not list(
        db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == user.id)
        )
    )
