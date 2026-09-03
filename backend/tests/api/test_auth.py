"""E2E auth against the ACTUAL primary: password login → bearer → /auth/me,
lockout, refresh, SSO linking/JIT, and the RBAC write gate.

Invariants: credentials are verified (wrong password 401 → lockout 423), tokens
carry the real user's identity, profile edits round-trip and validate, SSO only
links PRE-PROVISIONED users (JIT records a request, admin approval is the gate),
viewer is read-only. The real admin's row is primed IN the rolled-back
transaction (a known password hash, a clean throttle) — nothing persists.
Opt-in via REAL_DATA_DATABASE_URL (tests/real_data.py).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.auth as auth_api
from app.core.security import hash_password
from app.models import SsoConnection, User
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_USER_EMAIL,
    REAL_USER_ID,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

_EMAIL = REAL_USER_EMAIL
_PASSWORD = "S3cure-Passphrase!"

_ISSUER = "https://accounts.google.com"
_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
_SSO_SUBJECT = "google-oauth2|abc123"


@pytest.fixture
def auth_client(
    real_client: TestClient,
    _real_bound_sessionmaker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """``real_client`` with the auth routes' *system* session bound to the same
    rolled-back connection.

    login / sso / refresh run on the cross-tenant system session (the BYPASSRLS
    worker role in production, ``get_worker_sessionmaker``). Under the app-role
    URL that would open a SECOND connection outside the test transaction — the
    login side-effects (throttle counters, last_login_at, SSO linking, JIT
    stubs) would COMMIT to the primary — and see no users at all under RLS.
    Bind it to the shared connection scoped to the real org instead: every auth
    rule still runs for real, and the outer rollback discards everything.
    """

    def _scoped_system_session() -> Session:
        return _real_bound_sessionmaker(info={"organization_id": REAL_ORG_ID})

    monkeypatch.setattr(auth_api, "get_worker_sessionmaker", lambda: _scoped_system_session)
    return real_client


@pytest.fixture
def prime_admin(real_session: Session) -> Callable[..., User]:
    """Give the real admin a KNOWN password and a clean throttle/profile, inside
    the rolled-back transaction (the primary's row is untouched)."""

    def _prime(role: str = "account_admin") -> User:
        real_session.info["organization_id"] = REAL_ORG_ID
        user = real_session.get(User, REAL_USER_ID)
        assert user is not None and user.is_active and user.email == _EMAIL
        user.role = role
        user.auth_provider = "password"
        user.sso_subject = None
        user.password_hash = hash_password(_PASSWORD)
        user.failed_login_attempts = 0
        user.locked_until = None
        user.display_name = "Chief Financial Officer"
        user.job_title = None
        user.locale = None
        user.timezone = None
        user.theme = None
        real_session.commit()  # savepoint release on the shared connection
        return user

    return _prime


def _configure_sso_connection(
    session: Session,
    *,
    allowed_email_domains: list[str] | None = None,
    jit_enabled: bool = False,
) -> None:
    """Replace the org's SSO connection (one per org) with the test IdP — in the
    rolled-back transaction, so the real connection row is restored on teardown."""
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(delete(SsoConnection).where(SsoConnection.organization_id == REAL_ORG_ID))
    session.add(
        SsoConnection(
            organization_id=REAL_ORG_ID,
            issuer=_ISSUER,
            client_id=_CLIENT_ID,
            client_secret_ciphertext="sealed-opaque",
            allowed_email_domains=allowed_email_domains or [],
            enabled=True,
            jit_enabled=jit_enabled,
        )
    )
    session.commit()


def _remove_sso_connection(session: Session) -> None:
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(delete(SsoConnection).where(SsoConnection.organization_id == REAL_ORG_ID))
    session.commit()


def _id_token(**overrides: object) -> str:
    """A structurally real (but unsigned-for-us) id_token; the verify step is
    monkeypatched, only the unverified iss/aud routing reads this payload."""
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": _SSO_SUBJECT,
        "email": _EMAIL,
        "email_verified": True,
        "iat": 1,
        "exp": 4102444800,
    }
    claims.update(overrides)
    return jwt.encode(claims, "not-the-real-idp-key-padded-to-32-bytes!", algorithm="HS256")


