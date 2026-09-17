"""Ownership is two explicit sentences: administer the account, read the product."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.models import AuthorizationBinding, Organization, User
from app.services import authorization, organization_ownership


def _new_tenant(db: Session, *, organization_id: str) -> User:
    db.add(Organization(id=organization_id, name=f"Ownership proof {organization_id}"))
    admin = User(
        id=uuid4(),
        organization_id=organization_id,
        email=f"owner@{organization_id.lower()}.example",
        display_name="Sole Administrator",
        role="account_admin",
        auth_provider="password",
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _bindings(db: Session, organization_id: str) -> list[AuthorizationBinding]:
    return list(
        db.scalars(
            select(AuthorizationBinding)
            .where(AuthorizationBinding.organization_id == organization_id)
            .order_by(AuthorizationBinding.granted_at, AuthorizationBinding.role_bundle)
        )
    )


def test_initial_owner_receives_account_and_read_sentences(db_session: Session) -> None:
    organization_id = "OR-OWNRTST1"
    admin = _new_tenant(db_session, organization_id=organization_id)
    version_before = admin.authorization_version

    state = organization_ownership.assign_initial_owner(
        db_session,
        organization_id=organization_id,
        candidate=admin,
        granted_by_id="test:ownership",
    )

    rows = {row.role_bundle: row for row in _bindings(db_session, organization_id)}
    assert set(rows) == {"org_owner", "viewer"}
    owner = rows["org_owner"]
    assert owner.id == state.owner_binding_id
    assert (owner.institution_scope, owner.module_scope, owner.sensitivity_scope) == (
        "organization",
        "account",
        "all",
    )
    read = rows["viewer"]
    assert read.principal_user_id == admin.id
    assert read.principal_type == "human"
    assert (read.institution_scope, read.institution_id) == ("organization", None)
    assert (read.module_scope, read.sensitivity_scope) == ("all", "all")
    assert (read.granted_by_type, read.granted_by_id) == ("system", "test:ownership")
    assert read.grant_reason == organization_ownership.OWNER_READ_REASON
    # Each sentence is a binding creation, so each advances the version once.
    db_session.refresh(admin)
    assert admin.authorization_version == version_before + 2
    assert organization_ownership.owner_read_access_exists(
        db_session, organization_id=organization_id, user_id=admin.id
    )


def test_read_sentence_is_not_duplicated_beside_equivalent_authority(
    db_session: Session,
) -> None:
    organization_id = "OR-OWNRTST2"
    admin = _new_tenant(db_session, organization_id=organization_id)
    # An organization-wide all/all Analyst row already carries `view` everywhere.
    authorization.create_role_binding(
        db_session,
        organization_id=organization_id,
        principal_user_id=admin.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION, None, ModuleScope.ALL, SensitivityScope.ALL
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test:preexisting"),
        reason="pre-existing operational authority",
    )

    organization_ownership.assign_initial_owner(
        db_session,
        organization_id=organization_id,
        candidate=admin,
        granted_by_id="test:ownership",
    )

    assert [row.role_bundle for row in _bindings(db_session, organization_id)] == [
        "analyst",
        "org_owner",
    ]
    assert (
        organization_ownership.ensure_owner_read_access(
            db_session,
            organization_id=organization_id,
            owner=admin,
            granted_by_id="test:again",
        )
        is None
    )


@pytest.mark.parametrize(
    ("module_scope", "sensitivity_scope", "institution_scope"),
    [
        (ModuleScope.LIQUIDITY, SensitivityScope.ALL, InstitutionScope.ORGANIZATION),
        (ModuleScope.ALL, SensitivityScope.AGGREGATED, InstitutionScope.ORGANIZATION),
    ],
)
def test_narrower_read_rows_do_not_count_as_the_owner_read_sentence(
    db_session: Session,
    module_scope: ModuleScope,
    sensitivity_scope: SensitivityScope,
    institution_scope: InstitutionScope,
) -> None:
    organization_id = "OR-OWNRTST3"
    admin = _new_tenant(db_session, organization_id=organization_id)
    authorization.create_role_binding(
        db_session,
        organization_id=organization_id,
        principal_user_id=admin.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(institution_scope, None, module_scope, sensitivity_scope),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test:narrow"),
        reason="a narrower read row",
    )
    assert not organization_ownership.owner_read_access_exists(
        db_session, organization_id=organization_id, user_id=admin.id
    )
    created = organization_ownership.ensure_owner_read_access(
        db_session,
        organization_id=organization_id,
        owner=admin,
        granted_by_id="test:ownership",
    )
    assert created is not None
    assert (created.module_scope, created.sensitivity_scope) == ("all", "all")
