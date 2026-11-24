"""BoG staff: ID-token validation, group-to-role mapping, permissions and SoD helpers.

The portal sends the identity provider's ID token as a Bearer token. It is validated
against ``GATEWAY_OIDC_ISSUER``'s JWKS (audience ``GATEWAY_OIDC_AUDIENCE``); the
``groups`` claim maps to roles. When no external provider is federated, the issuer is the
gateway's own identity provider (``app.idp``), which holds Argon2id password hashes only.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.chain import Actor, append
from app.core.context import AppContext, app_state, get_ctx
from app.core.encoding import b64url_decode, sha256_hex
from app.core.errors import ApiError
from app.core.jsonvalue import NotJsonError, loads_object
from app.core.settings import Settings
from app.db.models import StaffAccount, StaffSession, StaffUser
from app.keys.jwk import JwkError, jwks_keys, public_key_from_jwk
from app.keys.jws import JwsError, decode_signed_json, header_kid, unverified_header

Role = Literal["supervisor", "senior_supervisor", "returns_policy", "access_approver", "auditor"]
ALL_ROLES: tuple[Role, ...] = (
    "supervisor",
    "senior_supervisor",
    "returns_policy",
    "access_approver",
    "auditor",
)
ROLE_LABELS: dict[Role, str] = {
    "supervisor": "Supervisor",
    "senior_supervisor": "Senior supervisor",
    "returns_policy": "Returns policy officer",
    "access_approver": "Access approver",
    "auditor": "Auditor",
}
CLOCK_SKEW = timedelta(seconds=60)


def short_name(name: str, given: str | None, family: str | None) -> str:
    """``Kofi Owusu`` → ``K. Owusu``; team accounts without given/family names stay whole."""
    if given and family:
        return f"{given[0]}. {family}"
    return name


@dataclass(frozen=True)
class StaffPrincipal:
    subject: str
    name: str
    short_name: str
    email: str
    groups: tuple[str, ...]
    roles: frozenset[Role]
    mfa: bool
    session_ref: str

    def has(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)

    @property
    def is_auditor(self) -> bool:
        return "auditor" in self.roles

    @property
    def sees_all_institutions(self) -> bool:
        return self.has("senior_supervisor", "auditor")

    @property
    def actor(self) -> Actor:
        via = "AequorOS Portal" + (" · MFA" if self.mfa else "")
        return Actor("staff", self.subject, self.short_name, via)


def require_role(principal: StaffPrincipal, *roles: Role) -> None:
    if not principal.has(*roles):
        raise ApiError("forbidden", "Your role does not allow this.")


def require_decider(principal: StaffPrincipal, *roles: Role) -> None:
    """Decisions: the caller needs one of ``roles`` and must not hold the auditor role."""
    if principal.is_auditor:
        raise ApiError("forbidden", "Auditors read everything and decide nothing.")
    require_role(principal, *roles)


def roles_for(groups: tuple[str, ...], settings: Settings) -> frozenset[Role]:
    roles: set[Role] = set()
    for group in groups:
        mapped = settings.group_roles.get(group)
        for role in ALL_ROLES:
            if role == mapped:
                roles.add(role)
    return frozenset(roles)


# --- key resolution -------------------------------------------------------------------


class StaffKeyResolver(Protocol):
    def key_for(self, kid: str | None, alg: str) -> object | None: ...


def _rsa_from_jwk(jwk: Mapping[str, JsonValue]) -> rsa.RSAPublicKey:
    n_text = jwk.get("n")
    e_text = jwk.get("e")
    if not isinstance(n_text, str) or not isinstance(e_text, str):
        raise JwkError("RSA keys need n and e")
    n = int.from_bytes(b64url_decode(n_text), "big")
    e = int.from_bytes(b64url_decode(e_text), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def key_from_jwk(jwk: Mapping[str, JsonValue]) -> ec.EllipticCurvePublicKey | rsa.RSAPublicKey:
    if jwk.get("kty") == "RSA":
        return _rsa_from_jwk(jwk)
    return public_key_from_jwk(jwk)


class JwksKeyResolver:
    """Keys from a JWKS document, fetched by a callable and cached for ten minutes."""

    def __init__(self, fetch: FetchJwks, ttl_seconds: float = 600.0) -> None:
        self._fetch: FetchJwks = fetch
        self._ttl: float = ttl_seconds
        self._lock: threading.Lock = threading.Lock()
        self._keys: dict[str, ec.EllipticCurvePublicKey | rsa.RSAPublicKey] = {}
        self._fetched_at: float = 0.0

    def _refresh(self) -> None:
        document = self._fetch()
        keys: dict[str, ec.EllipticCurvePublicKey | rsa.RSAPublicKey] = {}
        for jwk in jwks_keys(document):
            kid = jwk.get("kid")
            try:
                keys[kid if isinstance(kid, str) else ""] = key_from_jwk(jwk)
            except (JwkError, ValueError):
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()

    def key_for(self, kid: str | None, alg: str) -> object | None:
        with self._lock:
            stale = time.monotonic() - self._fetched_at > self._ttl
            if stale or (kid is not None and kid not in self._keys):
                try:
                    self._refresh()
                except (OSError, JwkError, NotJsonError):
                    if not self._keys:
                        return None
            if kid is None:
                return next(iter(self._keys.values()), None)
            return self._keys.get(kid)


class FetchJwks(Protocol):
    def __call__(self) -> dict[str, JsonValue]: ...


def _get_json(url: str) -> dict[str, JsonValue]:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310  # pyright: ignore[reportAny]
        body: bytes = response.read()  # pyright: ignore[reportAny]
    return loads_object(body)


def http_jwks_fetcher(issuer: str, jwks_url: str) -> FetchJwks:
    def fetch() -> dict[str, JsonValue]:
        url = jwks_url
        if not url:
            discovery = _get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
            jwks_uri = discovery.get("jwks_uri")
            if not isinstance(jwks_uri, str):
                raise NotJsonError("discovery document lacks jwks_uri")
            url = jwks_uri
        return _get_json(url)

    return fetch


# --- validation ---------------------------------------------------------------------


@dataclass(frozen=True)
class IdTokenIdentity:
    subject: str
    name: str
    given_name: str | None
    family_name: str | None
    email: str
    groups: tuple[str, ...]
    mfa: bool
    expires_at: datetime


def _str_claim(claims: Mapping[str, JsonValue], name: str) -> str | None:
    value = claims.get(name)
    return value if isinstance(value, str) else None


def _int_claim(claims: Mapping[str, JsonValue], name: str) -> int | None:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _str_list(claims: Mapping[str, JsonValue], name: str) -> tuple[str, ...]:
    value = claims.get(name)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def validate_id_token(
    token: str, *, settings: Settings, now: datetime, resolver: StaffKeyResolver
) -> IdTokenIdentity:
    issuer = settings.effective_oidc_issuer
    if not issuer:
        raise ApiError("unauthenticated", "No identity provider is configured.")
    try:
        header = unverified_header(token)
    except JwsError as exc:
        raise ApiError("unauthenticated", "The bearer token is not a JWT.") from exc
    raw_alg = header.get("alg")
    alg = raw_alg if isinstance(raw_alg, str) else ""
    if alg not in ("ES256", "RS256"):
        raise ApiError("unauthenticated", "The token algorithm is not accepted.")
    key = resolver.key_for(header_kid(header), alg)
    if key is None:
        raise ApiError("unauthenticated", "The token signing key is not known.")
    try:
        _, claims = decode_signed_json(token, key, algorithms=[alg])
    except JwsError as exc:
        raise ApiError("unauthenticated", "The token signature is not valid.") from exc
    if _str_claim(claims, "iss") != issuer:
        raise ApiError("unauthenticated", "The token issuer is not trusted.")
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else _str_list(claims, "aud")
    if settings.oidc_audience not in audiences:
        raise ApiError("unauthenticated", "The token audience is not this portal.")
    exp = _int_claim(claims, "exp")
    iat = _int_claim(claims, "iat")
    if exp is None or iat is None:
        raise ApiError("unauthenticated", "The token lacks exp or iat.")
    epoch = now.timestamp()
    if exp <= epoch - CLOCK_SKEW.total_seconds():
        raise ApiError("unauthenticated", "The token has expired.")
    if iat > epoch + CLOCK_SKEW.total_seconds():
        raise ApiError("unauthenticated", "The token is not valid yet.")
    subject = _str_claim(claims, "sub")
    if not subject:
        raise ApiError("unauthenticated", "The token lacks a subject.")
    return IdTokenIdentity(
        subject=subject,
        name=_str_claim(claims, "name") or subject,
        given_name=_str_claim(claims, "given_name"),
        family_name=_str_claim(claims, "family_name"),
        email=_str_claim(claims, "email") or "",
        groups=_str_list(claims, "groups"),
        mfa="mfa" in _str_list(claims, "amr"),
        expires_at=datetime.fromtimestamp(exp, UTC),
    )


def _existing_session(session: Session, token_hash: str) -> StaffSession | None:
    return session.scalars(
        select(StaffSession).where(StaffSession.token_hash == token_hash)
    ).first()


def _record_session(
    session: Session, identity: IdTokenIdentity, token_hash: str, now: datetime
) -> bool:
    """Record a token's first use; ``False`` when a concurrent request already did.

    The portal sends several requests with a new token at once, so two of them can both
    find no session and insert one. The loser's insert is undone in its own savepoint
    instead of failing the request, and only the winner writes the sign-in audit entry.
    """
    try:
        with session.begin_nested():
            session.add(
                StaffSession(
                    subject=identity.subject,
                    token_hash=token_hash,
                    first_seen_at=now,
                    expires_at=identity.expires_at,
                    mfa=identity.mfa,
                )
            )
            session.flush()
    except IntegrityError:
        return False
    return True


def record_staff(
    session: Session, ctx: AppContext, identity: IdTokenIdentity, token: str
) -> StaffPrincipal:
    """Upsert the staff directory row and record a sign-in for a token not seen before."""
    now = ctx.clock.now()
    roles = roles_for(identity.groups, ctx.settings)
    sorted_roles: list[str] = [str(role) for role in sorted(roles)]
    display = short_name(identity.name, identity.given_name, identity.family_name)
    user = session.get(StaffUser, identity.subject)
    if user is None:
        # A page opens with several requests at once; the first sign-in of a person
        # may be recorded by any of them. Whichever loses the race reads the winner's row.
        try:
            with session.begin_nested():
                session.add(
                    StaffUser(
                        subject=identity.subject,
                        name=identity.name,
                        short_name=display,
                        email=identity.email,
                        groups=list(identity.groups),
                        roles=sorted_roles,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
                session.flush()
        except IntegrityError:
            user = session.get(StaffUser, identity.subject)
    else:
        if (
            user.name != identity.name
            or user.short_name != display
            or user.email != identity.email
            or user.groups != list(identity.groups)
            or user.roles != sorted_roles
        ):
            user.name = identity.name
            user.short_name = display
            user.email = identity.email
            user.groups = list(identity.groups)
            user.roles = sorted_roles
        if now - user.last_seen_at > timedelta(seconds=60):
            user.last_seen_at = now
    token_hash = sha256_hex(token.encode("ascii"))
    existing = _existing_session(session, token_hash)
    principal = StaffPrincipal(
        subject=identity.subject,
        name=identity.name,
        short_name=display,
        email=identity.email,
        groups=identity.groups,
        roles=roles,
        mfa=identity.mfa,
        session_ref=token_hash[:4],
    )
    if existing is None and _record_session(session, identity, token_hash, now):
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=principal.actor,
            category="sign_in",
            action="Signed in",
            object_type="staff_session",
            object_ref=f"session {token_hash[:4]}…",
            detail={"roles": list(sorted_roles), "mfa": identity.mfa},
        )
    return principal


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError("unauthenticated", "A Bearer token is required.")
    return token.strip()


def staff_resolver(request: Request) -> StaffKeyResolver:
    resolver = app_state(request, "staff_resolver")
    if resolver is None:
        raise ApiError("unauthenticated", "No identity provider is configured.")
    return resolver  # pyright: ignore[reportReturnType]


def authenticate_staff(request: Request) -> StaffPrincipal:
    """FastAPI dependency: validate the ID token and record the staff sign-in."""
    ctx = get_ctx(request)
    token = bearer_token(request)
    identity = validate_id_token(
        token, settings=ctx.settings, now=ctx.clock.now(), resolver=staff_resolver(request)
    )
    with ctx.db.session() as session:
        if ctx.settings.uses_builtin_idp:
            # An account disabled at the gateway's own provider loses access at once,
            # not when its last ID token expires. (Accounts are disabled, never deleted.)
            account = session.get(StaffAccount, identity.subject)
            if account is not None and account.status != "active":
                raise ApiError("unauthenticated", "This account is disabled.")
        principal = record_staff(session, ctx, identity, token)
    if not principal.roles:
        raise ApiError("forbidden", "Your account is in no gateway group.")
    return principal


def staff_display_names(session: Session, subjects: set[str]) -> dict[str, str]:
    if not subjects:
        return {}
    rows = session.scalars(select(StaffUser).where(StaffUser.subject.in_(subjects)))
    return {row.subject: row.short_name for row in rows}
