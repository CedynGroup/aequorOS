"""``POST /oauth/token``: OAuth 2.0 client credentials with ``private_key_jwt``.

The client presents its certificate and a client assertion signed with a key from its
registered JWKS (``iss`` = ``sub`` = client_id, ``aud`` = the token endpoint URL, unique
``jti``, ``exp`` at most 300 s after ``iat``). No shared secret exists. The issued access
token is an ES256 JWT signed with the gateway receipt key and bound to the certificate.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, JsonValue
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.chain import append
from app.auth.clients import (
    ACCESS_TOKEN_TYP,
    ClientIdentity,
    presented_certificate,
    resolve_client,
)
from app.core.certs import PresentedCertificate
from app.core.context import AppContext, get_ctx
from app.core.encoding import b64url_decode
from app.core.errors import ApiError, Problem
from app.core.jcs import canonicalize
from app.core.jsonvalue import NotJsonError, loads_object
from app.db.models import UsedAssertion
from app.keys.jwk import JwkError, jwks_keys, public_key_from_jwk
from app.keys.jws import (
    JwsError,
    decode_signed_json,
    header_kid,
    sign_compact,
    unverified_header,
)

router = APIRouter(tags=["Authentication"])

ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
MAX_ASSERTION_LIFETIME = 300
CLOCK_SKEW = 30


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    scope: str


def _bad(detail: str) -> ApiError:
    return ApiError("invalid_client_assertion", detail)


def _int(claims: dict[str, JsonValue], name: str) -> int | None:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def verify_client_assertion(
    session: Session, ctx: AppContext, assertion: str, certificate: PresentedCertificate
) -> ClientIdentity:
    try:
        header = unverified_header(assertion)
    except JwsError as exc:
        raise _bad("The client assertion is not a JWT.") from exc
    if header.get("alg") != "ES256":
        raise _bad("Client assertions must be signed with ES256.")
    # Identify the client from the unverified claims, then verify against its JWKS.
    parts = assertion.split(".")
    try:
        unverified = loads_object(b64url_decode(parts[1]))
    except (NotJsonError, ValueError, IndexError) as exc:
        raise _bad("The client assertion payload is not JSON.") from exc
    issuer = unverified.get("iss")
    subject = unverified.get("sub")
    if not isinstance(issuer, str) or issuer != subject:
        raise _bad("iss and sub must both be the client_id.")
    client = resolve_client(session, ctx, issuer)
    if client is None or client.revoked:
        raise _bad("The client is not registered or has been revoked.")
    if client.thumbprint != certificate.thumbprint:
        raise ApiError(
            "certificate_mismatch", "The certificate is not the one issued to this client."
        )
    if client.suspended:
        raise ApiError(
            "forbidden", "The institution is suspended; its clients cannot obtain tokens."
        )
    kid = header_kid(header)
    try:
        candidates = [jwk for jwk in jwks_keys(client.jwks) if kid is None or jwk.get("kid") == kid]
    except JwkError as exc:
        raise _bad("The client's registered JWKS is not usable.") from exc
    claims: dict[str, JsonValue] | None = None
    for jwk in candidates:
        try:
            _, claims = decode_signed_json(
                assertion, public_key_from_jwk(jwk), algorithms=["ES256"]
            )
            break
        except (JwsError, JwkError):
            continue
    if claims is None:
        raise _bad("The client assertion signature does not match a registered key.")
    audience = claims.get("aud")
    if isinstance(audience, str):
        audiences = [audience]
    elif isinstance(audience, list):
        audiences = [a for a in audience if isinstance(a, str)]
    else:
        audiences = []
    if ctx.settings.token_endpoint not in audiences:
        raise _bad(f"aud must be {ctx.settings.token_endpoint}.")
    exp = _int(claims, "exp")
    iat = _int(claims, "iat")
    jti = claims.get("jti")
    if exp is None or iat is None or not isinstance(jti, str) or not jti:
        raise _bad("The client assertion needs exp, iat and jti.")
    now = ctx.clock.now().timestamp()
    if exp <= now:
        raise _bad("The client assertion has expired.")
    if iat > now + CLOCK_SKEW:
        raise _bad("The client assertion was issued in the future.")
    if exp - iat > MAX_ASSERTION_LIFETIME or exp > now + MAX_ASSERTION_LIFETIME + CLOCK_SKEW:
        raise _bad("exp may be at most 300 seconds after iat.")
    session.execute(
        delete(UsedAssertion).where(
            UsedAssertion.expires_at < ctx.clock.now() - timedelta(minutes=10)
        )
    )
    seen = session.scalars(
        select(UsedAssertion).where(
            UsedAssertion.client_id == client.client_id, UsedAssertion.jti == jti
        )
    ).first()
    if seen is not None:
        raise _bad("The client assertion jti has been used before.")
    session.add(
        UsedAssertion(
            client_id=client.client_id, jti=jti, expires_at=datetime.fromtimestamp(exp, UTC)
        )
    )
    try:
        session.flush()
    except IntegrityError as exc:
        raise _bad("The client assertion jti has been used before.") from exc
    return client


def issue_access_token(
    session: Session, ctx: AppContext, client: ClientIdentity, scope: str | None
) -> TokenResponse:
    granted = set(client.scopes)
    requested = set(scope.split()) if scope else set(granted)
    if not requested <= granted:
        raise ApiError("invalid_request", "The requested scope exceeds the client's scopes.")
    scope_text = " ".join(sorted(requested))
    now = int(ctx.clock.now().timestamp())
    lifetime = ctx.settings.access_token_seconds
    claims: dict[str, JsonValue] = {
        "iss": ctx.settings.issuer,
        "sub": client.client_id,
        "client_id": client.client_id,
        "client_kind": client.kind,
        "scope": scope_text,
        "cnf": {"x5t#S256": client.thumbprint},
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_hex(16),
    }
    if client.institution is not None:
        claims["institution_code"] = client.institution.code
    token = sign_compact(ctx.keys.current("receipt"), canonicalize(claims), typ=ACCESS_TOKEN_TYP)
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=client.actor,
        category="sign_in",
        action="Issued access token",
        object_type="api_client",
        object_ref=client.client_id,
        institution_code=client.institution.code if client.institution else None,
        detail={"scope": scope_text, "environment": client.environment, "expires_in": lifetime},
    )
    return TokenResponse(
        access_token=token,
        token_type="Bearer",  # noqa: S106 - the OAuth token type
        expires_in=lifetime,
        scope=scope_text,
    )


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    summary="Obtain an access token (client credentials with private_key_jwt)",
    responses={400: {"model": Problem}, 401: {"model": Problem}},
)
def token(
    request: Request,
    grant_type: Annotated[str, Form()],
    client_assertion_type: Annotated[str, Form()],
    client_assertion: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    ctx = get_ctx(request)
    certificate = presented_certificate(request, ctx)
    if grant_type != "client_credentials":
        raise ApiError("invalid_request", "grant_type must be client_credentials.")
    if client_assertion_type != ASSERTION_TYPE:
        raise ApiError("invalid_request", f"client_assertion_type must be {ASSERTION_TYPE}.")
    with ctx.db.session() as session:
        client = verify_client_assertion(session, ctx, client_assertion, certificate)
        response = issue_access_token(session, ctx, client, scope)
    return JSONResponse(
        response.model_dump(), headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )
