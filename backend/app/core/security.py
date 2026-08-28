"""Auth primitives: Argon2id password hashing and app-JWT sign/verify.

The backend is both the issuer and the verifier of app tokens (HS256 over
``AuthSettings.jwt_secret``), so every API request is authenticated by verifying a
signed token — never by trusting a header. A token carries the tenant (``org``),
the user (``sub``), legacy ``roles``, and authoritative authorization version
(``authv``); the API layer rejects a version that no longer matches the user.

If ``AUTH_JWT_SECRET`` is unset, :func:`create_token` / :func:`decode_token` raise
``AuthConfigError`` rather than degrade — the demo header-trust path can never
silently return.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from app.core.config import UNDEPLOYED_ENVS, AuthSettings, get_settings
from app.db.base import utc_now

if TYPE_CHECKING:
    from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# Legacy operational roles, most- to least-privileged. ``admin`` remains in the
# token vocabulary only so pre-migration/test claims can be decoded, but migration
# 202608280045 converts every persisted administrator to ``account_admin`` and
# invalidates their sessions. ``account_admin`` is deliberately outside this
# ladder and is authorized only by the explicit account-administration gate.
ROLES: tuple[str, ...] = ("admin", "approver", "analyst", "examiner", "viewer")
_ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}
ACCOUNT_ADMIN_ROLE = "account_admin"

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
    if required == ACCOUNT_ADMIN_ROLE:
        return ACCOUNT_ADMIN_ROLE in user_roles
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
    authorization_version: int,
    token_type: TokenType,
    email: str | None = None,
    name: str | None = None,
    jti: str | None = None,
    now: dt.datetime | None = None,
    settings: AuthSettings | None = None,
) -> str:
    """Sign an app access/refresh token for (org, user, roles, authority version).

    ``jti`` is REQUIRED for a refresh token and forbidden-by-omission for an
    access token: refresh-token state (rotation, reuse detection, revocation)
    lives in ``refresh_tokens`` keyed by that identifier, so a refresh token
    minted without one would be unrevokable by construction. Access tokens have
    no server-side token row, but their ``authv`` is checked against the tenant
    user on every data-bearing request.
    """
    settings = settings or get_settings().auth
    if isinstance(authorization_version, bool) or authorization_version < 1:
        raise ValueError("authorization_version must be a positive integer")
    moment = now or utc_now()
    ttl = (
        settings.access_token_ttl_seconds
        if token_type == "access"
        else settings.refresh_token_ttl_seconds
    )
    if token_type == "refresh" and not jti:
        msg = (
            "a refresh token must carry a jti — its rotation/revocation state is "
            "keyed by it (app.services.authentication.issue_tokens)."
        )
        raise ValueError(msg)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "org": str(organization_id),
        "roles": list(roles),
        "authv": authorization_version,
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
    if jti is not None:
        payload["jti"] = jti
    return jwt.encode(payload, _secret(settings), algorithm=settings.jwt_algorithm)


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
    settings: AuthSettings | None = None,
) -> dict[str, Any]:
    """Verify signature, expiry, issuer, audience, and required claims; return them.

    Every app token MUST carry ``authv``; a refresh token additionally MUST carry
    ``jti``. That is the fail-closed half
    of refresh-token revocation: a token with no ``jti`` has no server-side state,
    so it can be neither rotated nor revoked — and tokens minted before migration
    ``202608220028`` carry none. They are refused here rather than silently
    trusted. Tokens predating authorization migration ``202608250044`` have no
    ``authv`` and also re-authenticate once.
    """
    settings = settings or get_settings().auth
    required = ["exp", "iat", "sub", "org", "type", "authv"]
    if expected_type == "refresh":
        required.append("jti")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            _secret(settings),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": required},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(str(exc)) from exc
    if expected_type is not None and claims.get("type") != expected_type:
        msg = f"expected a {expected_type} token, got {claims.get('type')!r}"
        raise TokenInvalidError(msg)
    authorization_version = claims.get("authv")
    if (
        isinstance(authorization_version, bool)
        or not isinstance(authorization_version, int)
        or authorization_version < 1
    ):
        raise TokenInvalidError("authv must be a positive integer")
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


def mint_impersonation_token(  # noqa: PLR0913 - one keyword per signed claim
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


# -- OIDC discovery + JWKS ---------------------------------------------------
# Every fetch below aims the backend's socket at a destination somebody else
# chose. The ``issuer`` is TENANT-SETTABLE (an org admin's
# ``SsoConnectionUpdateRequest.issuer``); the ``jwks_uri`` is named by whatever
# answered the discovery request — a SECOND, separately attacker-controlled URL;
# and either fetch can be redirected onward by the remote side. An
# ``https://`` prefix test alone therefore made
# ``https://169.254.169.254/.well-known/openid-configuration`` a tenant-reachable
# read of cloud instance-metadata credentials, so each of those URLs now goes
# through the RESOLVING egress guard (:mod:`app.core.outbound`) immediately
# before it becomes a socket — the same primitive the Database-Direct, Temenos
# and ORASS connectors use. The loopback carve-out below is honoured first so a
# local stub IdP still works; it is False on every DEPLOYED environment
# (``staging`` included, not only ``production``), so there is no path around
# the guard on a reachable host.

_OIDC_FETCH_TIMEOUT_SECONDS: Final = 10


def _guard_oidc_target(url: str, *, field: str) -> None:
    """Refuse an OIDC endpoint that is not a routable public destination.

    Raises :class:`TokenInvalidError` rather than letting
    ``OutboundTargetBlocked`` (a ``ValueError``) escape: every caller of the SSO
    path catches :class:`AuthError` and turns it into a clean 401, so a blocked
    issuer is an authentication failure, never a 500. The resolved address and
    the block reason go to the log line only — the raised message names the
    field and nothing else, so this can never become an internal-topology
    oracle for whoever set the issuer.
    """
    if _is_loopback_issuer_allowed(url):
        return
    from app.core.outbound import (  # noqa: PLC0415 - lazy; only the SSO path needs it
        OutboundTargetBlocked,
        check_url,
    )

    try:
        check_url(url, field=field)
    except OutboundTargetBlocked as exc:
        logger.warning(
            "OIDC %s blocked by the egress guard (%s): %s",
            field,
            exc.reason,
            exc.internal_detail,
        )
        msg = f"The OIDC {field} is not a permitted destination for an outbound connection."
        raise TokenInvalidError(msg) from exc


@lru_cache(maxsize=1)
def _oidc_opener() -> Any:
    """A ``urllib`` opener that re-validates every redirect hop.

    ``urllib``'s default opener follows 3xx silently, so a permitted issuer
    answering ``302 Location: http://169.254.169.254/`` would walk straight past
    the check on the original URL. Used for the discovery fetch AND — via
    :func:`_jwks_client` — for the JWKS fetch, because ``PyJWKClient`` reaches
    for that same unguarded default opener.
    """
    import urllib.request  # noqa: PLC0415 - lazy; only the SSO path needs it

    class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202, PLR0913 - urllib's fixed signature
            _guard_oidc_target(newurl, field="redirect target")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_GuardedRedirectHandler)


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    from jwt import PyJWKClient  # noqa: PLC0415 - lazy; only the SSO path needs it
    from jwt.exceptions import PyJWKClientConnectionError  # noqa: PLC0415

    class _GuardedJWKClient(PyJWKClient):
        """``PyJWKClient`` whose JWKS fetch goes through :func:`_oidc_opener`.

        Upstream ``fetch_data`` calls ``urllib.request.urlopen`` — the process
        default opener, which follows redirects with nothing checking where they
        lead. Overriding that one method is the narrowest way to put the JWKS
        hop under the same guard as discovery; the two caching tiers above it
        are untouched. Kept deliberately byte-thin against upstream so a pyjwt
        bump is easy to re-check.
        """

        def fetch_data(self) -> Any:
            import json  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415
            from urllib.error import HTTPError, URLError  # noqa: PLC0415

            try:
                request = urllib.request.Request(url=self.uri, headers=self.headers)  # noqa: S310 - destination guarded above; redirects guarded by the opener
                with _oidc_opener().open(request, timeout=self.timeout) as response:
                    jwk_set = json.load(response)
            except (URLError, TimeoutError) as exc:
                if isinstance(exc, HTTPError):
                    exc.close()
                msg = f'Fail to fetch data from the url, err: "{exc}"'
                raise PyJWKClientConnectionError(msg) from exc
            if self.jwk_set_cache is not None:
                self.jwk_set_cache.put(jwk_set)
            return jwk_set

    return _GuardedJWKClient(jwks_url)  # caches fetched signing keys internally


@lru_cache(maxsize=8)
def _discover_jwks_uri(issuer: str) -> str:
    """Resolve an issuer's JWKS URL via OIDC discovery.

    The discovery document's location is fixed by the OIDC spec, so this works
    for any compliant IdP (Google, Entra, Okta, Keycloak, …) without vendor
    branches. Cached per issuer — the jwks_uri itself effectively never changes;
    key *rotation* is handled inside PyJWKClient.

    Two destinations are screened here, and they are screened separately: the
    discovery URL derived from the tenant-supplied ``issuer``, and the
    ``jwks_uri`` the discovery document names. The second is NOT covered by the
    first — a public issuer can hand back ``http://169.254.169.254/`` — so it is
    re-checked before it reaches ``PyJWKClient``.
    """
    import json  # noqa: PLC0415 - lazy; only the SSO path needs these
    import urllib.request  # noqa: PLC0415

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    if not url.startswith("https://") and not _is_loopback_issuer_allowed(issuer):
        raise TokenInvalidError(f"OIDC issuer must be https, got {issuer!r}.")
    _guard_oidc_target(url, field="issuer")
    try:
        request = urllib.request.Request(url)  # noqa: S310 - scheme + egress guard enforced above
        with _oidc_opener().open(request, timeout=_OIDC_FETCH_TIMEOUT_SECONDS) as response:
            document = json.load(response)
    except TokenInvalidError:
        raise  # a blocked redirect hop is an auth failure, not "discovery failed"
    except Exception as exc:
        raise TokenInvalidError(f"OIDC discovery failed for {issuer!r}: {exc}") from exc
    jwks_uri = document.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not (
        jwks_uri.startswith("https://") or _is_loopback_issuer_allowed(jwks_uri)
    ):
        raise TokenInvalidError(f"OIDC discovery for {issuer!r} returned no usable jwks_uri.")
    _guard_oidc_target(jwks_uri, field="jwks_uri")
    return jwks_uri


#: Environments where a developer's own machine IS the deployment. Everything
#: else — ``staging`` included — runs the same containers on a reachable host.
#: Defined once in ``app.core.config`` so the operator plane's never-in-
#: production guards and this one cannot drift apart (they did: those asked
#: ``app_env == "production"`` until 2026-08-23).
_UNDEPLOYED_ENVS: Final[frozenset[str]] = UNDEPLOYED_ENVS


def _is_loopback_issuer_allowed(url: str) -> bool:
    """Plain-http OIDC endpoints are tolerated ONLY on loopback and ONLY on an
    UNDEPLOYED environment (``local``/``test``).

    This exists so the full workforce-OIDC path (discovery → JWKS →
    verification) can be exercised locally against a stub IdP. It used to read
    "not production", which quietly left it open on ``staging`` — and unlike the
    other never-in-production switches (operator dev auth, ``outbound``'s
    private-target hatch) this one has no second flag to fail to set, while the
    value it screens is TENANT-settable. An org admin on a staging host could
    therefore point an SSO connection's ``issuer`` at ``http://127.0.0.1:8100``
    (the operator control plane) or ``http://127.0.0.1:8200`` (OpenBao) and have
    the backend fetch discovery from it. Staging now gets production's
    guarantee; ``local`` and ``test`` serve the carve-out's stated purpose in
    full.
    """
    from urllib.parse import urlparse  # noqa: PLC0415 - lazy; only the SSO path needs it

    from app.core.config import get_settings  # noqa: PLC0415 - avoid import cycle

    if get_settings().app.app_env not in _UNDEPLOYED_ENVS:
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
