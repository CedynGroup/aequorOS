"""E2E: password login → bearer token → /auth/me, with lockout + refresh."""

from __future__ import annotations

from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import Organization, SsoConnection, User
from tests.api.helpers import headers

_EMAIL = "cfo@testbank.example"
_PASSWORD = "S3cure-Passphrase!"

_ISSUER = "https://accounts.google.com"
_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


def _seed_sso_connection(
    org_id: UUID,
    *,
    allowed_email_domains: list[str] | None = None,
    jit_enabled: bool = False,
) -> None:
    session = get_sessionmaker()()
    session.add(
        SsoConnection(
            organization_id=org_id,
            issuer=_ISSUER,
            client_id=_CLIENT_ID,
            client_secret_ciphertext="sealed-opaque",
            allowed_email_domains=allowed_email_domains or [],
            enabled=True,
            jit_enabled=jit_enabled,
        )
    )
    session.commit()
    session.close()


def _id_token(**overrides: object) -> str:
    """A structurally real (but unsigned-for-us) id_token; the verify step is
    monkeypatched, only the unverified iss/aud routing reads this payload."""
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "google-oauth2|abc123",
        "email": _EMAIL,
        "email_verified": True,
        "iat": 1,
        "exp": 4102444800,
    }
    claims.update(overrides)
    return jwt.encode(claims, "not-the-real-idp-key-padded-to-32-bytes!", algorithm="HS256")


def _seed_user(role: str = "analyst") -> tuple:
    session = get_sessionmaker()()
    org = Organization(id=uuid4(), name="Auth Test Bank")
    session.add(org)
    session.flush()
    user = User(
        id=uuid4(),
        organization_id=org.id,
        email=_EMAIL,
        display_name="Chief Financial Officer",
        role=role,
        auth_provider="password",
        password_hash=hash_password(_PASSWORD),
    )
    session.add(user)
    session.commit()
    ids = (org.id, user.id)
    session.close()
    return ids


def test_password_login_then_me(db_client: TestClient) -> None:
    org_id, user_id = _seed_user(role="analyst")

    login = db_client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"

    me = db_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user_id"] == str(user_id)
    assert body["organization_id"] == str(org_id)
    assert body["email"] == _EMAIL
    assert body["role"] == "analyst"


def test_me_requires_a_valid_bearer_token(db_client: TestClient) -> None:
    assert db_client.get("/api/v1/auth/me").status_code == 401  # no token
    bad = db_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


def test_refresh_issues_new_tokens(db_client: TestClient) -> None:
    _seed_user()
    tokens = db_client.post(
        "/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    ).json()
    refreshed = db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]


