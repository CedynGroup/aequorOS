"""Machine clients (institution and operator): certificate presentation, client lookup
and certificate-bound access-token verification. Both kinds authenticate the same way."""

from __future__ import annotations

import ipaddress
import urllib.parse
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import Actor
from app.core.certs import PresentedCertificate, parse_certificate_pem
from app.core.context import AppContext
from app.core.errors import ApiError
from app.core.settings import Settings
from app.db.models import ApiClient, Institution
from app.keys.jws import JwsError, decode_signed_json, header_kid, unverified_header

CERT_HEADER = "x-forwarded-tls-client-cert"
INSTITUTION_SCOPES: tuple[str, ...] = (
    "returns:read",
    "obligations:read",
    "submissions:write",
    "submissions:read",
    "queries:read",
    "queries:write",
)
OPERATOR_SCOPE = "ops:enrol"
ACCESS_TOKEN_TYP = "at+jwt"  # noqa: S105 - a JWT typ, not a secret
CLOCK_SKEW = timedelta(seconds=30)
ClientKind = Literal["institution", "operator"]


def _from_trusted_proxy(request: Request, settings: Settings) -> bool:
    if settings.is_dev:
        return True
    client = request.client
    if client is None:
        return False
    try:
        peer = ipaddress.ip_address(client.host)
    except ValueError:
        return False
    return any(peer in network for network in settings.trusted_proxy_networks())


_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"


def forwarded_certificate_pem(raw: str) -> str:
    """The leaf certificate from the ingress header, as PEM, in either ingress's format.

    nginx (``$ssl_client_escaped_cert``) forwards the URL-encoded PEM. Traefik
    (``passTLSClientCert`` with ``pem``), the ingress Coolify runs, forwards the
    URL-encoded base64 body with the BEGIN/END lines and newlines removed, and a
    chain as comma-separated entries, leaf first.
    """
    text = urllib.parse.unquote(raw).strip()
    if _PEM_BEGIN in text:
        return text
    leaf = "".join(text.split(",", 1)[0].split())
    body = "\n".join(leaf[i : i + 64] for i in range(0, len(leaf), 64))
    return f"{_PEM_BEGIN}\n{body}\n{_PEM_END}\n"


def presented_certificate(request: Request, ctx: AppContext) -> PresentedCertificate:
    """The client certificate forwarded by a trusted ingress (or injected in local|test)."""
    raw = request.headers.get(CERT_HEADER)
    if not raw or not _from_trusted_proxy(request, ctx.settings):
        raise ApiError(
            "certificate_required", "Present the client certificate issued at enrolment."
        )
    try:
        certificate = parse_certificate_pem(forwarded_certificate_pem(raw))
    except ValueError as exc:
        raise ApiError("certificate_required", "The client certificate could not be read.") from exc
    now = ctx.clock.now()
    if not certificate.not_before - CLOCK_SKEW <= now <= certificate.not_after:
        raise ApiError("certificate_required", "The client certificate is not currently valid.")
    return certificate


@dataclass(frozen=True)
class ClientIdentity:
    client_id: str
    kind: ClientKind
    jwks: dict[str, JsonValue]
    thumbprint: str
    scopes: tuple[str, ...]
    revoked: bool
    institution: Institution | None
    environment: str

    @property
    def suspended(self) -> bool:
        return self.institution is not None and self.institution.status == "suspended"

    @property
    def actor(self) -> Actor:
        if self.kind == "operator" or self.institution is None:
            return Actor(
                "operator", self.client_id, "Channel operator", f"Operator client {self.client_id}"
            )
        return Actor(
            "institution", self.client_id, self.institution.name, f"API client {self.client_id}"
        )