def _patch_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the network JWKS verification; the token's own claims come back.
    Routing (iss/aud → connection) and every policy check still run for real."""
    monkeypatch.setattr(
        "app.core.security.verify_oidc_id_token",
        lambda id_token, *, issuer, audience: jwt.decode(
            id_token, options={"verify_signature": False}
        ),
    )


def _login(client: TestClient, password: str = _PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": password})


def _access_requests(client: TestClient, email: str) -> list[dict[str, Any]]:
    response = client.get("/api/v1/auth/sso/access-requests", headers=real_headers())
    assert response.status_code == 200, response.text
    return [request for request in response.json() if request["email"] == email]


def test_password_login_then_me(auth_client: TestClient, prime_admin: Callable[..., User]) -> None:
    prime_admin(role="analyst")

    login = _login(auth_client)
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"

    me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user_id"] == str(REAL_USER_ID)
    assert body["organization_id"] == REAL_ORG_ID
    assert body["email"] == _EMAIL
    assert body["role"] == "analyst"  # the row's role, not a claim
    assert body["job_title"] is None
    assert body["locale"] is None
    assert body["timezone"] is None
    assert body["theme"] is None


def test_user_can_update_and_clear_own_profile(
    auth_client: TestClient, prime_admin: Callable[..., User]
) -> None:
    prime_admin(role="viewer")
    token = _login(auth_client).json()["access_token"]
    authorization = {"Authorization": f"Bearer {token}"}

    updated = auth_client.patch(
        "/api/v1/auth/me",
        headers=authorization,
        json={
            "display_name": "  Jane Mensah  ",
            "job_title": "  Treasury Analyst  ",
            "locale": "en-GH",
            "timezone": "Africa/Accra",
            "theme": "system",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "user_id": str(REAL_USER_ID),
        "organization_id": REAL_ORG_ID,
        "email": _EMAIL,
        "display_name": "Jane Mensah",
        "job_title": "Treasury Analyst",
        "locale": "en-GH",
        "timezone": "Africa/Accra",
        "theme": "system",
        "role": "viewer",
        # Exposed so the signing ceremony can offer the right step-up proof.
        "auth_provider": "password",
    }
    assert auth_client.get("/api/v1/auth/me", headers=authorization).json() == updated.json()

    cleared = auth_client.patch(
        "/api/v1/auth/me",
        headers=authorization,
        json={"display_name": "", "job_title": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["display_name"] is None
    assert cleared.json()["job_title"] is None
    assert cleared.json()["locale"] == "en-GH"


@pytest.mark.parametrize(
    "payload",
    [
        {"timezone": "Not/A_Real_Zone"},
        {"locale": "definitely_not_a_locale"},
        {"theme": "sepia"},
        {"email": "new-address@example.com"},
        {"role": "admin"},
    ],
)
def test_profile_update_rejects_invalid_or_forbidden_fields(
    auth_client: TestClient, prime_admin: Callable[..., User], payload: dict[str, str]
) -> None:
    prime_admin(role="viewer")
    token = _login(auth_client).json()["access_token"]
    response = auth_client.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 422


def test_me_requires_a_valid_bearer_token(real_client: TestClient) -> None:
    assert real_client.get("/api/v1/auth/me").status_code == 401  # no token
    bad = real_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


def test_refresh_issues_new_tokens(
    auth_client: TestClient, prime_admin: Callable[..., User]
) -> None:
    prime_admin()
    tokens = _login(auth_client).json()
    refreshed = auth_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]


def test_sso_linked_account_keeps_password_login(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: linking SSO must ADD a sign-in method, not revoke the password.

    (An SSO sign-in flips auth_provider to 'oidc'; password login used to require
    auth_provider == 'password' and locked the account's own fallback out.)
    """
    prime_admin(role="account_admin")
    _configure_sso_connection(real_session)
    _patch_verify(monkeypatch)
    # SSO sign-in links the account (auth_provider becomes 'oidc').
    assert auth_client.post("/api/v1/auth/sso", json={"id_token": _id_token()}).status_code == 200

    # Password login still works afterwards.
    login = _login(auth_client)
    assert login.status_code == 200, login.text
    me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.json()["user_id"] == str(REAL_USER_ID)
    assert me.json()["auth_provider"] == "oidc"


