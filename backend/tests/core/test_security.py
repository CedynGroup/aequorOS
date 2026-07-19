"""Unit tests for the auth primitives: password hashing + app-JWT sign/verify."""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest

from app.core.config import AuthSettings
from app.core.security import (
    AuthConfigError,
    TokenInvalidError,
    create_token,
    decode_token,
    has_role,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.base import utc_now

def _auth(secret: str | None) -> AuthSettings:
    # `populate_by_name` lets us construct by field name, but some type-checkers
    # only see the alias (AUTH_JWT_SECRET) in the generated __init__ — silence
    # that false positive here rather than scatter ignores across call sites.
    return AuthSettings(jwt_secret=secret)  # pyright: ignore[reportCallIssue]


_SETTINGS = _auth("unit-test-signing-secret-please-rotate-000")


def _org_user() -> tuple:
    return uuid4(), uuid4()


# -- passwords ---------------------------------------------------------------
def test_password_hash_roundtrip_and_rejects_wrong() -> None:
    digest = hash_password("Correct-Horse-9!")
    assert digest != "Correct-Horse-9!"  # never stored in the clear
    assert verify_password("Correct-Horse-9!", digest) is True
    assert verify_password("wrong-password", digest) is False
    assert verify_password("anything", "not-a-valid-hash") is False  # never raises
    assert needs_rehash(digest) is False


# -- token roundtrip ---------------------------------------------------------
def test_access_token_roundtrips_with_identity_claims() -> None:
    org, user = _org_user()
    token = create_token(
        subject=user, organization_id=org, roles=["analyst"], token_type="access",
        email="a@bank.example", settings=_SETTINGS,
    )
    claims = decode_token(token, expected_type="access", settings=_SETTINGS)
    assert claims["sub"] == str(user)
    assert claims["org"] == str(org)
    assert claims["roles"] == ["analyst"]
    assert claims["type"] == "access"
    assert claims["email"] == "a@bank.example"


def test_refresh_token_type_is_enforced() -> None:
    org, user = _org_user()
    refresh = create_token(
        subject=user, organization_id=org, roles=["viewer"], token_type="refresh",
        settings=_SETTINGS,
    )
    assert decode_token(refresh, expected_type="refresh", settings=_SETTINGS)["type"] == "refresh"
    with pytest.raises(TokenInvalidError):
        decode_token(refresh, expected_type="access", settings=_SETTINGS)


# -- verification failures ---------------------------------------------------
def test_expired_token_is_rejected() -> None:
    org, user = _org_user()
    past = utc_now() - dt.timedelta(hours=1)  # exp = past + 15m < now → expired
    token = create_token(
        subject=user, organization_id=org, roles=["viewer"], token_type="access",
        now=past, settings=_SETTINGS,
    )
    with pytest.raises(TokenInvalidError):
        decode_token(token, settings=_SETTINGS)


def test_tampered_token_is_rejected() -> None:
    org, user = _org_user()
    token = create_token(
        subject=user, organization_id=org, roles=["admin"], token_type="access",
        settings=_SETTINGS,
    )
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}x.{signature}"
    with pytest.raises(TokenInvalidError):
        decode_token(tampered, settings=_SETTINGS)


def test_token_signed_with_another_secret_is_rejected() -> None:
    org, user = _org_user()
    other = _auth("a-different-secret-entirely-1234567890")
    token = create_token(
        subject=user, organization_id=org, roles=["viewer"], token_type="access",
        settings=other,
    )
    with pytest.raises(TokenInvalidError):
        decode_token(token, settings=_SETTINGS)


def test_unconfigured_secret_fails_closed() -> None:
    org, user = _org_user()
    unconfigured = _auth(None)
    with pytest.raises(AuthConfigError):
        create_token(
            subject=user, organization_id=org, roles=["viewer"], token_type="access",
            settings=unconfigured,
        )


# -- roles -------------------------------------------------------------------
def test_role_hierarchy() -> None:
    assert has_role(["admin"], "viewer") is True  # admin outranks everything
    assert has_role(["analyst"], "analyst") is True
    assert has_role(["analyst"], "approver") is False  # analyst cannot approve
    assert has_role(["viewer"], "analyst") is False
    assert has_role([], "viewer") is False
    assert has_role(["not-a-role"], "viewer") is False
