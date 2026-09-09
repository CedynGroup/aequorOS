"""SSO connection config endpoints: scoped Account administration, write-only
secret handling, public status, and the internal client-config fetch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import User
from app.services import authorization
from tests.api.helpers import ORG_1, USER_1, headers

MASTER_KEY = "sso-config-test-master-key"
SECRET = "google-client-secret-that-must-never-leak"
INTERNAL_KEY = "internal-key-for-dashboard-fetch"

_PAYLOAD = {
    "issuer": "https://accounts.google.com",
    "client_id": "abc.apps.googleusercontent.com",
    "client_secret": SECRET,
    "allowed_email_domains": ["Bank.Example", "bank.example", " "],
    "enabled": True,
}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSO_INTERNAL_KEY", INTERNAL_KEY)
    get_settings.cache_clear()


@pytest.fixture
def account_admin_headers(db_client: TestClient) -> dict[str, str]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ACCOUNT_ADMIN,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise scoped SSO administration",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return headers(
            roles=("account_admin",),
            authorization_version=user.authorization_version,
        )
    finally:
        session.close()


def test_admin_upserts_connection_and_secret_is_write_only(
    db_client: TestClient,
    vault_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    put = db_client.put(
        "/api/v1/auth/sso/connection",
        json=_PAYLOAD,
        headers=account_admin_headers,
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["client_secret_set"] is True
    assert body["enabled"] is True
    assert body["allowed_email_domains"] == ["bank.example"]  # normalized + deduped
    assert SECRET not in put.text  # the secret never comes back

    got = db_client.get("/api/v1/auth/sso/connection", headers=account_admin_headers)
    assert got.status_code == 200
    assert got.json()["client_secret_set"] is True
    assert SECRET not in got.text

    # Update without a secret keeps the stored one.
    update = {**_PAYLOAD, "client_secret": None, "client_id": "rotated-client-id"}
    put2 = db_client.put(
        "/api/v1/auth/sso/connection",
        json=update,
        headers=account_admin_headers,
    )
    assert put2.status_code == 200
    assert put2.json()["client_id"] == "rotated-client-id"
    assert put2.json()["client_secret_set"] is True


def test_enabling_without_a_secret_is_refused(
    db_client: TestClient,
    vault_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    payload = {**_PAYLOAD, "client_secret": None}
    r = db_client.put(
        "/api/v1/auth/sso/connection",
        json=payload,
        headers=account_admin_headers,
    )
    assert r.status_code == 422


def test_jit_requires_a_domain_allow_list(
    db_client: TestClient,
    vault_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    refused = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "allowed_email_domains": [], "jit_enabled": True},
        headers=account_admin_headers,
    )
    assert refused.status_code == 422
    assert "domain" in refused.json()["error"]["message"].lower()

    ok = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "jit_enabled": True},
        headers=account_admin_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["jit_enabled"] is True


def test_pasted_email_is_reduced_to_its_domain(
    db_client: TestClient,
    vault_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    r = db_client.put(
        "/api/v1/auth/sso/connection",
        json={**_PAYLOAD, "allowed_email_domains": ["eric@aequoros.com"]},
        headers=account_admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["allowed_email_domains"] == ["aequoros.com"]


def test_non_admin_cannot_read_or_write_connection(db_client: TestClient) -> None:
    for role in ("approver", "analyst", "viewer"):
        assert (
            db_client.get("/api/v1/auth/sso/connection", headers=headers(roles=(role,))).status_code
            == 403
        )
        assert (
            db_client.put(
                "/api/v1/auth/sso/connection", json=_PAYLOAD, headers=headers(roles=(role,))
            ).status_code
            == 403
        )
        assert (
            db_client.get(
                "/api/v1/auth/sso/access-requests", headers=headers(roles=(role,))
            ).status_code
            == 403
        )


def test_scalar_account_admin_without_binding_cannot_manage_account_authentication(
    db_client: TestClient,
) -> None:
    account_admin_headers = headers(roles=("account_admin",))
    assert (
        db_client.get(
            "/api/v1/auth/sso/connection",
            headers=account_admin_headers,
        ).status_code
        == 403
    )
    assert (
        db_client.get("/api/v1/auth/sso/access-requests", headers=account_admin_headers).status_code
        == 403
    )


def test_explicit_account_admin_binding_can_manage_account_authentication(
    db_client: TestClient,
    account_admin_headers: dict[str, str],
) -> None:
    assert (
        db_client.get(
            "/api/v1/auth/sso/connection",
            headers=account_admin_headers,
        ).status_code
        == 200
    )
    assert (
        db_client.get(
            "/api/v1/auth/sso/access-requests",
            headers=account_admin_headers,
        ).status_code
        == 200
    )


def test_public_status_reflects_enabled_connection(
    db_client: TestClient,
    vault_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    assert db_client.get("/api/v1/auth/sso/status").json() == {"enabled": False}
    db_client.put(
        "/api/v1/auth/sso/connection",
        json=_PAYLOAD,
        headers=account_admin_headers,
    )
    assert db_client.get("/api/v1/auth/sso/status").json() == {"enabled": True}


def test_client_config_requires_the_internal_key(
    db_client: TestClient,
    vault_key: None,
    internal_key: None,
    account_admin_headers: dict[str, str],
) -> None:
    db_client.put(
        "/api/v1/auth/sso/connection",
        json=_PAYLOAD,
        headers=account_admin_headers,
    )

    assert db_client.get("/api/v1/auth/sso/client-config").status_code == 401
    assert (
        db_client.get(
            "/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": "wrong"}
        ).status_code
        == 401
    )

    ok = db_client.get("/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": INTERNAL_KEY})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["enabled"] is True
    assert body["issuer"] == "https://accounts.google.com"
    assert body["client_secret"] == SECRET  # round-trips through the vault


def test_client_config_is_disabled_when_no_internal_key_configured(
    db_client: TestClient,
) -> None:
    r = db_client.get("/api/v1/auth/sso/client-config", headers={"X-Internal-Auth": "anything"})
    assert r.status_code == 404
