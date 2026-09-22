"""Acceptance coverage for scoped grant administration and Members."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
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
from app.models import (
    AuditEvent,
    AuthorizationAccessRequest,
    AuthorizationBinding,
    Bank,
    InstitutionType,
    RefreshToken,
    User,
)
from app.services import authentication, authorization, grant_administration
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

GRANTEE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BANK_A = "BK-GRNT0001"
BANK_B = "BK-GRNT0002"

# This concurrency test needs PostgreSQL row locks and independent sessions.
requires_committing_db = pytest.mark.committing_db


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
        "reason_category": "other",
        "reason_detail": reason,
    }


def _reviewed_payload(  # noqa: PLR0913
    client: TestClient,
    payload: dict[str, object] | None = None,
    *,
    role: str = "analyst",
    institution_id: str = BANK_A,
    module: str = "liq",
    sensitivity: str = "confidential",
    principal_user_id: UUID = GRANTEE,
    reason: str = "Treasury responsibilities approved by the Head of Treasury",
) -> dict[str, object]:
    reviewed = dict(
        payload
        or _payload(
            role=role,
            institution_id=institution_id,
            module=module,
            sensitivity=sensitivity,
            principal_user_id=principal_user_id,
            reason=reason,
        )
    )
    preview = client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(),
        json=reviewed,
    )
    assert preview.status_code == 200, preview.text
    reviewed["expected_authority_sentence"] = preview.json()["authority_sentence"]
    return reviewed


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
        json=_reviewed_payload(grant_client),
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
        assert audit.details["reason"] == _payload()["reason_detail"]
        assert audit.details["reason_category"] == "other"


@pytest.mark.parametrize(
    ("module", "sentence"),
    [
        (
            Module.CREDIT,
            "Amma Owusu is a Viewer in Credit for Aequor Bank Ghana, covering Aggregated data.",
        ),
        (
            Module.INSTITUTION,
            "Amma Owusu is a Viewer in Institution Profile for Aequor Bank Ghana, "
            "covering Aggregated data.",
        ),
    ],
    ids=["credit", "institution"],
)
def test_credit_and_institution_grants_round_trip_as_exact_vocabulary(
    grant_client: TestClient, module: Module, sentence: str
) -> None:
    """The two modules are grantable vocabulary and nothing more.

    Nothing consumes either module yet, so the grant must be exact in every
    dimension, must not reach a neighbouring module, and must revoke on its own.
    """

    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client, role="viewer", module=module.value, sensitivity="aggregated"
        ),
    )
    assert created.status_code == 201, created.text
    binding = created.json()["binding"]
    assert binding["module_scope"] == module.value
    assert binding["authority_sentence"] == sentence

    listed = grant_client.get(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        params={"principal_user_id": str(GRANTEE)},
    )
    assert listed.status_code == 200, listed.text
    assert [row["module_scope"] for row in listed.json()["bindings"]] == [module.value]

    with _session() as db:
        principal = PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN)
        assert authorization.evaluate_permission(
            db, principal, Permission.VIEW, _resource(BANK_A, module, Sensitivity.AGGREGATED)
        ).allowed
        neighbours = (Module.RISK, Module.ACCOUNT, Module.CAPITAL)
        for other in neighbours:
            assert not authorization.evaluate_permission(
                db, principal, Permission.VIEW, _resource(BANK_A, other, Sensitivity.AGGREGATED)
            ).allowed, other
        assert not authorization.evaluate_permission(
            db, principal, Permission.VIEW, _resource(BANK_B, module, Sensitivity.AGGREGATED)
        ).allowed
        assert not authorization.evaluate_permission(
            db, principal, Permission.RUN, _resource(BANK_A, module, Sensitivity.AGGREGATED)
        ).allowed

    revoked = grant_client.post(
        f"/api/v1/authorization/bindings/{binding['id']}/revoke",
        headers=_owner_headers(),
        json={"reason": "Responsibility reassigned"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["module_scope"] == module.value

    with _session() as db:
        principal = PrincipalLocator(ORG_1, GRANTEE, PrincipalType.HUMAN)
        assert not authorization.evaluate_permission(
            db, principal, Permission.VIEW, _resource(BANK_A, module, Sensitivity.AGGREGATED)
        ).allowed
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_revoked",
                AuditEvent.entity_id == binding["id"],
            )
        )
        assert audit is not None
        assert audit.details["scope"]["module_scope"] == module.value
        assert audit.details["authority_sentence"] == sentence


def test_preview_and_persisted_organization_wide_sentences_are_identical(
    grant_client: TestClient,
) -> None:
    payload = _payload(role="viewer")
    payload.update(
        institution_scope="organization",
        institution_id=None,
        module_scope="all",
        sensitivity_scope="all",
    )
    preview = grant_client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(),
        json=payload,
    )
    assert preview.status_code == 200, preview.text

    previewed_sentence = preview.json()["authority_sentence"]
    payload["expected_authority_sentence"] = previewed_sentence
    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=payload,
    )
    assert created.status_code == 201, created.text
    assert previewed_sentence == created.json()["binding"]["authority_sentence"]

    with _session() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_granted",
                AuditEvent.entity_id == created.json()["binding"]["id"],
            )
        )
        assert audit is not None
        assert audit.details["authority_sentence"] == previewed_sentence


def test_stale_review_is_rejected_without_binding_or_audit_mutation(
    grant_client: TestClient,
) -> None:
    payload = _reviewed_payload(grant_client)
    with _session() as db:
        member = db.get(User, GRANTEE)
        assert member is not None
        member.display_name = "Amma Mensah"
        db.commit()

    response = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=payload,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["message"] == (
        "The selection changed and must be reviewed again."
    )

    with _session() as db:
        assert not list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE
                )
            )
        )
        audits = list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "authorization.binding_granted")
            )
        )
        assert audits == []


def test_duplicate_effective_grant_is_targetable_and_revoke_removes_equivalent_authority(
    grant_client: TestClient,
) -> None:
    payload = _reviewed_payload(grant_client, role="viewer")
    first = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=payload,
    )
    assert first.status_code == 201, first.text
    binding = first.json()["binding"]

    duplicate = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=payload,
    )
    assert duplicate.status_code == 409, duplicate.text
    details = duplicate.json()["error"]["details"]
    assert details["existing_binding_id"] == binding["id"]
    assert binding["authority_sentence"] in details["message"]
    assert binding["id"] not in details["message"]

    revoked = grant_client.post(
        f"/api/v1/authorization/bindings/{binding['id']}/revoke",
        headers=_owner_headers(),
        json={"reason": "This exact authority is no longer required"},
    )
    assert revoked.status_code == 200, revoked.text
    with _session() as db:
        equivalents = [
            row
            for row in db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.role_bundle == "viewer",
                    AuthorizationBinding.institution_scope == "institution",
                    AuthorizationBinding.institution_id == BANK_A,
                    AuthorizationBinding.module_scope == "liq",
                    AuthorizationBinding.sensitivity_scope == "confidential",
                )
            )
            if grant_administration.binding_is_effective(row)
        ]
        assert equivalents == []


@requires_committing_db
def test_concurrent_conflicting_grants_serialize_before_sod_decision(
    grant_client: TestClient,
) -> None:
    _ = grant_client
    sessionmaker = get_sessionmaker()
    with sessionmaker() as dialect_session:
        if dialect_session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks are required for concurrency coverage.")

    analyst_scope = authorization.BindingScope(
        InstitutionScope.INSTITUTION,
        BANK_A,
        ModuleScope.LIQUIDITY,
        SensitivityScope.CONFIDENTIAL,
    )
    account_scope = authorization.BindingScope(
        InstitutionScope.ORGANIZATION,
        None,
        ModuleScope.ACCOUNT,
        SensitivityScope.ALL,
    )
    with sessionmaker() as analyst_session:
        analyst_session.info["organization_id"] = ORG_1
        grant_administration.create_scoped_grant(
            analyst_session,
            organization_id=ORG_1,
            principal_user_id=GRANTEE,
            role_bundle=RoleBundle.ANALYST,
            scope=analyst_scope,
            actor_user_id=USER_1,
            reason="Concurrent analyst assignment",
            expected_authority_sentence=grant_administration.scoped_authority_sentence(
                analyst_session,
                organization_id=ORG_1,
                principal_user_id=GRANTEE,
                role_bundle=RoleBundle.ANALYST,
                scope=analyst_scope,
            ),
            commit=False,
        )

        def grant_account_administration() -> str:
            with sessionmaker() as account_session:
                account_session.info["organization_id"] = ORG_1
                try:
                    grant_administration.create_scoped_grant(
                        account_session,
                        organization_id=ORG_1,
                        principal_user_id=GRANTEE,
                        role_bundle=RoleBundle.ACCOUNT_ADMIN,
                        scope=account_scope,
                        actor_user_id=USER_1,
                        reason="Concurrent account administration assignment",
                        expected_authority_sentence=(
                            grant_administration.scoped_authority_sentence(
                                account_session,
                                organization_id=ORG_1,
                                principal_user_id=GRANTEE,
                                role_bundle=RoleBundle.ACCOUNT_ADMIN,
                                scope=account_scope,
                            )
                        ),
                    )
                except grant_administration.SodPolicyBlocked:
                    return "blocked"
                return "created"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(grant_account_administration)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=0.2)
            analyst_session.commit()
            assert future.result(timeout=5) == "blocked"

    with _session() as verification_session:
        active_bundles = set(
            verification_session.scalars(
                select(AuthorizationBinding.role_bundle).where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.status == "active",
                )
            )
        )
    assert RoleBundle.ANALYST.value in active_bundles
    assert RoleBundle.ACCOUNT_ADMIN.value not in active_bundles


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
        json=_reviewed_payload(grant_client),
    )
    second = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
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
        json={**_payload(principal_user_id=USER_2), "expected_authority_sentence": "unseen"},
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


def test_institution_directory_is_account_plane_and_owner_gated(
    grant_client: TestClient,
) -> None:
    """Scoping a grant must not depend on what the Owner can personally view.

    `/banks` filters to institutions the caller holds a capability for, which is
    empty for an Owner holding Account alone — so the Members composer used to
    offer them no institution at all. The directory is gated on the owner
    binding and lists the whole organization regardless.
    """

    with _session() as db:
        # Leave the owner with ONLY the ownership sentence: no operational view.
        for binding in db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == ORG_1,
                AuthorizationBinding.principal_user_id == USER_1,
                AuthorizationBinding.role_bundle != RoleBundle.ORG_OWNER.value,
            )
        ):
            db.delete(binding)
        db.commit()

    operational = grant_client.get("/api/v1/banks", headers=_owner_headers())
    assert operational.status_code == 200, operational.text
    assert operational.json()["banks"] == []

    directory = grant_client.get("/api/v1/organization/institutions", headers=_owner_headers())
    assert directory.status_code == 200, directory.text
    listed = {
        entry["id"]: (entry["name"], entry["short_name"])
        for entry in directory.json()["institutions"]
    }
    assert listed[BANK_A] == ("Aequor Bank Ghana", "Aequor Ghana")
    assert listed[BANK_B] == ("Aequor Rural Bank", "Aequor Rural")
    with _session() as db:
        assert set(listed) == set(db.scalars(select(Bank.id).where(Bank.organization_id == ORG_1)))
    names = [entry["name"] for entry in directory.json()["institutions"]]
    assert names == sorted(names)

    # A member without the owner binding gets the same generic refusal as every
    # other grant-administration route, whatever their scalar role says.
    for roles in (("viewer",), ("account_admin",), ("admin",)):
        denied = grant_client.get(
            "/api/v1/organization/institutions",
            headers=headers(user_id=GRANTEE, roles=roles),
        )
        assert denied.status_code == 403, denied.text

    # A member of another tenant holds no owner binding here and is refused.
    other = grant_client.get(
        "/api/v1/organization/institutions",
        headers=headers(org_id=ORG_2, user_id=USER_2, roles=("account_admin",)),
    )
    assert other.status_code == 403, other.text


def test_revoke_ends_current_sign_ins_and_preserves_unrelated_grants(
    grant_client: TestClient,
) -> None:
    first = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(grant_client, role="viewer"),
    ).json()["binding"]
    second = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
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


def test_members_distinguish_unattributed_historical_revoker(
    grant_client: TestClient,
) -> None:
    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(grant_client, role="viewer"),
    ).json()["binding"]
    revoked = grant_client.post(
        f"/api/v1/authorization/bindings/{created['id']}/revoke",
        headers=_owner_headers(),
        json={"reason": "Historical revocation"},
    )
    assert revoked.status_code == 200, revoked.text

    with _session() as db:
        binding = db.get(AuthorizationBinding, UUID(created["id"]))
        assert binding is not None
        binding.revoked_by_type = "system"
        binding.revoked_by_id = "revoker-not-recorded-predates-attribution"
        db.commit()

    response = grant_client.get("/api/v1/organization/members", headers=_owner_headers())
    assert response.status_code == 200, response.text
    member = next(row for row in response.json()["members"] if row["user_id"] == str(GRANTEE))
    assert member["grants"][0]["revoked_by_name"] == (
        "Revoker not recorded (predates attribution requirement)"
    )


def test_server_returns_warn_and_block_sod_decisions(grant_client: TestClient) -> None:
    analyst = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(grant_client),
    )
    assert analyst.status_code == 201
    warning = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
            role="approver",
            reason="Independent checker duties",
        ),
    )
    assert warning.status_code == 201, warning.text
    assert warning.json()["sod_decision"]["outcome"] == "warn"
    assert warning.json()["sod_decision"]["findings"][0]["code"] == (
        "maker_checker_runtime_condition_required"
    )

    # Order matters: the owner exception is taken LAST. Granting the owner an
    # operational binding invalidates their own sessions in the same
    # transaction — the authorisation-version bump this codebase relies on — so
    # any request made with the same headers afterwards is correctly a 401.
    # A DELEGATED account administrator still cannot: they are not the
    # accountable principal and cannot self-authorise the exception. A fresh
    # identity, because GRANTEE already carries operational bundles from the
    # cases above and would be refused by the mirror rule instead.
    delegate = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    account_scope = authorization.BindingScope(
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module_scope=ModuleScope.ACCOUNT,
        sensitivity_scope=SensitivityScope.ALL,
    )
    with _session() as db:
        db.add(
            User(
                id=delegate,
                organization_id=ORG_1,
                email="delegated.admin@example.test",
                display_name="Delegated Admin",
                role="viewer",
            )
        )
        db.commit()
        grant_administration.create_scoped_grant(
            db,
            organization_id=ORG_1,
            principal_user_id=delegate,
            role_bundle=RoleBundle.ACCOUNT_ADMIN,
            scope=account_scope,
            actor_user_id=USER_1,
            reason="Delegated account administration",
            expected_authority_sentence=grant_administration.scoped_authority_sentence(
                db,
                organization_id=ORG_1,
                principal_user_id=delegate,
                role_bundle=RoleBundle.ACCOUNT_ADMIN,
                scope=account_scope,
            ),
        )
        db.commit()

    blocked = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
            principal_user_id=delegate,
            reason="Account administrator requests operational authority",
        ),
    )
    assert blocked.status_code == 409, blocked.text
    decision = blocked.json()["error"]["details"]["sod_decision"]
    assert decision["outcome"] == "block"
    assert decision["findings"][0]["code"] == ("c9_account_administration_operational_conflict")

    # The OWNER may accept the C9 exception for themselves (founder decision
    # 2026-09-20). It is recorded, not waived: the finding still comes back, so
    # an examiner reads an accepted risk rather than an absent control. The
    # reasoning behind C9 is unchanged — see check_sod_policy — and this
    # returns to a block once the per-object condition can catch it at action
    # time.
    owner_exception = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
            principal_user_id=USER_1,
            reason="Owner requests operational authority",
        ),
    )
    assert owner_exception.status_code == 201, owner_exception.text
    owner_decision = owner_exception.json()["sod_decision"]
    assert owner_decision["outcome"] == "warn"
    assert owner_decision["findings"][0]["code"] == "c9_owner_operational_exception"


def test_approving_and_filing_cannot_land_on_one_identity(grant_client: TestClient) -> None:
    """The assignment-time half of the filing split (2026-09-20).

    Approving a return and transmitting it to the regulator are now two
    permissions, but two permissions on one person is one person filing their
    own approval. The per-object condition that would catch that at action time
    belongs to the stage engine and does not exist yet
    (docs/filing_workflow_redesign.md §3.3 layer 3), so the separation is
    enforced where it currently can be: the grant is refused, in both
    directions, and the refusal is a BLOCK rather than a warning.

    It is also scope-independent, unlike the Analyst/Approver warning above:
    one Validator grant files every return family, so an approval grant on any
    module overlaps it.
    """
    approver = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(grant_client, role="approver", reason="Independent checker duties"),
    )
    assert approver.status_code == 201, approver.text

    blocked = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
            role="validator",
            module="reg",
            sensitivity="restricted",
            reason="Also files the returns",
        ),
    )
    assert blocked.status_code == 409, blocked.text
    decision = blocked.json()["error"]["details"]["sod_decision"]
    assert decision["outcome"] == "block"
    assert decision["findings"][0]["code"] == "approval_and_transmission_separation_required"


def test_a_validator_grant_is_accepted_for_an_identity_that_does_not_approve(
    grant_client: TestClient,
) -> None:
    """The authority has to be grantable, or no return could ever be filed."""
    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(
            grant_client,
            role="validator",
            module="reg",
            sensitivity="restricted",
            reason="Files this institution's returns to the regulator",
        ),
    )
    assert created.status_code == 201, created.text
    assert created.json()["sod_decision"]["outcome"] == "allow"
    assert created.json()["binding"]["role_bundle"] == "validator"
    assert "Validator" in created.json()["binding"]["authority_sentence"]


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

    approval_payload = {
        key: value
        for key, value in _payload(
            principal_user_id=pending_id,
            reason="Verified identity approved for liquidity analysis",
        ).items()
        if key != "principal_user_id"
    }
    preview_payload = {**approval_payload, "principal_user_id": str(pending_id)}
    preview = grant_client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(),
        json=preview_payload,
    )
    assert preview.status_code == 200, preview.text
    approval_payload["expected_authority_sentence"] = preview.json()["authority_sentence"]

    approved = grant_client.post(
        f"/api/v1/auth/sso/access-requests/{pending_id}/approve",
        headers=_owner_headers(),
        json=approval_payload,
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
        assert {binding.role_bundle for binding in bindings} == {"member", "analyst"}
        baseline = next(binding for binding in bindings if binding.role_bundle == "member")
        assert baseline.institution_scope == "organization"
        assert baseline.module_scope == "account"
        assert baseline.sensitivity_scope == "restricted"
        grant = next(binding for binding in bindings if binding.role_bundle == "analyst")
        assert grant.module_scope == "liq"
        assert grant.sensitivity_scope == "confidential"


def test_active_member_route_access_request_is_deduplicated_audited_and_approved(
    grant_client: TestClient,
) -> None:
    member_headers = headers(
        user_id=GRANTEE,
        roles=("viewer",),
        authorization_version=1,
    )
    payload = {
        "route": "/fx",
        "institution_id": BANK_A,
        "module_scope": "fx",
        "sensitivity_scope": "aggregated",
        "permission": "view",
        "reason_category": "project_engagement",
        "reason_detail": "Supporting the treasury hedging review",
        "reference": "CHG-2026-0918",
    }

    object_route = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload | {"route": "/fx/trades/secret-trade"},
    )
    assert object_route.status_code == 404
    forged_requirement = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload | {"module_scope": "risk"},
    )
    assert forged_requirement.status_code == 404

    created = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    duplicate = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload,
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["id"] == created.json()["id"]

    mine = grant_client.get(
        "/api/v1/authorization/access-requests/mine",
        headers=member_headers,
    )
    assert mine.status_code == 200
    assert len(mine.json()["requests"]) == 1

    pending = grant_client.get(
        "/api/v1/authorization/access-requests",
        headers=_owner_headers(),
    )
    assert pending.status_code == 200
    assert pending.json()["requests"][0]["reference"] == "CHG-2026-0918"

    preview_payload = {
        "principal_user_id": str(GRANTEE),
        "role_bundle": "viewer",
        "institution_scope": "institution",
        "institution_id": BANK_A,
        "module_scope": "fx",
        "sensitivity_scope": "aggregated",
        "reason_category": "project_engagement",
        "reason_detail": "Supporting the treasury hedging review",
        "reference": "CHG-2026-0918",
    }
    preview = grant_client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(),
        json=preview_payload,
    )
    assert preview.status_code == 200, preview.text
    approved = grant_client.post(
        f"/api/v1/authorization/access-requests/{created.json()['id']}/approve",
        headers=_owner_headers(),
        json={key: value for key, value in preview_payload.items() if key != "principal_user_id"}
        | {"expected_authority_sentence": preview.json()["authority_sentence"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["binding"]["grant_reason_category"] == "project_engagement"
    assert approved.json()["binding"]["grant_reference"] == "CHG-2026-0918"

    with _session() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.access_requested",
                AuditEvent.entity_id == created.json()["id"],
            )
        )
        assert audit is not None
        assert audit.actor_user_id == GRANTEE


def test_organization_scoped_access_request_carries_expiry_into_the_grant(
    grant_client: TestClient,
) -> None:
    """Account Administration routes are evaluated organization-wide, so the
    request names no institution and approval must preserve that scope; a
    temporary cover request carries its own expiry through to the binding."""

    expiry = datetime.now(UTC) + timedelta(days=30)
    member_headers = headers(user_id=GRANTEE, roles=("viewer",), authorization_version=1)
    payload = {
        "route": "/institution",
        "module_scope": "account",
        "sensitivity_scope": "restricted",
        "permission": "view",
        "reason_category": "temporary_cover",
        "reason_detail": "Covering the registers desk during leave",
        "valid_until": expiry.isoformat(),
    }

    institution_named = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload | {"institution_id": BANK_A},
    )
    assert institution_named.status_code == 422
    missing_expiry = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json={key: value for key, value in payload.items() if key != "valid_until"},
    )
    assert missing_expiry.status_code == 422
    institution_route_without_target = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload | {"route": "/fx", "module_scope": "fx", "sensitivity_scope": "aggregated"},
    )
    assert institution_route_without_target.status_code == 422

    created = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["institution_scope"] == "organization"
    assert body["institution_id"] is None
    assert body["institution_name"] is None
    request_expiry = datetime.fromisoformat(body["valid_until"])
    if request_expiry.tzinfo is None:
        request_expiry = request_expiry.replace(tzinfo=UTC)
    assert request_expiry == expiry
    duplicate = grant_client.post(
        "/api/v1/authorization/access-requests",
        headers=member_headers,
        json=payload,
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]

    grant_payload = {
        "role_bundle": "viewer",
        "institution_scope": "organization",
        "module_scope": "account",
        "sensitivity_scope": "restricted",
        "reason_category": "temporary_cover",
        "reason_detail": body["reason_detail"],
        "valid_until": body["valid_until"],
    }
    preview = grant_client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(),
        json=grant_payload | {"principal_user_id": str(GRANTEE)},
    )
    assert preview.status_code == 200, preview.text
    sentence = preview.json()["authority_sentence"]
    narrowed = grant_client.post(
        f"/api/v1/authorization/access-requests/{body['id']}/approve",
        headers=_owner_headers(),
        json=grant_payload
        | {
            "institution_scope": "institution",
            "institution_id": BANK_A,
            "expected_authority_sentence": sentence,
        },
    )
    assert narrowed.status_code == 409
    approved = grant_client.post(
        f"/api/v1/authorization/access-requests/{body['id']}/approve",
        headers=_owner_headers(),
        json=grant_payload | {"expected_authority_sentence": sentence},
    )
    assert approved.status_code == 200, approved.text
    binding = approved.json()["binding"]
    assert binding["institution_scope"] == "organization"
    assert binding["institution_id"] is None
    assert binding["module_scope"] == "account"
    assert binding["sensitivity_scope"] == "restricted"
    binding_expiry = datetime.fromisoformat(binding["valid_until"])
    if binding_expiry.tzinfo is None:
        binding_expiry = binding_expiry.replace(tzinfo=UTC)
    assert binding_expiry == expiry

    with _session() as db:
        user = db.get(User, GRANTEE)
        assert user is not None
        issued = authentication.issue_tokens(db, user)
    me = grant_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {issued.access_token}"},
    )
    assert me.status_code == 200, me.text
    assert {
        "module": "account",
        "sensitivity": "restricted",
        "permission": "view",
    } in [
        {key: capability[key] for key in ("module", "sensitivity", "permission")}
        for capability in me.json()["effective_authority"]["organization_capabilities"]
    ]


def test_access_request_directory_exposes_uncovered_institution_class(
    grant_client: TestClient,
) -> None:
    with _session() as db:
        bank = db.get(Bank, BANK_B)
        assert bank is not None
        sdi_type = db.scalar(
            select(InstitutionType).where(InstitutionType.institution_class == "sdi")
        )
        assert sdi_type is not None
        bank.institution_type = sdi_type.type_code
        db.commit()

    member_headers = headers(user_id=GRANTEE, roles=("viewer",))
    operational = grant_client.get("/api/v1/banks", headers=member_headers)
    assert operational.status_code == 200, operational.text
    assert operational.json()["banks"] == []

    for path, actor_headers in (
        ("/api/v1/organization/institutions", _owner_headers()),
        ("/api/v1/organization/institutions/access-request", member_headers),
    ):
        response = grant_client.get(path, headers=actor_headers)
        assert response.status_code == 200, response.text
        entries = {entry["id"]: entry for entry in response.json()["institutions"]}
        assert entries[BANK_A]["institution_class"] == "bank"
        assert entries[BANK_B]["institution_class"] == "sdi"
        with _session() as db:
            assert set(entries) == set(
                db.scalars(select(Bank.id).where(Bank.organization_id == ORG_1))
            )


@pytest.mark.parametrize("existing_status", ["active", "revoked", "composer"])
def test_route_requests_sharing_requirement_resolve_against_effective_binding(
    grant_client: TestClient,
    existing_status: str,
) -> None:
    request_ids = []
    for route in ("/liquidity/forecast", "/liquidity/monitoring"):
        response = grant_client.post(
            "/api/v1/authorization/access-requests",
            headers=headers(user_id=GRANTEE, roles=("viewer",)),
            json={
                "route": route,
                "institution_id": BANK_A,
                "module_scope": "liq",
                "sensitivity_scope": "confidential",
                "permission": "view",
                "reason_category": "role_change",
            },
        )
        assert response.status_code == 201, response.text
        request_ids.append(response.json()["id"])
    payload = _reviewed_payload(grant_client, role="viewer")
    payload.pop("principal_user_id")
    first = grant_client.post(
        f"/api/v1/authorization/access-requests/{request_ids[0]}/approve",
        headers=_owner_headers(),
        json=payload,
    )
    assert first.status_code == 200, first.text
    first_binding = first.json()["binding"]["id"]
    if existing_status == "revoked":
        revoked = grant_client.post(
            f"/api/v1/authorization/bindings/{first_binding}/revoke",
            headers=_owner_headers(),
            json={"reason": "Revoke before resolving the second route request"},
        )
        assert revoked.status_code == 200, revoked.text
    with _session() as db:
        user = db.get(User, GRANTEE)
        assert user is not None
        version = user.authorization_version

    if existing_status == "composer":
        second = grant_client.post(
            "/api/v1/authorization/bindings",
            headers=_owner_headers(),
            json=_reviewed_payload(grant_client, role="viewer", module="fx"),
        )
        assert second.status_code == 201, second.text
        with _session() as db:
            request = db.get(AuthorizationAccessRequest, UUID(request_ids[1]))
            assert request is not None
            second_binding = str(request.binding_id)
    else:
        second = grant_client.post(
            f"/api/v1/authorization/access-requests/{request_ids[1]}/approve",
            headers=_owner_headers(),
            json=payload,
        )
        assert second.status_code == 200, second.text
        second_binding = second.json()["binding"]["id"]
    assert (first_binding == second_binding) == (existing_status != "revoked")
    with _session() as db:
        rows = list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.organization_id == ORG_1,
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.role_bundle == "viewer",
                )
            )
        )
        assert len(rows) == (2 if existing_status in {"revoked", "composer"} else 1)
        user = db.get(User, GRANTEE)
        assert user is not None
        assert user.authorization_version == version + (existing_status != "active")
        for request_id, binding_id in zip(
            request_ids, (first_binding, second_binding), strict=True
        ):
            request = db.get(AuthorizationAccessRequest, UUID(request_id))
            assert request is not None
            assert request.status == "approved"
            assert request.binding_id == UUID(binding_id)
            assert request.resolved_at is not None
            assert request.resolved_by_user_id == USER_1
            audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "authorization.access_request_approved",
                        AuditEvent.entity_id == request_id,
                    )
                )
            )
            assert len(audits) == 1
            assert audits[0].actor_user_id == USER_1
            assert audits[0].details["binding_id"] == binding_id
            assert audits[0].details["authority_sentence"] == (
                first.json()["binding"]["authority_sentence"]
            )
    pending = grant_client.get("/api/v1/authorization/access-requests", headers=_owner_headers())
    assert pending.status_code == 200
    assert pending.json()["requests"] == []


def _file_route_request(client: TestClient, route: str, **overrides: object) -> str:
    response = client.post(
        "/api/v1/authorization/access-requests",
        headers=headers(user_id=GRANTEE, roles=("viewer",)),
        json={
            "route": route,
            "institution_id": BANK_A,
            "module_scope": "liq",
            "sensitivity_scope": "confidential",
            "permission": "view",
            "reason_category": "role_change",
        }
        | overrides,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_rejected_request_is_audited_with_its_reason_and_can_be_refiled(
    grant_client: TestClient,
) -> None:
    request_id = _file_route_request(grant_client, "/liquidity/forecast")
    sibling_headers = headers(
        user_id=GRANTEE,
        roles=("viewer",),
    )
    missing_detail = grant_client.post(
        f"/api/v1/authorization/access-requests/{request_id}/reject",
        headers=_owner_headers(),
        json={"reason_category": "other"},
    )
    assert missing_detail.status_code == 422
    member_cannot_reject = grant_client.post(
        f"/api/v1/authorization/access-requests/{request_id}/reject",
        headers=sibling_headers,
        json={"reason_category": "role_change"},
    )
    assert member_cannot_reject.status_code in {403, 404}

    rejected = grant_client.post(
        f"/api/v1/authorization/access-requests/{request_id}/reject",
        headers=_owner_headers(),
        json={
            "reason_category": "other",
            "reason_detail": "Treasury forecasting is not part of this role",
            "reference": "HR-2026-0042",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    again = grant_client.post(
        f"/api/v1/authorization/access-requests/{request_id}/reject",
        headers=_owner_headers(),
        json={"reason_category": "role_change"},
    )
    assert again.status_code == 404

    with _session() as db:
        request = db.get(AuthorizationAccessRequest, UUID(request_id))
        assert request is not None
        assert request.status == "rejected"
        assert request.binding_id is None
        assert request.resolved_by_user_id == USER_1
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.access_request_rejected",
                AuditEvent.entity_id == request_id,
            )
        )
        assert audit is not None
        assert audit.actor_user_id == USER_1
        assert audit.details["reason_category"] == "other"
        assert audit.details["reason_detail"] == "Treasury forecasting is not part of this role"
        assert audit.details["reference"] == "HR-2026-0042"
        assert not list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.role_bundle == "viewer",
                )
            )
        )
    pending = grant_client.get("/api/v1/authorization/access-requests", headers=_owner_headers())
    assert pending.json()["requests"] == []

    refiled = _file_route_request(grant_client, "/liquidity/forecast")
    assert refiled != request_id
    mine = grant_client.get(
        "/api/v1/authorization/access-requests/mine",
        headers=sibling_headers,
    )
    assert [row["status"] for row in mine.json()["requests"]] == ["pending", "rejected"]


def test_composer_grant_resolves_the_requests_it_satisfies(grant_client: TestClient) -> None:
    satisfied = _file_route_request(grant_client, "/liquidity/forecast")
    unrelated = _file_route_request(
        grant_client, "/fx", module_scope="fx", sensitivity_scope="aggregated"
    )

    created = grant_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed_payload(grant_client, role="viewer"),
    )
    assert created.status_code == 201, created.text
    binding_id = created.json()["binding"]["id"]

    with _session() as db:
        resolved = db.get(AuthorizationAccessRequest, UUID(satisfied))
        assert resolved is not None
        assert resolved.status == "approved"
        assert resolved.binding_id == UUID(binding_id)
        assert resolved.resolved_by_user_id == USER_1
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.access_request_approved",
                AuditEvent.entity_id == satisfied,
            )
        )
        assert audit is not None
        assert audit.details["binding_id"] == binding_id
        assert audit.details["resolution"] == "satisfied_by_grant"
        still_pending = db.get(AuthorizationAccessRequest, UUID(unrelated))
        assert still_pending is not None
        assert still_pending.status == "pending"
    pending = grant_client.get("/api/v1/authorization/access-requests", headers=_owner_headers())
    assert [row["id"] for row in pending.json()["requests"]] == [unrelated]


@requires_committing_db
def test_composer_does_not_overwrite_concurrent_request_rejection(
    grant_client: TestClient,
) -> None:
    from app.api.deps import TenantContext
    from app.features.manage_authorization import reject_authorization_access_request
    from app.schemas.authorization import AccessRequestReject

    with _session() as db:
        if db.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks are required for concurrency coverage.")
    request_id = _file_route_request(grant_client, "/liquidity/forecast")
    payload = _reviewed_payload(grant_client, role="viewer")
    with _session() as rejecting:
        request = rejecting.scalar(
            select(AuthorizationAccessRequest)
            .where(AuthorizationAccessRequest.id == UUID(request_id))
            .with_for_update()
        )
        assert request is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                grant_client.post,
                "/api/v1/authorization/bindings",
                headers=_owner_headers(),
                json=payload,
            )
            try:
                with pytest.raises(FutureTimeoutError):
                    future.result(timeout=0.2)
            finally:
                reject_authorization_access_request(
                    UUID(request_id),
                    AccessRequestReject(reason_category="role_change"),
                    rejecting,
                    TenantContext(ORG_1, actor_user_id=USER_1),
                )
            response = future.result(timeout=5)
            assert response.status_code == 201, response.text
    with _session() as db:
        request = db.get(AuthorizationAccessRequest, UUID(request_id))
        assert request is not None
        assert request.status == "rejected"
        assert request.binding_id is None
        decisions = list(
            db.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.entity_id == request_id,
                    AuditEvent.event_type.in_(
                        [
                            "authorization.access_request_approved",
                            "authorization.access_request_rejected",
                        ]
                    ),
                )
            )
        )
        assert decisions == ["authorization.access_request_rejected"]
