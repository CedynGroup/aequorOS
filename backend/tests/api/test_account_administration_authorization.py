from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, IntegrationKey, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

UNKNOWN_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ACCOUNT_ROUTES = (
    ("get", "/api/v1/auth/sso/connection"),
    ("get", "/api/v1/auth/sso/access-requests"),
    ("get", "/api/v1/integration-keys"),
    ("post", f"/api/v1/auth/sso/access-requests/{UNKNOWN_ID}/reject"),
    ("post", f"/api/v1/integration-keys/{UNKNOWN_ID}/revoke"),
)


@pytest.fixture(autouse=True)
def _start_without_fixture_bindings(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    try:
        session.execute(delete(AuthorizationBinding))
        for user_id in (USER_1, USER_2):
            user = session.get(User, user_id)
            assert user is not None
            user.authorization_version = 1
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - each binding dimension is independently tested
    *,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
    role_bundle: RoleBundle = RoleBundle.ACCOUNT_ADMIN,
    institution_scope: InstitutionScope = InstitutionScope.ORGANIZATION,
    institution_id: str | None = None,
    module_scope: ModuleScope = ModuleScope.ACCOUNT,
    sensitivity_scope: SensitivityScope = SensitivityScope.RESTRICTED,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        binding = authorization.create_role_binding(
            session,
            organization_id=organization_id,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                institution_scope,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise scoped Account enforcement",
        )
        user = session.get(User, user_id)
        assert user is not None
        return binding.id, user.authorization_version
    finally:
        session.close()


def _request(
    client: TestClient,
    method: str,
    path: str,
    *,
    authorization_version: int = 1,
    roles: tuple[str, ...] = ("account_admin",),
):
    payload = {"reason": "authorization boundary test"} if method == "post" else None
    return client.request(
        method,
        path,
        headers=headers(
            roles=roles,
            authorization_version=authorization_version,
        ),
        json=payload,
    )


@pytest.mark.parametrize("role_bundle", [RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER])
@pytest.mark.parametrize(("method", "path"), ACCOUNT_ROUTES)
def test_complete_account_admin_or_owner_binding_allows_named_administration_routes(
    db_client: TestClient,
    role_bundle: RoleBundle,
    method: str,
    path: str,
) -> None:
    _, version = _grant(role_bundle=role_bundle)

    response = _request(
        db_client,
        method,
        path,
        authorization_version=version,
        roles=("viewer",),
    )

    assert response.status_code in {200, 404}, response.text


def test_broad_account_admin_scope_still_matches_restricted_account_resource(
    db_client: TestClient,
) -> None:
    _, version = _grant(
        module_scope=ModuleScope.ALL,
        sensitivity_scope=SensitivityScope.ALL,
    )

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), ACCOUNT_ROUTES)
def test_scalar_account_admin_or_admin_role_without_binding_defaults_to_denial(
    db_client: TestClient,
    method: str,
    path: str,
) -> None:
    for scalar_role in ("account_admin", "admin"):
        response = _request(db_client, method, path, roles=(scalar_role,))
        assert response.status_code == 403
        assert "scoped Account administration" in response.json()["error"]["message"]


@pytest.mark.parametrize("role_bundle", [RoleBundle.ANALYST, RoleBundle.APPROVER])
def test_operational_product_grants_do_not_administer_accounts(
    db_client: TestClient,
    role_bundle: RoleBundle,
) -> None:
    _, version = _grant(
        role_bundle=role_bundle,
        module_scope=ModuleScope.ALL,
        sensitivity_scope=SensitivityScope.ALL,
    )

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(
            roles=(role_bundle.value,),
            authorization_version=version,
        ),
    )

    assert response.status_code == 403


def test_account_admin_cannot_approve_sso_request_but_owner_can_reach_workflow(
    db_client: TestClient,
) -> None:
    payload = {
        "role_bundle": "viewer",
        "institution_scope": "organization",
        "institution_id": None,
        "module_scope": "account",
        "sensitivity_scope": "restricted",
        "reason": "approve verified employee identity",
        "expected_authority_sentence": "server preview placeholder",
    }
    _, account_admin_version = _grant()
    account_admin = db_client.post(
        f"/api/v1/auth/sso/access-requests/{UNKNOWN_ID}/approve",
        headers=headers(authorization_version=account_admin_version),
        json=payload,
    )
    assert account_admin.status_code == 403
    assert "Organization Owner" in account_admin.json()["error"]["message"]

    _, owner_version = _grant(role_bundle=RoleBundle.ORG_OWNER)
    owner = db_client.post(
        f"/api/v1/auth/sso/access-requests/{UNKNOWN_ID}/approve",
        headers=headers(roles=("viewer",), authorization_version=owner_version),
        json=payload,
    )
    assert owner.status_code == 404