def test_sso_linked_account_keeps_password_login(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: linking SSO must ADD a sign-in method, not revoke the password.

    (An SSO sign-in flips auth_provider to 'oidc'; password login used to require
    auth_provider == 'password' and locked the account's own fallback out.)
    """
    org_id, user_id = _seed_user(role="admin")
    _seed_sso_connection(org_id)
    _patch_verify(monkeypatch)
    # SSO sign-in links the account (auth_provider becomes 'oidc').
    assert db_client.post("/api/v1/auth/sso", json={"id_token": _id_token()}).status_code == 200

    # Password login still works afterwards.
    login = db_client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    me = db_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.json()["user_id"] == str(user_id)


def test_wrong_password_is_rejected_then_locks_out(db_client: TestClient) -> None:
    _seed_user()
    for _ in range(5):  # AUTH_MAX_FAILED_LOGINS default
        r = db_client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": "wrong"})
        assert r.status_code == 401
    # Further attempts — even with the CORRECT password — are locked out.
    locked = db_client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert locked.status_code == 423


def _patch_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the network JWKS verification; the token's own claims come back.
    Routing (iss/aud → connection) and every policy check still run for real."""
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda id_token, *, issuer, audience: jwt.decode(
            id_token, options={"verify_signature": False}
        ),
    )


def test_sso_login_links_oidc_identity_and_issues_app_tokens(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, user_id = _seed_user(role="viewer")  # pre-provisioned by email
    _seed_sso_connection(org_id)
    _patch_verify(monkeypatch)
    login = db_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]

    me = db_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == str(user_id)

    session = get_sessionmaker()()
    session.info["organization_id"] = org_id
    user = session.get(User, user_id)
    assert user.auth_provider == "oidc"
    assert user.sso_subject == "google-oauth2|abc123"
    session.close()


def test_sso_login_rejects_unprovisioned_identity(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, _ = _seed_user(role="viewer")
    _seed_sso_connection(org_id)
    _patch_verify(monkeypatch)
    r = db_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|x", email="stranger@nowhere.example")},
    )
    assert r.status_code == 401  # no AequorOS account provisioned for this identity


def test_sso_login_without_a_configured_connection_is_rejected(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_user(role="viewer")  # user exists, but no sso_connections row
    _patch_verify(monkeypatch)
    r = db_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert r.status_code == 401


def test_sso_login_enforces_allowed_email_domains(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, _ = _seed_user(role="viewer")
    _seed_sso_connection(org_id, allowed_email_domains=["otherbank.example"])
    _patch_verify(monkeypatch)
    r = db_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert r.status_code == 401
    assert "domain" in r.json()["error"]["message"].lower()


def test_sso_login_rejects_unverified_email(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, _ = _seed_user(role="viewer")
    _seed_sso_connection(org_id)
    _patch_verify(monkeypatch)
    r = db_client.post("/api/v1/auth/sso", json={"id_token": _id_token(email_verified=False)})
    assert r.status_code == 401


def test_jit_records_a_request_and_admin_approval_is_the_gate(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Org + connection + an admin exist; the signing-in employee has NO account.
    org_id, admin_id = _seed_user(role="admin")
    _seed_sso_connection(org_id, allowed_email_domains=["newbank.example"], jit_enabled=True)
    _patch_verify(monkeypatch)
    admin = headers(org_id=org_id, user_id=admin_id, roles=("admin",))
    token = _id_token(
        sub="google|new-employee", email="analyst@newbank.example", name="New Analyst"
    )

    # First sign-in: NO session — an access request is recorded instead.
    first = db_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert first.status_code == 403
    assert "administrator must approve" in first.json()["error"]["message"].lower()

    # Retrying doesn't get in either, and doesn't duplicate the request.
    again = db_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert again.status_code == 403
    pending = db_client.get("/api/v1/auth/sso/access-requests", headers=admin)
    assert pending.status_code == 200, pending.text
    assert [r["email"] for r in pending.json()] == ["analyst@newbank.example"]

    # Approval — with an explicitly chosen role — is what grants access.
    request_id = pending.json()[0]["user_id"]
    approved = db_client.post(
        f"/api/v1/auth/sso/access-requests/{request_id}/approve",
        json={"role": "analyst"},
        headers=admin,
    )
    assert approved.status_code == 200, approved.text

    login = db_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert login.status_code == 200, login.text
    me = db_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "analyst@newbank.example"
    assert body["role"] == "analyst"  # exactly what the admin granted
    assert body["organization_id"] == str(org_id)
    # The request queue is empty once approved.
    assert db_client.get("/api/v1/auth/sso/access-requests", headers=admin).json() == []


def test_rejected_access_request_is_deleted_and_can_reapply(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, admin_id = _seed_user(role="admin")
    _seed_sso_connection(org_id, allowed_email_domains=["newbank.example"], jit_enabled=True)
    _patch_verify(monkeypatch)
    admin = headers(org_id=org_id, user_id=admin_id, roles=("admin",))
    token = _id_token(sub="google|temp", email="temp@newbank.example")

    assert db_client.post("/api/v1/auth/sso", json={"id_token": token}).status_code == 403
    request_id = db_client.get("/api/v1/auth/sso/access-requests", headers=admin).json()[0][
        "user_id"
    ]
    rejected = db_client.post(
        f"/api/v1/auth/sso/access-requests/{request_id}/reject", headers=admin
    )
    assert rejected.status_code == 204
    assert db_client.get("/api/v1/auth/sso/access-requests", headers=admin).json() == []
    # Still no access; a fresh sign-in just records a new request.
    assert db_client.post("/api/v1/auth/sso", json={"id_token": token}).status_code == 403
    assert len(db_client.get("/api/v1/auth/sso/access-requests", headers=admin).json()) == 1


def test_jit_still_rejects_domains_outside_the_allow_list(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, _ = _seed_user(role="admin")
    _seed_sso_connection(org_id, allowed_email_domains=["newbank.example"], jit_enabled=True)
    _patch_verify(monkeypatch)
    r = db_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|drifter", email="drifter@gmail.example")},
    )
    assert r.status_code == 401


def test_jit_without_domain_list_never_creates_accounts(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hand-edited row (jit on, no domains) must fail closed at login time.
    org_id, _ = _seed_user(role="admin")
    _seed_sso_connection(org_id, allowed_email_domains=[], jit_enabled=True)
    _patch_verify(monkeypatch)
    r = db_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|anyone", email="anyone@anywhere.example")},
    )
    assert r.status_code == 401


def test_viewer_is_read_only_analyst_can_mutate(db_client: TestClient) -> None:
    # A viewer can read...
    assert db_client.get("/api/v1/banks", headers=headers(roles=("viewer",))).status_code == 200
    # ...but every mutation endpoint rejects them (403 — RBAC write gate).
    viewer_mutate = db_client.post("/api/v1/banks/seed-demo", headers=headers(roles=("viewer",)))
    assert viewer_mutate.status_code == 403
    # analyst (or higher) may mutate.
    assert (
        db_client.post("/api/v1/banks/seed-demo", headers=headers(roles=("analyst",))).status_code
        == 200
    )


def test_unknown_email_is_rejected_uniformly(db_client: TestClient) -> None:
    r = db_client.post(
        "/api/v1/auth/login", json={"email": "nobody@nowhere.example", "password": "x"}
    )
    assert r.status_code == 401
    assert "Invalid email or password" in r.text