def test_wrong_password_is_rejected_then_locks_out(
    auth_client: TestClient, prime_admin: Callable[..., User]
) -> None:
    prime_admin()
    for _ in range(5):  # AUTH_MAX_FAILED_LOGINS default
        r = _login(auth_client, password="wrong")
        assert r.status_code == 401
    # Further attempts — even with the CORRECT password — are locked out.
    locked = _login(auth_client)
    assert locked.status_code == 423


def test_sso_login_links_oidc_identity_and_issues_app_tokens(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="viewer")  # pre-provisioned by email
    _configure_sso_connection(real_session)
    _patch_verify(monkeypatch)
    login = auth_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]

    me = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == str(REAL_USER_ID)

    real_session.expire_all()
    user = real_session.get(User, REAL_USER_ID)
    assert user is not None
    assert user.auth_provider == "oidc"
    assert user.sso_subject == _SSO_SUBJECT


def test_sso_login_rejects_unprovisioned_identity(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="viewer")
    _configure_sso_connection(real_session)
    _patch_verify(monkeypatch)
    r = auth_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|x", email="stranger@nowhere.example")},
    )
    assert r.status_code == 401  # no AequorOS account provisioned for this identity


def test_sso_login_without_a_configured_connection_is_rejected(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="viewer")  # user exists, but no sso_connections row
    _remove_sso_connection(real_session)
    _patch_verify(monkeypatch)
    r = auth_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert r.status_code == 401


def test_sso_login_enforces_allowed_email_domains(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="viewer")
    _configure_sso_connection(real_session, allowed_email_domains=["otherbank.example"])
    _patch_verify(monkeypatch)
    r = auth_client.post("/api/v1/auth/sso", json={"id_token": _id_token()})
    assert r.status_code == 401
    assert "domain" in r.json()["error"]["message"].lower()


def test_sso_login_rejects_unverified_email(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="viewer")
    _configure_sso_connection(real_session)
    _patch_verify(monkeypatch)
    r = auth_client.post("/api/v1/auth/sso", json={"id_token": _id_token(email_verified=False)})
    assert r.status_code == 401


