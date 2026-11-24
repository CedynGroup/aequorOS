"""Enrolment: institutions, API clients and signing keys (SPEC Part 5).

Only the operator API (and the seed, through these functions) creates or changes them.
Writes are idempotent where the contract says so; every call is an audit entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import Actor, append
from app.auth.clients import INSTITUTION_SCOPES
from app.core.certs import describe_certificate
from app.core.clock import iso
from app.core.context import AppContext
from app.core.errors import ApiError
from app.core.jcs import canonicalize
from app.db.models import ApiClient, Institution, InstitutionContact, SigningKey, Submission
from app.enrolment.ca import pem, require_ca
from app.keys.jwk import JwkError, jwks_keys, public_jwk, public_key_from_jwk
from app.keys.provider import KeyConfigurationError

InstitutionType = Literal[
    "universal_bank", "rural_bank", "savings_and_loans", "microfinance", "other"
]
Environment = Literal["sandbox", "production"]

TYPE_LABELS: dict[str, str] = {
    "universal_bank": "Universal bank",
    "rural_bank": "Rural bank",
    "savings_and_loans": "Savings and loans company",
    "microfinance": "Microfinance institution",
    "other": "Other institution",
}


class ContactIn(BaseModel):
    role: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200)


class EnrolInstitutionRequest(BaseModel):
    code: str = Field(pattern=r"^\d{4}$", description="BoG institution code, 4 digits")
    name: str = Field(min_length=1, max_length=200)
    type: InstitutionType
    contacts: list[ContactIn] = Field(default_factory=lambda: [])


class EnrolledClient(BaseModel):
    client_id: str
    environment: Environment
    certificate_not_after: str
    revoked: bool


class EnrolledKey(BaseModel):
    kid: str
    status: Literal["active", "retired", "revoked"]
    registered_at: str


class EnrolledInstitution(BaseModel):
    code: str
    name: str
    type: InstitutionType
    status: Literal["enrolled", "live", "suspended"]
    enrolled_at: str
    clients: list[EnrolledClient]
    signing_keys: list[EnrolledKey]


class JwksIn(BaseModel):
    keys: list[dict[str, JsonValue]] = Field(min_length=1)


class SoftwareIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)


class IssueClientRequest(BaseModel):
    environment: Environment
    csr_pem: str
    jwks: JwksIn
    software: SoftwareIn | None = None


class IssuedClient(BaseModel):
    client_id: str
    certificate_pem: str
    certificate_chain_pem: str
    not_after: str
    scopes: list[str]


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RegisterKeyRequest(BaseModel):
    kid: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")
    public_jwk: dict[str, JsonValue]


class StatusRequest(BaseModel):
    status: Literal["live", "suspended"]
    reason: str = Field(min_length=1, max_length=500)


def _institution(session: Session, code: str) -> Institution:
    institution = session.scalars(select(Institution).where(Institution.code == code)).first()
    if institution is None:
        raise ApiError("not_found", f"No institution with code {code} is enrolled.")
    return institution


def _key_status(value: str) -> Literal["active", "retired", "revoked"]:
    if value == "retired":
        return "retired"
    if value == "revoked":
        return "revoked"
    return "active"


def _env(value: str) -> Environment:
    return "production" if value == "production" else "sandbox"


def _type(value: str) -> InstitutionType:
    for known in ("universal_bank", "rural_bank", "savings_and_loans", "microfinance"):
        if value == known:
            return known
    return "other"


def enrolled_view(session: Session, institution: Institution) -> EnrolledInstitution:
    clients = session.scalars(
        select(ApiClient)
        .where(ApiClient.institution_id == institution.id)
        .order_by(ApiClient.created_at)
    ).all()
    keys = session.scalars(
        select(SigningKey)
        .where(SigningKey.institution_id == institution.id)
        .order_by(SigningKey.registered_at)
    ).all()
    return EnrolledInstitution(
        code=institution.code,
        name=institution.name,
        type=_type(institution.type),
        status=display_status(session, institution),
        enrolled_at=iso(institution.registered_at),
        clients=[
            EnrolledClient(
                client_id=c.client_id,
                environment=_env(c.environment),
                certificate_not_after=iso(c.certificate_not_after),
                revoked=c.status != "active",
            )
            for c in clients
        ],
        signing_keys=[
            EnrolledKey(kid=k.kid, status=_key_status(k.status), registered_at=iso(k.registered_at))
            for k in keys
        ],
    )


def enrol_institution(
    session: Session,
    ctx: AppContext,
    actor: Actor,
    request: EnrolInstitutionRequest,
    *,
    at: datetime | None = None,
) -> tuple[bool, EnrolledInstitution]:
    """Create the institution; the same code with the same name and type returns it."""
    now = at or ctx.clock.now()
    existing = session.scalars(select(Institution).where(Institution.code == request.code)).first()
    if existing is not None:
        if existing.name != request.name or existing.type != request.type:
            raise ApiError(
                "conflict",
                f"Institution {request.code} is already enrolled with a different name or type.",
            )
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=actor,
            category="configuration",
            action="Re-enrolment matched the existing institution",
            object_type="institution",
            object_ref=request.code,
            institution_code=request.code,
            detail={"name": request.name},
        )
        return False, enrolled_view(session, existing)
    institution = Institution(
        code=request.code,
        name=request.name,
        type=request.type,
        status="enrolled",
        channel=None,
        software_declared=None,
        flags=[],
        supervisor_subject=None,
        portal_users=0,
        portal_mfa=True,
        registered_by=actor.ref,
        registered_at=now,
        live_since=None,
    )
    session.add(institution)
    session.flush()
    for contact in request.contacts:
        session.add(
            InstitutionContact(
                institution_id=institution.id,
                role=contact.role,
                name=contact.name,
                email=contact.email,
            )
        )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="configuration",
        action="Enrolled institution",
        object_type="institution",
        object_ref=request.code,
        institution_code=request.code,
        at=now,
        detail={"name": request.name, "type": request.type, "contacts": len(request.contacts)},
    )
    session.flush()
    return True, enrolled_view(session, institution)


def get_enrolled(session: Session, ctx: AppContext, actor: Actor, code: str) -> EnrolledInstitution:
    institution = _institution(session, code)
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="view",
        action="Read enrolment record",
        object_type="institution",
        object_ref=code,
        institution_code=code,
    )
    return enrolled_view(session, institution)


class EnrolledInstitutionList(BaseModel):
    items: list[EnrolledInstitution]
    next_cursor: str | None


def list_enrolled(
    session: Session, ctx: AppContext, actor: Actor, *, limit: int = 50, cursor: str | None = None
) -> EnrolledInstitutionList:
    """Every institution on the channel, by code; ``cursor`` is the last code seen."""
    query = select(Institution).order_by(Institution.code).limit(limit + 1)
    if cursor:
        query = query.where(Institution.code > cursor)
    rows = list(session.scalars(query))
    page = rows[:limit]
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="view",
        action="Listed enrolled institutions",
        object_type="institution",
        object_ref="*",
        detail={"count": len(page), "after": cursor},
    )
    return EnrolledInstitutionList(
        items=[enrolled_view(session, institution) for institution in page],
        next_cursor=page[-1].code if len(rows) > limit and page else None,
    )


def _slug(name: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z]+", name) if w and w.lower() not in ("and",)]
    initials = "".join(word[0] for word in words).lower()
    if len(initials) >= 3:
        return initials[:3]
    return (words[0][:3] if words else "srg").lower()


def _next_client_id(session: Session, institution: Institution, environment: str) -> str:
    prefix = f"{_slug(institution.name)}-{'prod' if environment == 'production' else 'sbx'}-"
    taken = set(
        session.scalars(select(ApiClient.client_id).where(ApiClient.client_id.like(prefix + "%")))
    )
    number = 1
    while f"{prefix}{number:02d}" in taken:
        number += 1
    return f"{prefix}{number:02d}"


def _same_key(a: ec.EllipticCurvePublicKey, b: ec.EllipticCurvePublicKey) -> bool:
    return a.public_numbers() == b.public_numbers()


@dataclass(frozen=True)
class _ParsedClientRequest:
    csr_key: ec.EllipticCurvePublicKey
    jwks: dict[str, JsonValue]


def _parse_client_request(request: IssueClientRequest) -> _ParsedClientRequest:
    try:
        csr = x509.load_pem_x509_csr(request.csr_pem.encode("ascii"))
    except ValueError as exc:
        raise ApiError(
            "invalid_request", "csr_pem is not a PEM certificate signing request."
        ) from exc
    if not csr.is_signature_valid:
        raise ApiError("invalid_request", "The CSR signature is not valid.")
    csr_key = csr.public_key()
    if not isinstance(csr_key, ec.EllipticCurvePublicKey) or not isinstance(
        csr_key.curve, ec.SECP256R1
    ):
        raise ApiError("invalid_request", "The CSR key must be EC P-256.")
    jwks: dict[str, JsonValue] = {"keys": list(request.jwks.keys)}
    try:
        assertion_keys = [public_key_from_jwk(jwk) for jwk in jwks_keys(jwks)]
    except JwkError as exc:
        raise ApiError("invalid_request", f"jwks: {exc}") from exc
    if not any(_same_key(csr_key, key) for key in assertion_keys):
        raise ApiError(
            "invalid_request",
            "The CSR public key must equal the JWKS key used for client assertions.",
        )
    return _ParsedClientRequest(csr_key=csr_key, jwks=jwks)


def issue_client(
    session: Session,
    ctx: AppContext,
    actor: Actor,
    code: str,
    request: IssueClientRequest,
    *,
    preferred_client_id: str | None = None,
    at: datetime | None = None,
) -> tuple[bool, IssuedClient]:
    """Issue a client certificate from the gateway CA. Re-sending the same CSR key for the
    same environment returns the existing client instead of a duplicate."""
    institution = _institution(session, code)
    parsed = _parse_client_request(request)
    try:
        ca = require_ca(ctx.ca)
    except KeyConfigurationError as exc:
        raise ApiError("conflict", str(exc), status=503) from exc
    chain = pem(ca.certificate())
    for existing in session.scalars(
        select(ApiClient).where(
            ApiClient.institution_id == institution.id,
            ApiClient.environment == request.environment,
            ApiClient.status == "active",
        )
    ):
        cert = x509.load_pem_x509_certificate(existing.certificate_pem.encode("ascii"))
        cert_key = cert.public_key()
        if isinstance(cert_key, ec.EllipticCurvePublicKey) and _same_key(cert_key, parsed.csr_key):
            # A repeated request may declare a newer version of the same software.
            declared = (
                f"{request.software.name} {request.software.version}" if request.software else None
            )
            updated = declared is not None and declared != institution.software_declared
            if updated:
                institution.software_declared = declared
            append(
                session,
                ctx.keys,
                ctx.clock,
                actor=actor,
                category="credential",
                action="Client request matched an existing client",
                object_type="api_client",
                object_ref=existing.client_id,
                institution_code=code,
                detail={"software_declared": declared} if updated else None,
            )
            return False, IssuedClient(
                client_id=existing.client_id,
                certificate_pem=existing.certificate_pem,
                certificate_chain_pem=existing.certificate_pem + chain,
                not_after=iso(existing.certificate_not_after),
                scopes=list(existing.scopes),
            )
    now = at or ctx.clock.now()
    client_id = preferred_client_id or _next_client_id(session, institution, request.environment)
    if (
        session.scalars(select(ApiClient).where(ApiClient.client_id == client_id)).first()
        is not None
    ):
        raise ApiError("conflict", f"Client id {client_id} is taken.")
    certificate = ca.issue(
        parsed.csr_key,
        common_name=client_id,
        organisation=institution.name,
        not_before=now - timedelta(minutes=5),
        not_after=now + timedelta(days=ctx.settings.client_certificate_days),
    )
    described = describe_certificate(certificate)
    scopes = list(INSTITUTION_SCOPES)
    session.add(
        ApiClient(
            client_id=client_id,
            institution_id=institution.id,
            environment=request.environment,
            certificate_pem=described.pem,
            certificate_thumbprint=described.thumbprint,
            certificate_serial=described.serial,
            certificate_not_after=described.not_after,
            jwks=parsed.jwks,
            scopes=scopes,
            status="active",
            created_at=now,
            created_by=actor.ref,
        )
    )
    institution.channel = "api"
    if request.software is not None:
        institution.software_declared = f"{request.software.name} {request.software.version}"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="credential",
        at=now,
        action=f"Issued {request.environment} client certificate",
        object_type="api_client",
        object_ref=client_id,
        institution_code=code,
        detail={
            "environment": request.environment,
            "certificate_sha256": described.thumbprint,
            "not_after": iso(described.not_after),
            "scopes": list(scopes),
        },
    )
    session.flush()
    return True, IssuedClient(
        client_id=client_id,
        certificate_pem=described.pem,
        certificate_chain_pem=described.pem + chain,
        not_after=iso(described.not_after),
        scopes=scopes,
    )


def revoke_client(
    session: Session, ctx: AppContext, actor: Actor, client_id: str, reason: str
) -> None:
    client = session.scalars(select(ApiClient).where(ApiClient.client_id == client_id)).first()
    if client is None:
        raise ApiError("not_found", f"No client {client_id}.")
    institution = session.get(Institution, client.institution_id)
    already = client.status != "active"
    client.status = "revoked"
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="credential",
        action="Client already revoked" if already else "Revoked client certificate",
        object_type="api_client",
        object_ref=client_id,
        institution_code=institution.code if institution else None,
        detail={"reason": reason},
    )


def register_signing_key(
    session: Session,
    ctx: AppContext,
    actor: Actor,
    code: str,
    request: RegisterKeyRequest,
    *,
    at: datetime | None = None,
) -> bool:
    """Register a public signing key. Idempotent on ``kid`` with the same key; a different
    key under an existing ``kid`` is a conflict. Older keys stay as they are."""
    institution = _institution(session, code)
    try:
        key = public_key_from_jwk(request.public_jwk)
    except JwkError as exc:
        raise ApiError("invalid_request", f"public_jwk: {exc}") from exc
    normalised = public_jwk(key, request.kid)
    existing = session.scalars(select(SigningKey).where(SigningKey.kid == request.kid)).first()
    if existing is not None:
        same = existing.institution_id == institution.id and canonicalize(
            {k: v for k, v in existing.public_jwk.items() if k in ("crv", "kty", "x", "y")}
        ) == canonicalize({k: v for k, v in normalised.items() if k in ("crv", "kty", "x", "y")})
        if not same:
            raise ApiError(
                "conflict", f"Key id {request.kid} is already registered with a different key."
            )
        append(
            session,
            ctx.keys,
            ctx.clock,
            actor=actor,
            category="credential",
            action="Signing key registration matched the existing key",
            object_type="signing_key",
            object_ref=request.kid,
            institution_code=code,
        )
        return False
    now = at or ctx.clock.now()
    session.add(
        SigningKey(
            institution_id=institution.id,
            kid=request.kid,
            public_jwk=normalised,
            status="active",
            custody_note=f"Public key registered by the channel operator ({actor.ref}); "
            "the gateway holds only the public key",
            registered_at=now,
            registered_by=actor.ref,
            revoked_at=None,
        )
    )
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="credential",
        at=now,
        action="Registered signing key",
        object_type="signing_key",
        object_ref=request.kid,
        institution_code=code,
        detail={"public_jwk": normalised},
    )
    session.flush()
    return True


def retire_signing_key(
    session: Session, ctx: AppContext, actor: Actor, code: str, kid: str
) -> None:
    institution = _institution(session, code)
    key = session.scalars(
        select(SigningKey).where(SigningKey.kid == kid, SigningKey.institution_id == institution.id)
    ).first()
    if key is None:
        raise ApiError("not_found", f"No signing key {kid} for institution {code}.")
    if key.status == "active":
        key.status = "retired"
        key.revoked_at = ctx.clock.now()
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="credential",
        action="Retired signing key",
        object_type="signing_key",
        object_ref=kid,
        institution_code=code,
        detail={"note": "Filings already signed with this key stay verifiable."},
    )


def set_status(
    session: Session,
    ctx: AppContext,
    actor: Actor,
    code: str,
    request: StatusRequest,
    *,
    at: datetime | None = None,
) -> None:
    """Set live or suspended. Obligations on the channel start from the date it first
    went live; a suspended institution's clients cannot obtain tokens."""
    institution = _institution(session, code)
    now = at or ctx.clock.now()
    previous = institution.status
    institution.status = request.status
    if request.status == "live" and institution.live_since is None:
        institution.live_since = now.date()
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=actor,
        category="configuration",
        action="Suspended institution" if request.status == "suspended" else "Set institution live",
        object_type="institution",
        object_ref=code,
        institution_code=code,
        at=now,
        detail={"from": previous, "to": request.status, "reason": request.reason},
    )


def display_status(
    session: Session, institution: Institution
) -> Literal["enrolled", "live", "suspended"]:
    """``suspended`` when its clients cannot obtain tokens; ``live`` once the operator set it
    live or it has filed; otherwise ``enrolled``."""
    if institution.status == "suspended":
        return "suspended"
    if institution.status == "live":
        return "live"
    filed = session.scalars(
        select(Submission.id)
        .where(Submission.institution_id == institution.id, Submission.environment == "production")
        .limit(1)
    ).first()
    return "live" if filed is not None else "enrolled"
