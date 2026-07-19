"""E2E: password login → bearer token → /auth/me, with lockout + refresh."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import Organization, User
from tests.api.helpers import headers

_EMAIL = "cfo@testbank.example"
_PASSWORD = "S3cure-Passphrase!"


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

    login = db_client.post(
        "/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    )
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


def test_wrong_password_is_rejected_then_locks_out(db_client: TestClient) -> None:
    _seed_user()
    for _ in range(5):  # AUTH_MAX_FAILED_LOGINS default
        r = db_client.post(
            "/api/v1/auth/login", json={"email": _EMAIL, "password": "wrong"}
        )
        assert r.status_code == 401
    # Further attempts — even with the CORRECT password — are locked out.
    locked = db_client.post(
        "/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    )
    assert locked.status_code == 423


def test_sso_login_links_auth0_identity_and_issues_app_tokens(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, user_id = _seed_user(role="viewer")  # pre-provisioned by email
    monkeypatch.setattr(
        "app.core.security.verify_auth0_id_token",
        lambda _id_token, settings=None: {"sub": "auth0|abc123", "email": _EMAIL},
    )
    login = db_client.post("/api/v1/auth/sso", json={"id_token": "auth0-opaque-token"})
    assert login.status_code == 200, login.text
    access = login.json()["access_token"]

    me = db_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == str(user_id)

    session = get_sessionmaker()()
    session.info["organization_id"] = org_id
    user = session.get(User, user_id)
    assert user.auth_provider == "auth0"
    assert user.sso_subject == "auth0|abc123"
    session.close()


def test_sso_login_rejects_unprovisioned_identity(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.core.security.verify_auth0_id_token",
        lambda _id_token, settings=None: {"sub": "auth0|x", "email": "stranger@nowhere.example"},
    )
    r = db_client.post("/api/v1/auth/sso", json={"id_token": "opaque"})
    assert r.status_code == 401  # no AequorOS account provisioned for this identity


def test_viewer_is_read_only_analyst_can_mutate(db_client: TestClient) -> None:
    # A viewer can read...
    assert db_client.get("/api/v1/banks", headers=headers(roles=("viewer",))).status_code == 200
    # ...but every mutation endpoint rejects them (403 — RBAC write gate).
    viewer_mutate = db_client.post(
        "/api/v1/banks/seed-demo", headers=headers(roles=("viewer",))
    )
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