def test_partial_bindings_never_compose_account_administration(
    db_client: TestClient,
) -> None:
    _grant(sensitivity_scope=SensitivityScope.PUBLISHED)
    _, version = _grant(module_scope=ModuleScope.AUDIT)

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("scope_changes", "bank_id"),
    [
        (
            {
                "institution_scope": InstitutionScope.INSTITUTION,
                "institution_id": "BK-ACCT0001",
            },
            "BK-ACCT0001",
        ),
        ({"module_scope": ModuleScope.AUDIT}, None),
        ({"sensitivity_scope": SensitivityScope.CONFIDENTIAL}, None),
    ],
)
def test_wrong_scope_dimensions_deny_account_administration(
    db_client: TestClient,
    scope_changes: dict[str, object],
    bank_id: str | None,
) -> None:
    if bank_id is not None:
        session = get_sessionmaker()()
        try:
            session.add(
                Bank(
                    id=bank_id,
                    organization_id=ORG_1,
                    name="Account scope test bank",
                    short_name="Account scope",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
            session.commit()
        finally:
            session.close()
    _, version = _grant(**scope_changes)  # type: ignore[arg-type]

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 403


def test_other_tenant_binding_does_not_authorize_account_administration(
    db_client: TestClient,
) -> None:
    _grant()

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(
            ORG_2,
            user_id=USER_2,
            roles=("account_admin",),
            authorization_version=1,
        ),
    )

    assert response.status_code == 403


def test_authorized_other_tenant_cannot_see_or_revoke_foreign_key(
    db_client: TestClient,
) -> None:
    key_id = uuid4()
    session = get_sessionmaker()()
    try:
        service_user = User(
            organization_id=ORG_1,
            email=f"foreign-key-{uuid4().hex}@service.aequoros.invalid",
            display_name="Foreign integration",
            role="analyst",
            auth_provider="service",
            is_active=True,
        )
        session.add(service_user)
        session.flush()
        session.add(
            IntegrationKey(
                id=key_id,
                organization_id=ORG_1,
                service_user_id=service_user.id,
                label="Foreign key",
                key_prefix="aeq_live_TEST…",
                key_hash="a" * 64,
                created_by=USER_1,
            )
        )
        session.commit()
    finally:
        session.close()
    _, version = _grant(
        organization_id=ORG_2,
        user_id=USER_2,
    )
    other_headers = headers(
        ORG_2,
        user_id=USER_2,
        roles=("account_admin",),
        authorization_version=version,
    )

    listed = db_client.get("/api/v1/integration-keys", headers=other_headers)
    revoked = db_client.post(
        f"/api/v1/integration-keys/{key_id}/revoke",
        headers=other_headers,
        json={"reason": "cross-tenant probe"},
    )

    assert listed.status_code == 200
    assert listed.json()["keys"] == []
    assert revoked.status_code == 404
    session = get_sessionmaker()()
    try:
        foreign_key = session.get(IntegrationKey, key_id)
        assert foreign_key is not None
        assert foreign_key.revoked_at is None
    finally:
        session.close()


@pytest.mark.parametrize("lifecycle", ["suspended", "expired", "revoked"])
def test_inactive_binding_lifecycle_denies_account_administration(
    db_client: TestClient,
    lifecycle: str,
) -> None:
    binding_id, version = _grant()
    session = get_sessionmaker()()
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        if lifecycle == "suspended":
            binding.status = BindingStatus.SUSPENDED.value
        elif lifecycle == "expired":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(days=1)
        else:
            binding.status = BindingStatus.REVOKED.value
            binding.revoked_at = utc_now()
            binding.revoked_by_type = GrantorType.SYSTEM.value
            binding.revoked_by_id = "test-suite"
            binding.revoked_reason = "exercise inactive lifecycle"
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 403


def test_stale_authorization_version_denies_before_account_evaluation(
    db_client: TestClient,
) -> None:
    _grant()

    response = db_client.get(
        "/api/v1/auth/sso/connection",
        headers=headers(authorization_version=1),
    )

    assert response.status_code == 401
    assert "stale" in response.json()["error"]["message"].lower()


def test_account_administer_does_not_imply_organization_directory_view(
    db_client: TestClient,
) -> None:
    _, admin_version = _grant()
    denied = db_client.get(
        "/api/v1/organization/users",
        headers=headers(authorization_version=admin_version),
    )
    assert denied.status_code == 403

    _, view_version = _grant(role_bundle=RoleBundle.VIEWER)
    allowed = db_client.get(
        "/api/v1/organization/users",
        headers=headers(roles=("viewer",), authorization_version=view_version),
    )
    assert allowed.status_code == 200, allowed.text
    assert {user["id"] for user in allowed.json()["users"]} == {str(USER_1)}


def test_denial_precedes_sso_and_key_mutation_side_effects(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_side_effect(*_args: object, **_kwargs: object) -> None:
        pytest.fail("authorization denial must happen before the service call")

    monkeypatch.setattr(
        "app.api.v1.auth.authentication.reject_sso_access_request",
        unexpected_side_effect,
    )
    monkeypatch.setattr(
        "app.api.v1.auth.sso_config.upsert_connection",
        unexpected_side_effect,
    )
    monkeypatch.setattr(
        "app.features.manage_integration_keys.integration_keys.revoke_key",
        unexpected_side_effect,
    )

    rejected = db_client.post(
        f"/api/v1/auth/sso/access-requests/{uuid4()}/reject",
        headers=headers(roles=("admin",)),
    )
    sso_update = db_client.put(
        "/api/v1/auth/sso/connection",
        headers=headers(roles=("admin",)),
        json={
            "issuer": "https://idp.example.test",
            "client_id": "must-not-reach-service",
            "client_secret": None,
            "allowed_email_domains": [],
            "enabled": False,
        },
    )
    revoked = db_client.post(
        f"/api/v1/integration-keys/{uuid4()}/revoke",
        headers=headers(roles=("admin",)),
        json={"reason": "must not reach service"},
    )

    assert rejected.status_code == 403
    assert sso_update.status_code == 403
    assert revoked.status_code == 403
