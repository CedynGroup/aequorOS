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
# maker-checker second signer; `analyst` runs calculations + mutations; `examiner`
# is the supervisory read-only role (Phase 2 item 7: reads everything incl. the
# examiner surfaces, mutates nothing — every mutation gate sits at analyst or
# above); `viewer` reads the standard surfaces only.
ROLES: tuple[str, ...] = ("admin", "approver", "analyst", "examiner", "viewer")
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
    organization_id: str,
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


# -- impersonation (act-as-examiner) -----------------------------------------
# A cross-app token minted by the operator control plane and accepted by the
# tenant API to open a READ-ONLY, session-bound examiner view into ONE tenant.
# Signed with a DEDICATED secret (``AuthSettings.impersonation_jwt_secret``) so
# it is worthless on the normal access-token path and a normal access token can
# never masquerade as one. The tenant API pins the role to ``examiner``
# regardless of the claim; the token merely carries provenance (which operator,
# which inspector session, which tenant).
IMPERSONATION_TOKEN_TYP = "impersonation"


def mint_impersonation_token(
    *,
    organization_id: str,
    act_operator: str,
    session_id: str,
    secret: str,
    issued_at: dt.datetime,
    expires_at: dt.datetime,
    roles: list[str] | None = None,
) -> str:
    """Sign an act-as-examiner impersonation token for one tenant.

    ``expires_at`` is supplied already clamped to the originating inspector
    session's window (the operator endpoint does the clamping); this function
    signs exactly what it is given so the crypto stays policy-free.
    """
    payload: dict[str, Any] = {
        "typ": IMPERSONATION_TOKEN_TYP,
        "org": str(organization_id),
        "act_operator": act_operator,
        "session_id": str(session_id),
        "roles": list(roles) if roles is not None else ["examiner"],
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_impersonation_token(token: str, *, secret: str) -> dict[str, Any] | None:
    """Decode an impersonation token, distinguishing "not ours" from "invalid".

    Returns the claims when ``token`` is a valid, unexpired impersonation token
    signed by ``secret``.

    Returns ``None`` when the token is NOT an impersonation token at all — its
    signature does not verify against ``secret`` (a normal tenant access token,
    signed with a DIFFERENT secret, lands here), or it is malformed, or it lacks
    the ``impersonation`` typ. The caller then falls through to the normal
    access-token path with NO regression.

    Raises :class:`TokenInvalidError` when the token IS an impersonation token
    (its signature verifies against ``secret``) but fails a check — an EXPIRED
    impersonation token above all. An expired impersonation token must be
    rejected, never fall through.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "typ", "org"]},
        )
    except jwt.ExpiredSignatureError as exc:
        # Signature matched the impersonation secret → this IS an impersonation
        # token, but it has expired. Reject; do not fall through.
        raise TokenInvalidError("impersonation token expired") from exc
    except jwt.PyJWTError:
        # Wrong signature / malformed / missing required claim → not one of ours.
        return None
    if claims.get("typ") != IMPERSONATION_TOKEN_TYP:
        return None
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
    if not url.startswith("https://") and not _is_loopback_issuer_allowed(issuer):
        raise TokenInvalidError(f"OIDC issuer must be https, got {issuer!r}.")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - https (or non-production loopback) enforced above
            document = json.load(response)
    except Exception as exc:
        raise TokenInvalidError(f"OIDC discovery failed for {issuer!r}: {exc}") from exc
    jwks_uri = document.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not (
        jwks_uri.startswith("https://") or _is_loopback_issuer_allowed(jwks_uri)
    ):
        raise TokenInvalidError(f"OIDC discovery for {issuer!r} returned no usable jwks_uri.")
    return jwks_uri


def _is_loopback_issuer_allowed(url: str) -> bool:
    """Plain-http OIDC endpoints are tolerated ONLY on loopback and ONLY
    outside production — the same never-in-production rule as operator dev
    auth. This exists so the full workforce-OIDC path (discovery → JWKS →
    verification) can be exercised locally against a stub IdP; every deployed
    environment still hard-requires https."""
    from urllib.parse import urlparse  # noqa: PLC0415 - lazy; only the SSO path needs it

    from app.core.config import get_settings  # noqa: PLC0415 - avoid import cycle

    if get_settings().app.app_env == "production":
        return False
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


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
