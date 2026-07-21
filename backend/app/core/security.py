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


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    from jwt import PyJWKClient  # noqa: PLC0415 - lazy; only the SSO path needs it

    return PyJWKClient(jwks_url)  # caches fetched signing keys internally


@lru_cache(maxsize=8)
def _discover_jwks_uri(issuer: str) -> str:
    """Resolve an issuer's JWKS URL via OIDC discovery.

    The discovery document's location is fixed by the OIDC spec, so this works
    for any compliant IdP (Google, Entra, Okta, Keycloak, …) without vendor
    branches. Cached per issuer — the jwks_uri itself effectively never changes;
    key *rotation* is handled inside PyJWKClient.
    """
    import json  # noqa: PLC0415 - lazy; only the SSO path needs these
    import urllib.request  # noqa: PLC0415

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    if not url.startswith("https://"):
        raise TokenInvalidError(f"OIDC issuer must be https, got {issuer!r}.")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - https enforced above
            document = json.load(response)
    except Exception as exc:
        raise TokenInvalidError(f"OIDC discovery failed for {issuer!r}: {exc}") from exc
    jwks_uri = document.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
        raise TokenInvalidError(f"OIDC discovery for {issuer!r} returned no usable jwks_uri.")
    return jwks_uri


def unverified_claims(id_token: str) -> dict[str, Any]:
    """Decode an id_token's payload WITHOUT verification.

    Only for routing — picking which configured SSO connection (issuer/audience)
    to verify against. Never trust these claims for identity; a forged `iss` can
    only select a configured connection, whose JWKS the token must then survive.
    """
    try:
        return jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(f"Malformed id_token: {exc}") from exc


def verify_oidc_id_token(id_token: str, *, issuer: str, audience: str) -> dict[str, Any]:
    """Verify an OIDC id_token against its issuer's JWKS, return its claims.

    Zero-trust SSO: the backend independently checks the signature (RS256/ES256
    via the issuer's published keys), issuer, audience, and expiry — it never
    trusts that the dashboard already validated the token. ``issuer``/``audience``
    come from the stored SSO connection, not from the token.
    """
    try:
        signing_key = _jwks_client(_discover_jwks_uri(issuer)).get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(f"OIDC id_token verification failed: {exc}") from exc
