"""Step-up re-authentication and single-use signing authorisations.

The platform had no step-up concept at all: a 15-minute bearer token was the
only thing between a stolen session and an approval (gap G5). Signing must be
harder than browsing, for one specific legal reason — Act 772 requires the
signature to be created by means under the signatory's control, and a
server-held key used on the strength of an ambient session token is a weak
answer to that. This module is the strongest control we can offer without
moving keys onto signer-held hardware, and the honest limits of that argument
are recorded as legal-review item L1 (docs/attestation_esignature.md §7).

Two steps, deliberately separate:

1. **Step-up** — the signer proves presence *now*, not at session start:
   a fresh OIDC id_token from the bank's own IdP (``prompt=login``/``max_age=0``,
   whose ``auth_time`` we check), or a password re-entry for password accounts.
   Whatever the IdP asserts about assurance (``acr``/``amr``) is captured
   verbatim into the signature record — we do not invent it.

2. **Authorisation** — a single-use token bound to
   ``(user, package, certification_digest, signing_role)`` with a short TTL.
   Only its hash is stored. It is consumed atomically at signing, so it cannot
   be replayed, cannot be used for a different package, and cannot survive the
   figures changing underneath it.

Service accounts can never step up: they have no signer identity, and the
integration-key principal carries the ``analyst`` role only.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.core.config import get_settings
from app.models import SigningAuthorization, SsoConnection, User
from app.services.audit import record_event
from app.services.sso_config import find_enabled_by_issuer_audience

#: How recent an IdP authentication must be to count as step-up. Wider than the
#: authorisation TTL because IdP round-trips include user interaction.
MAX_AUTH_AGE = timedelta(minutes=5)


class StepUpRequired(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "step_up_required", "message": message},
        )


class StepUpFailed(HTTPException):
    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "step_up_failed", "message": message},
        )


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_step_up(  # noqa: PLR0913 - two distinct proof methods + metadata
    db: Session,
    ctx: TenantContext,
    user_id: UUID,
    *,
    id_token: str | None = None,
    password: str | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove the signer is present now. Returns the evidence to record.

    The returned dict is stored verbatim on the signature; it is evidence about
    *how* the person authenticated, which is exactly what an attribution
    question later turns on.
    """
    user = db.scalar(
        select(User).where(
            User.id == user_id,
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise StepUpFailed("This account is not active in the institution.")
    if user.auth_provider == "service":
        raise StepUpFailed("Service accounts cannot sign — machines do not attest.")

    evidence: dict[str, Any] = {
        "method": None,
        "verified_at": datetime.now(UTC).isoformat(),
        **(request_metadata or {}),
    }

    if id_token:
        connection = find_enabled_by_issuer_audience_for(db, ctx, id_token)
        claims = security.verify_oidc_id_token(
            id_token, issuer=connection.issuer, audience=connection.client_id
        )
        if claims.get("sub") != user.sso_subject:
            raise StepUpFailed(
                "The re-authenticated identity does not match the signed-in user."
            )
        auth_time = claims.get("auth_time")
        if auth_time is not None:
            authenticated = datetime.fromtimestamp(int(auth_time), tz=UTC)
            if datetime.now(UTC) - authenticated > MAX_AUTH_AGE:
                raise StepUpFailed(
                    "The identity provider reported an older sign-in than this "
                    "action requires. Re-authenticate and try again."
                )
            evidence["auth_time"] = authenticated.isoformat()
        # Assurance claims are recorded as the IdP asserted them — never
        # synthesised, because their whole value is that we did not author them.
        for claim in ("acr", "amr", "idp", "iss"):
            if claim in claims:
                evidence[claim] = claims[claim]
        evidence["method"] = "oidc_reauth"
        return evidence

    if password:
        if not user.password_hash or not security.verify_password(password, user.password_hash):
            raise StepUpFailed("Re-authentication failed.")
        evidence["method"] = "password_reauth"
        return evidence

    raise StepUpRequired(
        "Signing requires re-authentication. Confirm your identity to continue."
    )


def find_enabled_by_issuer_audience_for(
    db: Session, ctx: TenantContext, id_token: str
) -> SsoConnection:
    """Resolve the org's SSO connection for a presented token.

    Issuer and audience come from the STORED connection, never from the token —
    the same rule the login path follows.
    """
    unverified = security.unverified_claims(id_token)
    issuer = str(unverified.get("iss", ""))
    audience = unverified.get("aud")
    audience_value = audience[0] if isinstance(audience, list) and audience else audience
    connection = find_enabled_by_issuer_audience(
        db, issuer=issuer, audience=str(audience_value or "")
    )
    if connection is None or connection.organization_id != ctx.organization_id:
        raise StepUpFailed("No enabled identity provider matches this token.")
    return connection


def mint_authorization(  # noqa: PLR0913 - the binding tuple IS the argument list
    db: Session,
    ctx: TenantContext,
    *,
    user_id: UUID,
    signer_id: str,
    package_id: UUID,
    signing_role: str,
    certification_digest: str,
    auth_evidence: dict[str, Any],
) -> tuple[str, SigningAuthorization]:
    """Issue a single-use signing authorisation. The raw token is returned once."""
    raw = secrets.token_urlsafe(32)
    ttl = get_settings().attestation.authorization_ttl_seconds
    row = SigningAuthorization(
        organization_id=ctx.organization_id,
        user_id=user_id,
        signer_id=signer_id,
        package_id=package_id,
        signing_role=signing_role,
        certification_digest=certification_digest,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        auth_evidence=auth_evidence,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="attestation.step_up_authorized",
        entity_type="regulatory_package",
        entity_id=package_id,
        details={
            "signing_role": signing_role,
            "signer_id": signer_id,
            "method": auth_evidence.get("method"),
            "expires_at": row.expires_at.isoformat(),
        },
    )
    db.commit()
    return raw, row


def consume_authorization(  # noqa: PLR0913 - every field must be matched exactly
    db: Session,
    ctx: TenantContext,
    *,
    token: str,
    user_id: UUID,
    package_id: UUID,
    signing_role: str,
    certification_digest: str,
) -> SigningAuthorization:
    """Validate and burn an authorisation. Every field must match exactly.

    The ``certification_digest`` check is what stops an authorisation obtained
    for one set of figures from being spent on another: if the figures moved
    between step-up and signing, this refuses.
    """
    row = db.scalar(
        select(SigningAuthorization).where(
            SigningAuthorization.token_hash == _hash_token(token),
            SigningAuthorization.organization_id == ctx.organization_id,
        )
    )
    if row is None:
        raise StepUpFailed("This signing authorisation is not recognised.")
    if row.consumed_at is not None:
        raise StepUpFailed("This signing authorisation has already been used.")

    expires_at = row.expires_at
    if expires_at.tzinfo is None:  # sqlite returns naive datetimes
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) > expires_at:
        raise StepUpFailed("This signing authorisation has expired. Re-authenticate to sign.")

    if (
        row.user_id != user_id
        or row.package_id != package_id
        or row.signing_role != signing_role
    ):
        raise StepUpFailed("This signing authorisation was issued for a different action.")
    if row.certification_digest != certification_digest:
        raise StepUpFailed(
            "The figures changed after you re-authenticated. Review the return and sign again."
        )

    row.consumed_at = datetime.now(UTC)
    db.flush()
    return row
