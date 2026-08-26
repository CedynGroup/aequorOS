"""Refresh-token lifecycle: rotation, reuse detection, revocation (P0-5).

Before migration ``202608220028`` a refresh token was a bare 14-day JWT: no
``jti``, no server-side state, no way to end a session short of deactivating the
account. These tests pin the replacement — one refresh per token, a new token on
every refresh, a revoked family the moment a retired token is replayed, and an
outright revoke on logout / password change / deactivation.

Everything here runs hermetically (``db_client``): login, refresh and logout use
the cross-tenant system session, which falls back to ``DATABASE_URL`` when
``WORKER_DATABASE_URL`` is blank — exactly the suite's configuration.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import RefreshToken, User
from app.services import authentication, authorization
from tests.api.helpers import ORG_1, USER_1

_EMAIL = "demo.user.one@example.test"  # conftest._seed_demo_tenants
_PASSWORD = "S3cure-Passphrase!"


@contextmanager
def _session() -> Iterator[Session]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def password_user(db_client: TestClient) -> TestClient:
    """Give the seeded tenant user a known password so ``/auth/login`` works."""
    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        user.password_hash = security.hash_password(_PASSWORD)
        user.auth_provider = "password"
        user.failed_login_attempts = 0
        user.locked_until = None
        session.commit()
    return db_client


def _set_grace(monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    """Pin the concurrent-refresh grace window for one test."""
    monkeypatch.setenv("AUTH_REFRESH_ROTATION_GRACE", str(seconds))
    get_settings.cache_clear()


def _login(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def _refresh(client: TestClient, token: str):  # noqa: ANN202 - httpx.Response
    return client.post("/api/v1/auth/refresh", json={"refresh_token": token})


def _rows() -> list[RefreshToken]:
    with _session() as session:
        return list(
            session.scalars(
                select(RefreshToken)
                .where(RefreshToken.user_id == USER_1)
                .order_by(RefreshToken.issued_at)
            )
        )


def _jti(token: str) -> UUID:
    return UUID(str(jwt.decode(token, options={"verify_signature": False})["jti"]))


# -- the happy path ----------------------------------------------------------
def test_login_access_refresh_and_authorized_call_round_trip(
    password_user: TestClient,
) -> None:
    """Regression: the whole flow still works end to end — login, call an
    authorized endpoint, refresh, call it again with the new access token."""
    tokens = _login(password_user)

    first = password_user.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["email"] == _EMAIL

    refreshed = _refresh(password_user, tokens["refresh_token"])
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()

    second = password_user.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {rotated['access_token']}"}
    )
    assert second.status_code == 200, second.text
    assert second.json()["email"] == _EMAIL


def test_login_records_refresh_token_state_as_a_hash(password_user: TestClient) -> None:
    tokens = _login(password_user)
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == _jti(tokens["refresh_token"])
    assert row.family_id == row.id  # a login starts its own family
    assert row.rotated_at is None and row.revoked_at is None
    # The raw token is never persisted — only its digest.
    assert row.token_hash == authentication._hash_refresh_token(tokens["refresh_token"])
    assert tokens["refresh_token"] not in row.token_hash


def test_access_token_carries_authorization_version_but_no_refresh_jti(
    password_user: TestClient,
) -> None:
    """Access tokens use the user version while refresh state remains jti-keyed."""
    tokens = _login(password_user)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    assert "jti" not in claims
    assert claims["authv"] == 1
    assert claims["type"] == "access"
    response = password_user.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200


# -- rotation ----------------------------------------------------------------
def test_refresh_rotates_to_a_new_refresh_token(password_user: TestClient) -> None:
    tokens = _login(password_user)
    rotated = _refresh(password_user, tokens["refresh_token"]).json()

    assert rotated["refresh_token"] != tokens["refresh_token"]
    rows = {row.id: row for row in _rows()}
    assert len(rows) == 2
    old, new = rows[_jti(tokens["refresh_token"])], rows[_jti(rotated["refresh_token"])]
    assert old.rotated_at is not None
    assert old.replaced_by_id == new.id
    assert new.family_id == old.family_id  # same session lineage
    assert new.rotated_at is None and new.revoked_at is None


def test_a_refresh_token_works_exactly_once(
    password_user: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_grace(monkeypatch, 0)
    tokens = _login(password_user)

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 200
    replay = _refresh(password_user, tokens["refresh_token"])
    assert replay.status_code == 401
    # One generic message for every refresh failure — never "revoked for reuse".
    assert replay.json()["error"]["message"] == "Invalid refresh token."


def test_reusing_a_rotated_token_revokes_the_whole_family(
    password_user: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The security event: a retired token replayed outside the grace window
    kills the lineage — the thief's copy AND the live successor."""
    _set_grace(monkeypatch, 0)
    tokens = _login(password_user)
    rotated = _refresh(password_user, tokens["refresh_token"]).json()

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401

    rows = _rows()
    assert len(rows) == 2
    assert all(row.revoked_at is not None for row in rows)
    assert {row.revoked_reason for row in rows} == {"reuse_detected"}
    # The successor the real user is holding is dead too — that is the point.
    assert _refresh(password_user, rotated["refresh_token"]).status_code == 401


