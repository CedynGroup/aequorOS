"""Auth primitives: Argon2id password hashing and app-JWT sign/verify.

The backend is both the issuer and the verifier of app tokens (HS256 over
``AuthSettings.jwt_secret``), so every API request is authenticated by verifying a
signed token — never by trusting a header. A token carries the tenant (``org``),
the user (``sub``), and ``roles``; the API layer derives ``TenantContext`` and
enforces RBAC from the *verified* claims.

If ``AUTH_JWT_SECRET`` is unset, :func:`create_token` / :func:`decode_token` raise
``AuthConfigError`` rather than degrade — the demo header-trust path can never
silently return.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from app.core.config import AuthSettings, get_settings
from app.db.base import utc_now

if TYPE_CHECKING:
    from jwt import PyJWKClient

# Roles, most- to least-privileged. `admin` manages users/config; `approver` is the
# maker-checker second signer; `analyst` runs calculations + mutations; `viewer` reads.
ROLES: tuple[str, ...] = ("admin", "approver", "analyst", "viewer")
_ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}

TokenType = Literal["access", "refresh"]

_hasher = PasswordHasher()  # Argon2id with sane defaults


class AuthError(Exception):
    """Base class for auth failures."""


class AuthConfigError(AuthError):
    """The auth system is not configured (no signing secret) — fail closed."""


class TokenInvalidError(AuthError):
    """A token failed signature / expiry / claim verification."""


# -- passwords ---------------------------------------------------------------
def hash_password(password: str) -> str:
    """Argon2id hash of ``password`` (includes a per-hash random salt)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True iff ``password`` matches ``password_hash``; never raises."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash used weaker params than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHash:
        return True


# -- roles -------------------------------------------------------------------
def has_role(user_roles: list[str], required: str) -> bool:
    """True if any of ``user_roles`` is at least as privileged as ``required``."""
    threshold = _ROLE_RANK.get(required)
    if threshold is None:
        return False
    return any(_ROLE_RANK.get(role, len(ROLES)) <= threshold for role in user_roles)


# -- tokens ------------------------------------------------------------------
def _secret(settings: AuthSettings) -> str:
    if not settings.jwt_secret:
        msg = "AUTH_JWT_SECRET is not set; refusing to issue or verify tokens."
        raise AuthConfigError(msg)
    return settings.jwt_secret


def create_token(  # noqa: PLR0913 - a token carries the full identity envelope
    *,
    subject: UUID,
    organization_id: UUID,
    roles: list[str],
    token_type: TokenType,
    email: str | None = None,
    name: str | None = None,
    now: dt.datetime | None = None,
    settings: AuthSettings | None = None,
) -> str:
    """Sign an app access/refresh token for (org, user, roles)."""
    settings = settings or get_settings().auth
    moment = now or utc_now()
    ttl = (
        settings.access_token_ttl_seconds
        if token_type == "access"
        else settings.refresh_token_ttl_seconds
    )
    payload: dict[str, Any] = {
        "sub": str(subject),
        "org": str(organization_id),
        "roles": list(roles),
        "type": token_type,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(moment.timestamp()),
        "exp": int((moment + dt.timedelta(seconds=ttl)).timestamp()),
    }
    if email is not None:
        payload["email"] = email
    if name is not None:
        payload["name"] = name
    return jwt.encode(payload, _secret(settings), algorithm=settings.jwt_algorithm)


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
    settings: AuthSettings | None = None,
) -> dict[str, Any]:
    """Verify signature, expiry, issuer, audience, and required claims; return them."""
    settings = settings or get_settings().auth
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            _secret(settings),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "org", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(str(exc)) from exc
    if expected_type is not None and claims.get("type") != expected_type:
        msg = f"expected a {expected_type} token, got {claims.get('type')!r}"
        raise TokenInvalidError(msg)
    return claims


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    from jwt import PyJWKClient  # noqa: PLC0415 - lazy; only the SSO path needs it

    return PyJWKClient(jwks_url)  # caches fetched signing keys internally


def verify_auth0_id_token(
    id_token: str, *, settings: AuthSettings | None = None
) -> dict[str, Any]:
    """Verify an Auth0 OIDC id_token against Auth0's JWKS (RS256), return its claims.

    Zero-trust SSO: the backend independently checks the signature, issuer
    (``https://{domain}/``), and audience (the client id) — it never trusts that
    the dashboard already validated the token.
    """
    settings = settings or get_settings().auth
    if not settings.auth0_domain or not settings.auth0_client_id:
        raise AuthConfigError("Auth0 SSO is not configured (AUTH0_DOMAIN/CLIENT_ID unset).")
    issuer = f"https://{settings.auth0_domain}/"
    try:
        signing_key = _jwks_client(f"{issuer}.well-known/jwks.json").get_signing_key_from_jwt(
            id_token
        )
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_client_id,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(f"Auth0 id_token verification failed: {exc}") from exc
