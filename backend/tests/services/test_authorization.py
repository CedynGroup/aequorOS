"""Database and service invariants for scoped authorization bindings."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
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
from app.db.base import utc_now
from app.models import AuthorizationBinding, Bank, RefreshToken, User
from app.services import authentication, authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1

BANK_1 = "BK-AUTH0001"
BANK_2 = "BK-AUTH0002"


def _banks(db: Session) -> None:
    db.add_all(
        [
            Bank(
                id=BANK_1,
                organization_id=ORG_1,
                name="Authorization Bank One",
                short_name="Auth One",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            ),
            Bank(
                id=BANK_2,
                organization_id=ORG_2,
                name="Authorization Bank Two",
                short_name="Auth Two",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            ),
        ]
    )
    db.commit()


def _raw_binding(*, organization_id: str, institution_id: str) -> AuthorizationBinding:
    return AuthorizationBinding(
        organization_id=organization_id,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN.value,
        role_bundle=RoleBundle.VIEWER.value,
        institution_scope=InstitutionScope.INSTITUTION.value,
        institution_id=institution_id,
        module_scope=ModuleScope.LIQUIDITY.value,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL.value,
        granted_by_type=authorization.GrantorType.SYSTEM.value,
        granted_by_id="test-suite",
        grant_reason="prove tenant-consistent institution references",
        granted_at=utc_now(),
        status=BindingStatus.ACTIVE.value,
        valid_from=utc_now(),
    )


def test_existing_users_start_versioned_without_implicit_new_authority(
    db_session: Session,
) -> None:
    user = db_session.get(User, USER_1)

    assert user is not None
    assert user.authorization_version == 1
    assert not list(
        db_session.scalars(
            select(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == user.id)
        )
    )


def test_legacy_admin_role_is_not_new_policy_authority(db_session: Session) -> None:
    user = db_session.get(User, USER_1)
    assert user is not None
    user.role = "admin"
    db_session.commit()

    decision = authorization.evaluate_permission(
        db_session,
        PrincipalLocator(ORG_1, user.id, PrincipalType.HUMAN),
        Permission.ADMINISTER,
        ResourceLocator(ORG_1, None, Module.ACCOUNT, Sensitivity.RESTRICTED),
    )

    assert not decision.allowed
    assert decision.reason == "no_active_exact_binding"
    assert decision.matching_binding_ids == ()


def test_token_issuance_refreshes_authorization_version_after_owner_lock(
    db_session: Session,
) -> None:
    user = db_session.get(User, USER_1)
    assert user is not None
    db_session.execute(
        update(User)
        .where(User.id == user.id)
        .values(authorization_version=2)
        .execution_options(synchronize_session=False)
    )
    assert user.authorization_version == 1

    issued = authentication.issue_tokens(db_session, user)
    claims = jwt.decode(issued.access_token, options={"verify_signature": False})

    assert claims["authv"] == 2


def test_database_rejects_cross_tenant_institution_reference(db_session: Session) -> None:
    _banks(db_session)
    db_session.add(_raw_binding(organization_id=ORG_1, institution_id=BANK_2))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    ("principal_type", "role_bundle"),
    [
        (PrincipalType.HUMAN, RoleBundle.INTEGRATION_WRITER),
        (PrincipalType.MACHINE, RoleBundle.ANALYST),
    ],
)
def test_database_rejects_incompatible_principal_bundle_pairs(
    db_session: Session,
    principal_type: PrincipalType,
    role_bundle: RoleBundle,
) -> None:
    _banks(db_session)
    binding = _raw_binding(organization_id=ORG_1, institution_id=BANK_1)
    binding.principal_type = principal_type.value
    binding.role_bundle = role_bundle.value
    db_session.add(binding)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    ("status", "revoked_at", "revoked_reason"),
    [
        (BindingStatus.ACTIVE, None, "not revoked"),
        (BindingStatus.SUSPENDED, None, "not revoked"),
        (BindingStatus.REVOKED, utc_now(), None),
        (BindingStatus.REVOKED, utc_now(), "   "),
    ],
)
def test_database_rejects_inconsistent_revocation_evidence(
    db_session: Session,
    status: BindingStatus,
    revoked_at: datetime | None,
    revoked_reason: str | None,
) -> None:
    _banks(db_session)
    binding = _raw_binding(organization_id=ORG_1, institution_id=BANK_1)
    binding.status = status.value
    binding.revoked_at = revoked_at
    binding.revoked_reason = revoked_reason
    db_session.add(binding)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_service_rejects_cross_tenant_institution_before_insert(db_session: Session) -> None:
    _banks(db_session)
    user = db_session.get(User, USER_1)
    assert user is not None

    with pytest.raises(
        authorization.AuthorizationInvariantError,
        match="institution does not belong",
    ):
        authorization.create_role_binding(
            db_session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                BANK_2,
                ModuleScope.LIQUIDITY,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(
                authorization.GrantorType.SYSTEM,
                "test-suite",
            ),
            reason="cross-tenant probe",
        )

    assert not list(db_session.scalars(select(AuthorizationBinding)))
    assert user.authorization_version == 1


def test_binding_creation_bumps_version_and_revokes_refresh_families_atomically(
    db_session: Session,
) -> None:
    _banks(db_session)
    user = db_session.get(User, USER_1)
    assert user is not None
    issued = authentication.issue_tokens(db_session, user)
    old_claims = jwt.decode(issued.access_token, options={"verify_signature": False})
    assert old_claims["authv"] == 1

    binding = authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            BANK_1,
            ModuleScope.LIQUIDITY,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(
            authorization.GrantorType.SYSTEM,
            "test-suite",
        ),
        reason="assign the first shadow binding",
    )

    db_session.refresh(user)
    refresh_rows = list(
        db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id))
    )
    assert binding.principal_user_id == user.id
    assert user.authorization_version == 2
    assert refresh_rows
    assert all(row.revoked_at is not None for row in refresh_rows)
    assert {row.revoked_reason for row in refresh_rows} == {"authorization_changed"}


def test_service_denies_locator_whose_institution_is_not_in_resource_tenant(
    db_session: Session,
) -> None:
    _banks(db_session)
    user = db_session.get(User, USER_1)
    assert user is not None
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ALL,
            SensitivityScope.ALL,
        ),
        grantor=authorization.GrantorRef(
            authorization.GrantorType.SYSTEM,
            "test-suite",
        ),
        reason="organization-wide shadow visibility",
    )

    decision = authorization.evaluate_permission(
        db_session,
        PrincipalLocator(ORG_1, user.id, PrincipalType.HUMAN),
        Permission.VIEW,
        ResourceLocator(ORG_1, BANK_2, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
    )

    assert not decision.allowed
    assert decision.reason == "resource_institution_not_in_tenant"


def test_machine_principal_cannot_receive_a_human_bundle(db_session: Session) -> None:
    machine = User(
        id=uuid4(),
        organization_id=ORG_1,
        email="authorization-machine@service.aequoros.invalid",
        role="analyst",
        auth_provider="service",
    )
    db_session.add(machine)
    db_session.commit()

    with pytest.raises(
        authorization.AuthorizationInvariantError,
        match="machine permission bundle",
    ):
        authorization.create_role_binding(
            db_session,
            organization_id=ORG_1,
            principal_user_id=machine.id,
            principal_type=PrincipalType.MACHINE,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.DATA,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(
                authorization.GrantorType.SYSTEM,
                "test-suite",
            ),
            reason="invalid human preset for a machine",
        )