def resolve_client(session: Session, ctx: AppContext, client_id: str) -> ClientIdentity | None:
    operator = ctx.operators.get(client_id)
    if operator is not None:
        return ClientIdentity(
            client_id=operator.client_id,
            kind="operator",
            jwks=operator.jwks,
            thumbprint=operator.thumbprint,
            scopes=(OPERATOR_SCOPE,),
            revoked=False,
            institution=None,
            environment="production",
        )
    client = session.scalars(select(ApiClient).where(ApiClient.client_id == client_id)).first()
    if client is None:
        return None
    institution = session.get(Institution, client.institution_id)
    return ClientIdentity(
        client_id=client.client_id,
        kind="institution",
        jwks=client.jwks,
        thumbprint=client.certificate_thumbprint,
        scopes=tuple(scope for scope in client.scopes if scope in INSTITUTION_SCOPES),
        revoked=client.status != "active",
        institution=institution,
        environment=client.environment,
    )


def _claim_str(claims: dict[str, JsonValue], name: str) -> str | None:
    value = claims.get(name)
    return value if isinstance(value, str) else None


def _claim_int(claims: dict[str, JsonValue], name: str) -> int | None:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@dataclass(frozen=True)
class VerifiedToken:
    client: ClientIdentity
    scopes: frozenset[str]
    certificate: PresentedCertificate


def verify_access_token(
    session: Session, ctx: AppContext, token: str, certificate: PresentedCertificate
) -> VerifiedToken:
    try:
        header = unverified_header(token)
    except JwsError as exc:
        raise ApiError("unauthenticated", "The bearer token is not a JWT.") from exc
    if header.get("typ") != ACCESS_TOKEN_TYP:
        raise ApiError("unauthenticated", "The bearer token is not a gateway access token.")
    kid = header_kid(header)
    key = ctx.keys.find("receipt", kid) if kid else None
    if key is None:
        raise ApiError("unauthenticated", "The access token key is not known.")
    try:
        _, claims = decode_signed_json(token, key.public_key, algorithms=["ES256"])
    except JwsError as exc:
        raise ApiError("unauthenticated", "The access token signature is not valid.") from exc
    if _claim_str(claims, "iss") != ctx.settings.issuer:
        raise ApiError("unauthenticated", "The access token issuer is not this gateway.")
    exp = _claim_int(claims, "exp")
    iat = _claim_int(claims, "iat")
    now = ctx.clock.now().timestamp()
    if exp is None or iat is None or exp <= now or iat > now + CLOCK_SKEW.total_seconds():
        raise ApiError("unauthenticated", "The access token has expired or is not valid yet.")
    cnf = claims.get("cnf")
    bound = cnf.get("x5t#S256") if isinstance(cnf, dict) else None
    if bound != certificate.thumbprint:
        raise ApiError(
            "certificate_mismatch", "The access token is bound to a different certificate."
        )
    client_id = _claim_str(claims, "client_id") or _claim_str(claims, "sub") or ""
    client = resolve_client(session, ctx, client_id)
    if client is None or client.revoked:
        raise ApiError("unauthenticated", "The client is not active.")
    if client.thumbprint != certificate.thumbprint:
        raise ApiError("certificate_mismatch", "The certificate is not registered to this client.")
    if client.suspended:
        raise ApiError("forbidden", "The institution is suspended from the channel.")
    granted = frozenset((_claim_str(claims, "scope") or "").split()) & frozenset(client.scopes)
    return VerifiedToken(client=client, scopes=granted, certificate=certificate)


def bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError("unauthenticated", "A Bearer access token is required.")
    return token.strip()


def authenticate_client(request: Request, ctx: AppContext) -> VerifiedToken:
    certificate = presented_certificate(request, ctx)
    token = bearer(request)
    with ctx.db.session() as session:
        verified = verify_access_token(session, ctx, token, certificate)
    retry = ctx.rate_limiter.check(verified.client.client_id, ctx.clock.now().timestamp())
    if retry is not None:
        raise ApiError(
            "rate_limited",
            "The rate limit is 600 requests per minute per client.",
            headers={"Retry-After": str(retry)},
        )
    return verified


def insufficient_scope(scope: str) -> ApiError:
    return ApiError(
        "insufficient_scope",
        f"This call needs the {scope} scope.",
        headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'},
    )
