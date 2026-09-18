"""Baseline membership opens self-service, never institution or module data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, AuthorizationBinding, Bank, User
from app.services import authentication, grant_administration, membership
from tests.api.helpers import ORG_1, USER_1, headers

_BANK_ID = "BK-MEMB0001"


def _baseline_only(db: Session) -> int:
    db.execute(
        delete(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == ORG_1,
            AuthorizationBinding.principal_user_id == USER_1,
        )
    )
    user = db.scalar(select(User).where(User.id == USER_1, User.organization_id == ORG_1))
    assert user is not None
    db.add(
        Bank(
            id=_BANK_ID,
            organization_id=ORG_1,
            name="Baseline Membership Test Bank",
            short_name="Membership",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
    )
    binding = membership.ensure_baseline_membership(
        db,
        user=user,
        granted_by_id="test:activation",
        commit=False,
    )
    db.commit()
    assert binding.role_bundle == "member"
    return user.authorization_version


def test_baseline_member_can_use_only_own_self_service(
    db_client: TestClient,
    db_session: Session,
) -> None:
    version = _baseline_only(db_session)
    auth = headers(roles=("viewer",), authorization_version=version)

    me = db_client.get("/api/v1/auth/me", headers=auth)
    assert me.status_code == 200, me.text
    assert me.json()["effective_authority"] == {
        "authv": version,
        "organization_capabilities": [],
        "institution_capabilities": [],
    }

    authority = db_client.get("/api/v1/auth/effective-authority", headers=auth)
    assert authority.status_code == 200, authority.text
    assert authority.json() == me.json()["effective_authority"]

    profile = db_client.patch(
        "/api/v1/auth/me",
        headers=auth,
        json={"theme": "system"},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["theme"] == "system"

    # The collection preserves its existing filtered shape; object and module
    # routes preserve their hidden/denied shapes.
    banks = db_client.get("/api/v1/banks", headers=auth)
    assert banks.status_code == 200, banks.text
    assert banks.json()["banks"] == []
    for path in (
        f"/api/v1/banks/{_BANK_ID}",
        f"/api/v1/banks/{_BANK_ID}/reporting-periods",
        f"/api/v1/banks/{_BANK_ID}/liquidity/dashboard",
        f"/api/v1/banks/{_BANK_ID}/capital/dashboard",
    ):
        assert db_client.get(path, headers=auth).status_code in {403, 404}

    assert db_client.get("/api/v1/organization/users", headers=auth).status_code == 403

    rows = list(
        db_session.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == ORG_1,
                AuthorizationBinding.principal_user_id == USER_1,
            )
        )
    )
    assert [(row.role_bundle, row.module_scope, row.sensitivity_scope) for row in rows] == [
        ("member", "account", "restricted")
    ]
    audit = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == str(rows[0].id),
            AuditEvent.event_type == "authorization.binding_granted",
        )
    )
    assert audit is not None
    assert audit.details["role_bundle"] == "member"


def test_baseline_is_system_managed_and_ends_on_deactivation(
    db_session: Session,
) -> None:
    version = _baseline_only(db_session)
    user = db_session.scalar(select(User).where(User.id == USER_1, User.organization_id == ORG_1))
    binding = db_session.scalar(
        select(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == ORG_1,
            AuthorizationBinding.principal_user_id == USER_1,
            AuthorizationBinding.role_bundle == "member",
        )
    )
    assert user is not None and binding is not None

    with pytest.raises(
        grant_administration.GrantAdministrationError,
        match="ends only when the member is deactivated",
    ):
        grant_administration.revoke_scoped_grant(
            db_session,
            organization_id=ORG_1,
            binding_id=binding.id,
            actor_user_id=USER_1,
            reason="member settings attempt",
        )

    authentication.deactivate_user(db_session, user)
    db_session.refresh(user)
    db_session.refresh(binding)
    assert user.is_active is False
    assert user.authorization_version == version + 1
    assert binding.status == "revoked"
    assert binding.revoked_by_id == "user_deactivation"
    assert binding.revoked_reason == membership.MEMBERSHIP_REVOKE_REASON