def test_a_stolen_rotated_token_cannot_mint_a_new_session(
    password_user: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token copied off the wire and replayed after the legitimate client has
    rotated mints nothing: no tokens in the response, no new rows."""
    _set_grace(monkeypatch, 0)
    tokens = _login(password_user)
    stolen = tokens["refresh_token"]
    _refresh(password_user, stolen)  # the real client rotates

    before = len(_rows())
    attack = _refresh(password_user, stolen)

    assert attack.status_code == 401
    assert "access_token" not in attack.json()
    assert len(_rows()) == before


def test_concurrent_refresh_inside_the_grace_window_is_not_reuse(
    password_user: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented concurrency semantics: refreshes of the same token serialize on
    a row lock, and a loser inside the grace window is a retry, not theft — it
    gets its own sibling token and the family survives."""
    _set_grace(monkeypatch, 30)
    tokens = _login(password_user)
    original = tokens["refresh_token"]

    first = _refresh(password_user, original)
    second = _refresh(password_user, original)

    assert first.status_code == 200
    assert second.status_code == 200, second.text
    assert first.json()["refresh_token"] != second.json()["refresh_token"]

    rows = {row.id: row for row in _rows()}
    assert len(rows) == 3  # the original plus two siblings
    assert all(row.revoked_at is None for row in rows.values())
    assert len({row.family_id for row in rows.values()}) == 1
    # Both siblings work; the spent original does not extend its own window.
    assert _refresh(password_user, first.json()["refresh_token"]).status_code == 200
    assert _refresh(password_user, second.json()["refresh_token"]).status_code == 200


def test_the_grace_window_is_measured_from_the_first_rotation(
    password_user: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rotated_at`` is stamped once. Back-dating it past the window turns the
    next replay into reuse, so a spent token cannot be nursed along forever."""
    _set_grace(monkeypatch, 30)
    tokens = _login(password_user)
    original = tokens["refresh_token"]
    assert _refresh(password_user, original).status_code == 200

    with _session() as session:
        row = session.get(RefreshToken, _jti(original))
        assert row is not None
        row.rotated_at = utc_now() - dt.timedelta(seconds=120)
        session.commit()

    assert _refresh(password_user, original).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"reuse_detected"}


# -- revocation --------------------------------------------------------------
def test_logout_revokes_refresh_capability(password_user: TestClient) -> None:
    tokens = _login(password_user)

    response = password_user.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 204

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"logout"}


def test_logout_revokes_the_whole_lineage_not_just_the_presented_token(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)
    rotated = _refresh(password_user, tokens["refresh_token"]).json()

    assert (
        password_user.post(
            "/api/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]}
        ).status_code
        == 204
    )
    rows = _rows()
    assert len(rows) == 2
    assert all(row.revoked_at is not None for row in rows)


def test_logout_with_an_unknown_token_is_a_silent_no_op(
    password_user: TestClient,
) -> None:
    """Never an oracle: a junk token answers 204 exactly like a real one, and the
    live session is untouched."""
    tokens = _login(password_user)
    response = password_user.post("/api/v1/auth/logout", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 204
    assert _refresh(password_user, tokens["refresh_token"]).status_code == 200


def test_password_change_invalidates_existing_refresh_tokens(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)

    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        authentication.set_password(session, user, "An-Entirely-New-Passphrase!1")

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"password_change"}


def test_account_deactivation_invalidates_refresh_tokens(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)

    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        authentication.deactivate_user(session, user)

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"user_deactivated"}


def test_a_bare_is_active_flip_still_blocks_refresh_and_revokes_the_family(
    password_user: TestClient,
) -> None:
    """Defence in depth: even when a caller flips the flag without going through
    ``deactivate_user``, the refresh path refuses and cleans up the lineage."""
    tokens = _login(password_user)

    with _session() as session:
        user = session.get(User, USER_1)
        assert user is not None
        user.is_active = False
        session.commit()

    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"user_deactivated"}


def test_authorization_change_rejects_stale_access_and_revokes_refresh_family(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)

    with _session() as session:
        assert (
            authorization.invalidate_user_authorization(
                session,
                organization_id=ORG_1,
                user_id=USER_1,
                reason="test role/scope change",
            )
            == 2
        )

    stale_access = password_user.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert stale_access.status_code == 401
    assert stale_access.json()["error"]["message"] == (
        "Session authorization is stale. Sign in again."
    )
    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401
    assert {row.revoked_reason for row in _rows()} == {"authorization_changed"}

    # Existing membership remains usable: re-authentication reads the current
    # version and issues a fresh family at that version.
    replacement = _login(password_user)
    replacement_claims = jwt.decode(
        replacement["access_token"], options={"verify_signature": False}
    )
    assert replacement_claims["authv"] == 2
    assert password_user.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {replacement['access_token']}"},
    ).status_code == 200


# -- malformed / expired / pre-migration -------------------------------------
def test_an_expired_refresh_token_is_rejected(password_user: TestClient) -> None:
    tokens = _login(password_user)
    settings = get_settings().auth
    expired = security.create_token(
        subject=USER_1,
        organization_id=ORG_1,
        roles=["admin"],
        authorization_version=1,
        token_type="refresh",
        jti=str(_jti(tokens["refresh_token"])),  # a real, live server-side row
        now=utc_now() - dt.timedelta(seconds=settings.refresh_token_ttl_seconds + 60),
        settings=settings,
    )
    assert _refresh(password_user, expired).status_code == 401
    # The live row is untouched — an expired token is not a reuse event.
    assert _refresh(password_user, tokens["refresh_token"]).status_code == 200


def test_a_server_side_expiry_is_enforced_even_for_an_unexpired_jwt(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)
    with _session() as session:
        row = session.get(RefreshToken, _jti(tokens["refresh_token"]))
        assert row is not None
        row.expires_at = utc_now() - dt.timedelta(seconds=1)
        session.commit()
    assert _refresh(password_user, tokens["refresh_token"]).status_code == 401


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "a.b.c", "Bearer something"],
    ids=["empty", "garbage", "three-segments", "prefixed"],
)
def test_a_malformed_refresh_token_is_rejected(password_user: TestClient, token: str) -> None:
    response = _refresh(password_user, token)
    # "" fails schema validation (min_length=1); the rest fail verification.
    assert response.status_code in (401, 422)


def test_a_refresh_token_signed_with_a_foreign_secret_is_rejected(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)
    settings = get_settings().auth
    forged = jwt.encode(
        jwt.decode(tokens["refresh_token"], options={"verify_signature": False}),
        "an-attacker-controlled-secret",
        algorithm=settings.jwt_algorithm,
    )
    assert _refresh(password_user, forged).status_code == 401


def test_an_access_token_is_refused_on_the_refresh_endpoint(
    password_user: TestClient,
) -> None:
    tokens = _login(password_user)
    assert _refresh(password_user, tokens["access_token"]).status_code == 401


def test_a_pre_migration_refresh_token_without_a_jti_is_refused(
    password_user: TestClient,
) -> None:
    """Fail closed, and deliberately user-visible: tokens issued before
    ``202608220028`` carry no ``jti``, so they have no revocation state and are
    refused. Outstanding sessions re-authenticate once."""
    settings = get_settings().auth
    secret = settings.jwt_secret
    assert secret is not None  # conftest pins AUTH_JWT_SECRET
    now = utc_now()
    legacy = jwt.encode(
        {
            "sub": str(USER_1),
            "org": ORG_1,
            "roles": ["admin"],
            "authv": 1,
            "type": "refresh",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + dt.timedelta(days=14)).timestamp()),
        },
        secret,
        algorithm=settings.jwt_algorithm,
    )
    # Signature, issuer, audience and expiry all verify — only the missing jti
    # stops it.
    assert jwt.decode(
        legacy,
        secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )["sub"] == str(USER_1)
    assert _refresh(password_user, legacy).status_code == 401


def test_pre_authorization_version_access_and_refresh_tokens_fail_closed(
    password_user: TestClient,
) -> None:
    """The deployment transition is a deliberate one-time re-authentication.

    Tokens from before migration 202608250044 have valid signatures and may even
    carry a refresh jti, but they have no authoritative generation to compare.
    Neither token type is accepted and their old role claims grant nothing.
    """

    settings = get_settings().auth
    secret = settings.jwt_secret
    assert secret is not None
    now = utc_now()

    def legacy(token_type: str) -> str:
        claims: dict[str, object] = {
            "sub": str(USER_1),
            "org": ORG_1,
            "roles": ["admin"],
            "type": token_type,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + dt.timedelta(hours=1)).timestamp()),
        }
        if token_type == "refresh":
            claims["jti"] = str(UUID("11111111-1111-4111-8111-111111111111"))
        return jwt.encode(claims, secret, algorithm=settings.jwt_algorithm)

    access = password_user.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {legacy('access')}"},
    )
    refresh = _refresh(password_user, legacy("refresh"))

    assert access.status_code == 401
    assert refresh.status_code == 401


def test_a_jti_with_the_wrong_token_bytes_is_refused(password_user: TestClient) -> None:
    """The stored digest binds the row to the exact token: knowing a ``jti`` (it
    is not secret — it rides in a token the client already holds) buys nothing."""
    tokens = _login(password_user)
    settings = get_settings().auth
    forged = security.create_token(
        subject=USER_1,
        organization_id=ORG_1,
        roles=["admin"],
        authorization_version=1,
        token_type="refresh",
        jti=str(_jti(tokens["refresh_token"])),
        email="someone.else@example.test",  # different bytes, same jti
        settings=settings,
    )
    assert forged != tokens["refresh_token"]
    assert _refresh(password_user, forged).status_code == 401


def test_create_token_refuses_to_mint_a_refresh_token_without_a_jti() -> None:
    with pytest.raises(ValueError, match="must carry a jti"):
        security.create_token(
            subject=USER_1,
            organization_id=ORG_1,
            roles=["admin"],
            authorization_version=1,
            token_type="refresh",
            settings=get_settings().auth,
        )