def test_jit_records_a_request_and_admin_approval_is_the_gate(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Org + connection + the real admin exist; the signing-in employee has NO account.
    prime_admin(role="account_admin")
    _configure_sso_connection(
        real_session, allowed_email_domains=["newbank.example"], jit_enabled=True
    )
    _patch_verify(monkeypatch)
    email = f"analyst-{uuid4().hex[:8]}@newbank.example"
    token = _id_token(sub=f"google|{uuid4().hex}", email=email, name="New Analyst")

    # First sign-in: NO session — an access request is recorded instead.
    first = auth_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert first.status_code == 403
    assert "administrator must approve" in first.json()["error"]["message"].lower()

    # Retrying doesn't get in either, and doesn't duplicate the request.
    again = auth_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert again.status_code == 403
    pending = _access_requests(auth_client, email)
    assert len(pending) == 1

    # Approval creates one complete scoped binding; verified identity alone had
    # no access and the scalar role remains compatibility-only Viewer state.
    request_id = pending[0]["user_id"]
    approval_payload = {
        "role_bundle": "analyst",
        "institution_scope": "institution",
        "institution_id": REAL_BANK_ID,
        "module_scope": "liq",
        "sensitivity_scope": "confidential",
        "reason": "Verified employee approved for liquidity analysis",
    }
    preview = auth_client.post(
        "/api/v1/authorization/bindings/preview",
        json={**approval_payload, "principal_user_id": request_id},
        headers=real_headers(),
    )
    assert preview.status_code == 200, preview.text
    approval_payload["expected_authority_sentence"] = preview.json()["authority_sentence"]
    approved = auth_client.post(
        f"/api/v1/auth/sso/access-requests/{request_id}/approve",
        json=approval_payload,
        headers=real_headers(),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["binding"]["role_bundle"] == "analyst"
    assert approved.json()["binding"]["module_scope"] == "liq"
    assert approved.json()["binding"]["sensitivity_scope"] == "confidential"

    login = auth_client.post("/api/v1/auth/sso", json={"id_token": token})
    assert login.status_code == 200, login.text
    me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == email
    assert body["role"] == "viewer"  # scalar compatibility state is not the scoped grant
    assert body["organization_id"] == REAL_ORG_ID
    # The request is gone from the queue once approved.
    assert _access_requests(auth_client, email) == []


# Rejection is a recorded STATE on the kept (deactivated) stub — users are
# never physically deleted (signer identities reference them and the
# append-only privilege tiering makes a DELETE fail on the primary; fixed
# 2026-08-16: users.access_rejected_at, migration 202608160016). A fresh
# sign-in by the same email clears the rejection and re-opens the request.
def test_rejected_access_request_is_recorded_not_deleted_and_can_reapply(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="account_admin")
    _configure_sso_connection(
        real_session, allowed_email_domains=["newbank.example"], jit_enabled=True
    )
    _patch_verify(monkeypatch)
    email = f"temp-{uuid4().hex[:8]}@newbank.example"
    token = _id_token(sub=f"google|{uuid4().hex}", email=email)

    assert auth_client.post("/api/v1/auth/sso", json={"id_token": token}).status_code == 403
    request_id = _access_requests(auth_client, email)[0]["user_id"]
    rejected = auth_client.post(
        f"/api/v1/auth/sso/access-requests/{request_id}/reject", headers=real_headers()
    )
    assert rejected.status_code == 204
    assert _access_requests(auth_client, email) == []
    # Still no access; a fresh sign-in just records a new request.
    assert auth_client.post("/api/v1/auth/sso", json={"id_token": token}).status_code == 403
    assert len(_access_requests(auth_client, email)) == 1


def test_jit_still_rejects_domains_outside_the_allow_list(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prime_admin(role="account_admin")
    _configure_sso_connection(
        real_session, allowed_email_domains=["newbank.example"], jit_enabled=True
    )
    _patch_verify(monkeypatch)
    r = auth_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|drifter", email="drifter@gmail.example")},
    )
    assert r.status_code == 401


def test_jit_without_domain_list_never_creates_accounts(
    auth_client: TestClient,
    real_session: Session,
    prime_admin: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hand-edited row (jit on, no domains) must fail closed at login time.
    prime_admin(role="account_admin")
    _configure_sso_connection(real_session, allowed_email_domains=[], jit_enabled=True)
    _patch_verify(monkeypatch)
    email = "anyone@anywhere.example"
    r = auth_client.post(
        "/api/v1/auth/sso",
        json={"id_token": _id_token(sub="google|anyone", email=email)},
    )
    assert r.status_code == 401
    real_session.info["organization_id"] = REAL_ORG_ID
    assert real_session.scalar(select(User.id).where(User.email == email)) is None


def test_viewer_is_read_only_analyst_can_mutate(real_client: TestClient) -> None:
    """RBAC ladder is enforced from the token's role claims (the real admin's
    identity, narrowed per request)."""
    # A viewer can read...
    assert (
        real_client.get("/api/v1/banks", headers=real_headers(roles=("viewer",))).status_code == 200
    )
    # ...but every mutation endpoint rejects them (403 — RBAC write gate).
    payload = {
        "as_of_date": "2026-06-30",
        "idempotency_key": f"auth-rbac-{uuid4().hex}",
        "reason": "RBAC write-gate check",
    }
    viewer_mutate = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/push-batches",
        headers=real_headers(roles=("viewer",)),
        json=payload,
    )
    assert viewer_mutate.status_code == 403
    # analyst (or higher) may mutate.
    analyst_mutate = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/push-batches",
        headers=real_headers(roles=("analyst",)),
        json=payload,
    )
    assert analyst_mutate.status_code == 201, analyst_mutate.text


def test_unknown_email_is_rejected_uniformly(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/v1/auth/login", json={"email": "nobody@nowhere.example", "password": "x"}
    )
    assert r.status_code == 401
    assert "Invalid email or password" in r.text
