"""Acceptance coverage for scoped grant administration and Members."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
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
from app.db.session import get_sessionmaker
from app.models import AuditEvent, AuthorizationBinding, Bank, RefreshToken, User
from app.services import authentication, authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, USER_2, headers

GRANTEE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BANK_A = "BK-GRNT0001"
BANK_B = "BK-GRNT0002"


def _session() -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    return session


def _owner_headers() -> dict[str, str]:
    return headers(roles=("account_admin",), authorization_version=2)


def _seed_admin_surface() -> None:
    with _session() as db:
        owner = db.get(User, USER_1)
        assert owner is not None
        owner.role = "account_admin"
        db.add(
            User(
                id=GRANTEE,
                organization_id=ORG_1,
                email="amma.owusu@example.test",
                display_name="Amma Owusu",
                role="viewer",
            )
        )
        db.add_all(
            [
                Bank(
                    id=BANK_A,
                    organization_id=ORG_1,
                    name="Aequor Bank Ghana",
                    short_name="Aequor Ghana",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                ),
                Bank(
                    id=BANK_B,
                    organization_id=ORG_1,
                    name="Aequor Rural Bank",
                    short_name="Aequor Rural",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="rural_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                ),
            ]
        )
        db.commit()
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=owner.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ORG_OWNER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
            reason="explicit owner authority for grant administration tests",
        )


@pytest.fixture
def grant_client(db_client: TestClient) -> TestClient:
    _seed_admin_surface()
    return db_client


def _payload(  # noqa: PLR0913 - each scalar is one indivisible scope dimension
    *,
    role: str = "analyst",
    institution_id: str = BANK_A,
    module: str = "liq",
    sensitivity: str = "confidential",
    principal_user_id: UUID = GRANTEE,
    reason: str = "Treasury responsibilities approved by the Head of Treasury",
) -> dict[str, object]:
    return {
        "principal_user_id": str(principal_user_id),
        "role_bundle": role,
        "institution_scope": "institution",
        "institution_id": institution_id,
        "module_scope": module,
        "sensitivity_scope": sensitivity,
        "reason": reason,
    }


def _resource(
    bank_id: str,
    module: Module,
    sensitivity: Sensitivity,
) -> ResourceLocator:
    return ResourceLocator(
        ORG_1,
        InstitutionScope.INSTITUTION,
        bank_id,
        module,
        sensitivity,
    )


def test_create_and_list_keep_every_scalar_dimension_exact(
    grant_client: TestClient,
) -> None:
    missing_sensitivity = _payload()
    missing_sensitivity.pop("sensitivity_scope")
    assert (
        grant_client.post(
            "/api/v1/authorization/bindings",
            headers=_owner_headers(),
            json=missing_sensitivity,
        ).status_code
        == 422
    )

    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["sod_decision"] == {"outcome": "allow", "findings": []}
    assert body["binding"]["sensitivity_scope"] == "confidential"
    assert body["binding"]["authority_sentence"] == (
        "Amma Owusu is an Analyst in Liquidity Monitoring for Aequor Bank Ghana, "
        "covering Confidential data."
    )

    listed = grant_client.get(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        params={"principal_user_id": str(GRANTEE)},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["bindings"]) == 1
    assert listed.json()["bindings"][0]["sensitivity_scope"] == "confidential"

    with _session() as db:
        principal = PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN)
        exact = authorization.evaluate_permission(
            db,
            principal,
            Permission.RUN,
            _resource(BANK_A, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        )
        other_module = authorization.evaluate_permission(
            db,
            principal,
            Permission.RUN,
            _resource(BANK_A, Module.REGULATORY, Sensitivity.CONFIDENTIAL),
        )
        other_institution = authorization.evaluate_permission(
            db,
            principal,
            Permission.RUN,
            _resource(BANK_B, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        )
        other_sensitivity = authorization.evaluate_permission(
            db,
            principal,
            Permission.RUN,
            _resource(BANK_A, Module.LIQUIDITY, Sensitivity.RESTRICTED),
        )
        assert exact.allowed
        assert not other_module.allowed
        assert not other_institution.allowed
        assert not other_sensitivity.allowed

        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_granted",
                AuditEvent.entity_id == body["binding"]["id"],
            )
        )
        assert audit is not None
        assert audit.actor_user_id == USER_1
        assert audit.details["grantee_user_id"] == str(GRANTEE)
        assert audit.details["role_bundle"] == "analyst"
        assert audit.details["scope"] == {
            "institution_scope": "institution",
            "institution_id": BANK_A,
            "module_scope": "liq",
            "sensitivity_scope": "confidential",
        }
        assert audit.details["reason"] == _payload()["reason"]


def test_one_request_cannot_fan_out_and_two_combinations_require_two_requests(
    grant_client: TestClient,
) -> None:
    for field, array_value in (
        ("role_bundle", ["analyst", "approver"]),
        ("institution_scope", ["institution", "organization"]),
        ("institution_id", [BANK_A, BANK_B]),
        ("module_scope", ["liq", "reg"]),
        ("sensitivity_scope", ["confidential", "restricted"]),
    ):
        crossed = _payload()
        crossed[field] = array_value
        assert (
            grant_client.post(
                "/api/v1/authorization/bindings",
                headers=_owner_headers(),
                json=crossed,
            ).status_code
            == 422
        ), field

    first = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(),
    )
    second = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(
            role="approver",
            institution_id=BANK_B,
            module="reg",
            sensitivity="restricted",
            reason="Independent regulatory checker assignment",
        ),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listed = grant_client.get(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        params={"principal_user_id": str(GRANTEE)},
    ).json()["bindings"]
    assert {
        (
            row["role_bundle"],
            row["institution_id"],
            row["module_scope"],
            row["sensitivity_scope"],
        )
        for row in listed
    } == {
        ("analyst", BANK_A, "liq", "confidential"),
        ("approver", BANK_B, "reg", "restricted"),
    }

    with _session() as db:
        principal = PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN)
        assert not authorization.evaluate_permission(
            db,
            principal,
            Permission.RUN,
            _resource(BANK_B, Module.REGULATORY, Sensitivity.RESTRICTED),
        ).allowed
        assert not authorization.evaluate_permission(
            db,
            principal,
            Permission.APPROVE,
            _resource(BANK_A, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        ).allowed


def test_members_are_tenant_scoped_and_no_binding_means_no_authority(
    grant_client: TestClient,
) -> None:
    response = grant_client.get("/api/v1/organization/members", headers=_owner_headers())
    assert response.status_code == 200, response.text
    members = response.json()["members"]
    ids = {member["user_id"] for member in members}
    assert str(GRANTEE) in ids
    assert str(USER_2) not in ids
    grantee = next(member for member in members if member["user_id"] == str(GRANTEE))
    assert grantee["active_grant_count"] == 0
    assert grantee["grants"] == []
    assert grantee["authentication_method"] == "password"
    assert grantee["access_request_state"] == "none"

    with _session() as db:
        decision = authorization.evaluate_permission(
            db,
            PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN),
            Permission.VIEW,
            _resource(BANK_A, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        )
        assert not decision.allowed
        assert decision.reason == "no_active_exact_binding"

    cross_tenant = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(principal_user_id=USER_2),
    )
    assert cross_tenant.status_code == 404
    assert (
        grant_client.get(
            "/api/v1/authorization/bindings",
            headers=_owner_headers(),
            params={"principal_user_id": str(USER_2)},
        ).status_code
        == 404
    )


def test_revoke_ends_current_sign_ins_and_preserves_unrelated_grants(
    grant_client: TestClient,
) -> None:
    first = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(role="viewer"),
    ).json()["binding"]
    second = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(
            role="viewer",
            institution_id=BANK_B,
            module="reg",
            sensitivity="restricted",
            reason="Keep independent regulatory read access",
        ),
    ).json()["binding"]

    with _session() as db:
        user = db.get(User, GRANTEE)
        assert user is not None
        issued = authentication.issue_tokens(db, user)
        current_version = user.authorization_version

    revoked = grant_client.post(
        f"/api/v1/authorization/bindings/{first['id']}/revoke",
        headers=_owner_headers(),
        json={"reason": "Liquidity responsibilities moved to another officer"},
    )
    assert revoked.status_code == 200, revoked.text
    revoked_body = revoked.json()
    assert revoked_body["status"] == "revoked"
    assert revoked_body["revoked_by_id"] == str(USER_1)
    assert revoked_body["sensitivity_scope"] == "confidential"

    next_action = grant_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )
    assert next_action.status_code == 401
    assert "sign in again" in next_action.json()["error"]["message"].lower()
    assert (
        grant_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": issued.refresh_token},
        ).status_code
        == 401
    )

    with _session() as db:
        user = db.get(User, GRANTEE)
        assert user is not None
        assert user.authorization_version == current_version + 1
        rows = {
            row.id: row
            for row in db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE
                )
            )
        }
        assert rows[UUID(first["id"])].status == "revoked"
        assert rows[UUID(second["id"])].status == "active"
        principal = PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN)
        assert not authorization.evaluate_permission(
            db,
            principal,
            Permission.VIEW,
            _resource(BANK_A, Module.LIQUIDITY, Sensitivity.CONFIDENTIAL),
        ).allowed
        assert authorization.evaluate_permission(
            db,
            principal,
            Permission.VIEW,
            _resource(BANK_B, Module.REGULATORY, Sensitivity.RESTRICTED),
        ).allowed
        refresh_rows = list(db.scalars(select(RefreshToken).where(RefreshToken.user_id == GRANTEE)))
        assert refresh_rows
        assert all(row.revoked_reason == "authorization_changed" for row in refresh_rows)
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_revoked",
                AuditEvent.entity_id == first["id"],
            )
        )
        assert audit is not None
        assert audit.details["scope"]["sensitivity_scope"] == "confidential"
        assert audit.details["reason"] == "Liquidity responsibilities moved to another officer"


def test_server_returns_warn_and_block_sod_decisions(grant_client: TestClient) -> None:
    analyst = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(),
    )
    assert analyst.status_code == 201
    warning = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(role="approver", reason="Independent checker duties"),
    )
    assert warning.status_code == 201, warning.text
    assert warning.json()["sod_decision"]["outcome"] == "warn"
    assert warning.json()["sod_decision"]["findings"][0]["code"] == (
        "maker_checker_runtime_condition_required"
    )

    blocked = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_payload(principal_user_id=USER_1, reason="Owner requests operational authority"),
    )
    assert blocked.status_code == 409, blocked.text
    decision = blocked.json()["error"]["details"]["sod_decision"]
    assert decision["outcome"] == "block"
    assert decision["findings"][0]["code"] == ("c9_account_administration_operational_conflict")


def test_sso_approval_activates_identity_only_with_a_complete_grant(
    grant_client: TestClient,
) -> None:
    pending_id = uuid4()
    with _session() as db:
        db.add(
            User(
                id=pending_id,
                organization_id=ORG_1,
                email="verified.sso@example.test",
                display_name="Verified SSO User",
                role="viewer",
                auth_provider="oidc",
                sso_subject="verified-sso-subject",
                is_active=False,
            )
        )
        db.commit()

    before = grant_client.get("/api/v1/organization/members", headers=_owner_headers()).json()
    pending = next(member for member in before["members"] if member["user_id"] == str(pending_id))
    assert pending["access_request_state"] == "approval_needed"
    assert pending["active_grant_count"] == 0
    assert pending["grants"] == []

    approved = grant_client.post(
        f"/api/v1/auth/sso/access-requests/{pending_id}/approve",
        headers=_owner_headers(),
        json={
            key: value
            for key, value in _payload(
                principal_user_id=pending_id,
                reason="Verified identity approved for liquidity analysis",
            ).items()
            if key != "principal_user_id"
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["binding"]["sensitivity_scope"] == "confidential"

    with _session() as db:
        user = db.get(User, pending_id)
        assert user is not None
        assert user.is_active
        assert user.role == "viewer"
        bindings = list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == pending_id
                )
            )
        )
        assert len(bindings) == 1
        assert bindings[0].module_scope == "liq"
        assert bindings[0].sensitivity_scope == "confidential"
