"""Signer identities — the permanent, opaque per-person signing identifier.

``SGN-XXXXXXXXXXXXXXXX`` (80 bits of Crockford base32) is derived once from the
platform user id and then PERSISTED as the authority. Derivation exists so the
value is reconstructible in a disaster-recovery scenario from the user table
plus the pepper; persistence is what makes it permanent. Rotating the pepper
therefore changes what a *new* user would derive and moves nothing that already
exists (docs/attestation_esignature.md §2.2, §2.3).

Four properties, each load-bearing:

* **Deterministic** — same user id, same pepper generation, same identifier.
* **Opaque** — HMAC output. Encodes no name, email, role, tenant or issue
  order, so an outsider holding a filed PDF learns an identifier and nothing
  else. This opacity is also what lets a name be redacted under Act 843 while
  the signature stays attributable to a determinate person (legal register L10).
* **Globally unique** — one platform-wide unique index. Because the value
  carries no tenant component, two banks cannot correlate their staff.
* **Permanent** — never rewritten, never reissued, retained after
  deprovisioning. ``signer_identities`` is UPDATE-blocked at the database
  level, and ``user_id`` is ``ON DELETE RESTRICT``.

Machines never attest: principals whose ``auth_provider`` is ``service`` (the
integration-key service accounts) are refused an identity, which is what makes
it structurally impossible for an API key to produce a signature.
"""

from __future__ import annotations

import hmac
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import SignerIdentity, User
from app.services.audit import record_event
from app.services.public_ids import (
    SIGNER_ID_LENGTH,
    SIGNER_PUBLIC_ID_PREFIX,
    crockford_encode,
)

#: Bump only alongside a deliberate change to the derivation input format.
DERIVATION_VERSION = 1
_DERIVATION_LABEL = "aeq:signer:v1:"
#: 80 bits — 16 Crockford characters.
_MATERIAL_BYTES = 10
#: Bounded retry on the (astronomically unlikely) collision.
_MAX_COLLISION_ATTEMPTS = 8


class SignerIdentityError(Exception):
    """A signer identity could not be provisioned."""


def _pepper() -> bytes:
    """The server-side derivation key.

    Absent configuration is a hard failure rather than a silent fallback: a
    predictable signer identity would let anyone holding a filed document
    enumerate the platform's user ids.
    """
    pepper = get_settings().attestation.signer_id_pepper
    if not pepper:
        raise SignerIdentityError(
            "SIGNER_ID_PEPPER is not configured; signer identities cannot be "
            "derived without it (see docs/attestation_esignature.md §2.2)."
        )
    return pepper.encode("utf-8")


def derive_signer_id(user_id: UUID, *, collision_counter: int = 0) -> str:
    """Deterministically derive a signer identity from a platform user id."""
    message = f"{_DERIVATION_LABEL}{user_id}"
    if collision_counter:
        message = f"{message}:{collision_counter}"
    material = hmac.new(_pepper(), message.encode("utf-8"), sha256).digest()
    body = crockford_encode(material[:_MATERIAL_BYTES], SIGNER_ID_LENGTH)
    return f"{SIGNER_PUBLIC_ID_PREFIX}-{body}"


def get_signer_identity(db: Session, ctx: TenantContext, user_id: UUID) -> SignerIdentity | None:
    return db.scalar(
        select(SignerIdentity).where(
            SignerIdentity.user_id == user_id,
            SignerIdentity.organization_id == ctx.organization_id,
        )
    )


def ensure_signer_identity(
    db: Session, ctx: TenantContext, user_id: UUID, *, audit: bool = True
) -> SignerIdentity:
    """Provision (idempotently) the permanent signer identity for a user.

    Called from every path that grants a person platform access, so the
    identifier exists before it is ever needed and an operator can print a
    signer roster in advance. Safe to call repeatedly and concurrently: a
    unique-violation is resolved by re-reading the winning row.
    """
    existing = get_signer_identity(db, ctx, user_id)
    if existing is not None:
        return existing

    user = db.scalar(
        select(User).where(
            User.id == user_id,
            User.organization_id == ctx.organization_id,
        )
    )
    if user is None:
        raise SignerIdentityError(f"User {user_id} is not a member of this organization.")
    if user.auth_provider == "service":
        raise SignerIdentityError(
            "Service accounts cannot hold a signer identity — machines do not attest."
        )

    for attempt in range(_MAX_COLLISION_ATTEMPTS):
        candidate = derive_signer_id(user_id, collision_counter=attempt)
        identity = SignerIdentity(
            organization_id=ctx.organization_id,
            user_id=user_id,
            signer_id=candidate,
            derivation_version=DERIVATION_VERSION,
        )
        db.add(identity)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            # Either another request won the race for this user (re-read), or
            # the derived value collided with a different user's (re-derive).
            winner = get_signer_identity(db, ctx, user_id)
            if winner is not None:
                return winner
            continue
        if audit:
            record_event(
                db,
                ctx,
                event_type="signer_identity.provisioned",
                entity_type="signer_identity",
                entity_id=identity.id,
                details={"signer_id": candidate, "user_id": str(user_id)},
            )
        return identity

    raise SignerIdentityError(
        "Could not derive a unique signer identity after "
        f"{_MAX_COLLISION_ATTEMPTS} attempts."
    )


def require_signer_identity(db: Session, ctx: TenantContext, user_id: UUID) -> SignerIdentity:
    """The signing path's accessor: an identity is mandatory to sign.

    Carries a typed ``error_code`` because the signing UI must be able to tell a
    *deployment* problem from a *credential* problem. Without one this arrived at
    the browser as an untyped refusal and rendered above the password field,
    which reads as "your password was rejected" — sending the signer to re-type a
    password that was never checked and could not have helped.
    """
    try:
        return ensure_signer_identity(db, ctx, user_id)
    except SignerIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_signer_identity",
                "message": f"No signer identity is available for this user: {exc}",
            },
        ) from exc


def resolve_signer_display(
    db: Session, ctx: TenantContext, user_id: UUID
) -> tuple[str | None, str | None]:
    """``(display_name, job_title)`` snapshotted into a signature at signing.

    Stored on the signature record so attribution survives deprovisioning,
    renaming, and Act 843 redaction of the live user row.
    """
    row = db.execute(
        select(User.display_name, User.job_title).where(
            User.id == user_id,
            User.organization_id == ctx.organization_id,
        )
    ).one_or_none()
    if row is None:
        return None, None
    return row[0], row[1]
