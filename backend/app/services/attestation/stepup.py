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
   whose ``auth_time`` is REQUIRED and checked), or a password re-entry for
   password accounts. Whatever the IdP asserts about assurance (``acr``/``amr``)
   is captured verbatim into the signature record — we do not invent it.

   Password re-entry is throttled through :mod:`app.services.auth_throttle`,
   which shares ``users.failed_login_attempts`` / ``users.locked_until`` with
   the sign-in path. Until 2026-08-21 it was not: an authenticated analyst could
   guess an approver's password without limit against the one endpoint that
   mints a signing authorisation (audit finding P0-4).

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
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.core.config import get_settings
from app.models import SigningAuthorization, SsoConnection, User
from app.services import auth_throttle
from app.services.audit import record_event
from app.services.sso_config import find_enabled_by_issuer_audience

logger = logging.getLogger(__name__)

#: How recent an IdP authentication must be to count as step-up. Wider than the
#: authorisation TTL because IdP round-trips include user interaction.
MAX_AUTH_AGE = timedelta(minutes=5)

#: One message for every way a password re-entry can fail — wrong password, no
#: password on the account, no such membership. The account is the caller's own
#: (the actor comes from the verified token, never the request body), so this is
#: not an enumeration surface; uniform wording plus the equalising Argon2 burn
#: below keeps it from becoming one if that ever changes.
_REAUTH_FAILED = "Re-authentication failed."


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


class StepUpLocked(HTTPException):
    """Too many wrong passwords. 423 mirrors the sign-in path's own lockout."""

    def __init__(self, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_423_LOCKED,
            detail={"error_code": "step_up_locked", "message": message},
        )


def _locked_message(expiry: datetime) -> str:
    minutes = auth_throttle.minutes_remaining(expiry)
    plural = "" if minutes == 1 else "s"
    return (
        f"Too many failed password attempts. This account is locked for the next "
        f"{minutes} minute{plural} — for signing and for sign-in. If you cannot "
        f"wait, ask an administrator to reset your password."
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
        # The actor's own membership went away mid-session. The API's auth
        # dependency already refuses an inactive user, so this is defence in
        # depth — but it answers exactly like a wrong password, and pays the
        # same Argon2id cost, so the two are indistinguishable from outside.
        if password:
            auth_throttle.burn_password_check(password)
        raise StepUpFailed(_REAUTH_FAILED)
    if user.auth_provider == "service":
        raise StepUpFailed("Service accounts cannot sign — machines do not attest.")

    evidence: dict[str, Any] = {
        "method": None,
        "verified_at": datetime.now(UTC).isoformat(),
        **(request_metadata or {}),
    }

    if id_token:
        _verify_oidc_proof(db, ctx, user, id_token, evidence)
        return evidence

    if password:
        _verify_password_proof(db, user, password)
        evidence["method"] = "password_reauth"
        return evidence

    raise StepUpRequired("Signing requires re-authentication. Confirm your identity to continue.")


def _verify_oidc_proof(
    db: Session,
    ctx: TenantContext,
    user: User,
    id_token: str,
    evidence: dict[str, Any],
) -> None:
    """A fresh IdP assertion. Deliberately NOT gated on the password lockout.

    An id_token is an RS256/ES256 assertion verified against the IdP's JWKS —
    there is no secret here to guess, so the throttle would buy nothing, while
    letting someone else's failed guessing at the sign-in page deny an approver
    the ability to file. Freshness is this path's control.
    """
    try:
        connection = find_enabled_by_issuer_audience_for(db, ctx, id_token)
        claims = security.verify_oidc_id_token(
            id_token, issuer=connection.issuer, audience=connection.client_id
        )
    except security.AuthError as exc:
        # Every way the token itself can be unacceptable — malformed, wrong
        # signature, expired, or an issuer the egress guard refuses to reach —
        # arrives here as an AuthError. Uncaught it was a 500: an ordinary
        # invalid id_token read as "the platform broke" rather than "that proof
        # was not accepted", and the signer got no route to the password path.
        # Reported exactly like a failed password re-entry, and for the same
        # reason: the detail is the IdP's business, not the browser's.
        logger.warning("Step-up OIDC proof rejected: %s: %s", type(exc).__name__, exc)
        raise StepUpFailed(_REAUTH_FAILED) from exc
    if claims.get("sub") != user.sso_subject:
        raise StepUpFailed("The re-authenticated identity does not match the signed-in user.")
    # REQUIRED, not optional. The whole purpose of this path is to prove presence
    # *now*, and the only evidence of "now" is auth_time. We ask for it correctly
    # (the step-up redirect sends max_age=0, which OIDC Core §3.1.2.1 makes
    # auth_time mandatory in the response), so a token without it is either a
    # non-conforming IdP or a token minted for some other purpose — and treating
    # an absent claim as "fresh enough" made the check decorative.
    auth_time = claims.get("auth_time")
    if auth_time is None:
        raise StepUpFailed(
            "Your identity provider did not report when you authenticated, so "
            "this signature cannot be tied to a fresh sign-in. Sign with your "
            "password instead, or ask your administrator to enable the "
            "auth_time claim."
        )
    # A token can pass signature verification and still carry a junk auth_time
    # (a string, a bool, or a value outside the epoch range). `int()` and
    # `fromtimestamp` both raise on those, and an unhandled raise here is a 500 —
    # the caller sees a server fault for what is squarely a refusal.
    try:
        authenticated = datetime.fromtimestamp(int(auth_time), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        logger.warning("Step-up OIDC proof rejected: unusable auth_time %r: %s", auth_time, exc)
        raise StepUpFailed(
            "Your identity provider reported a sign-in time this system could "
            "not read, so this signature cannot be tied to a fresh sign-in. "
            "Sign with your password instead, or ask your administrator to "
            "check the auth_time claim."
        ) from exc
    if datetime.now(UTC) - authenticated > MAX_AUTH_AGE:
        raise StepUpFailed(
            "The identity provider reported an older sign-in than this action "
            "requires. Re-authenticate and try again."
        )
    evidence["auth_time"] = authenticated.isoformat()
    # Assurance claims are recorded as the IdP asserted them — never synthesised,
    # because their whole value is that we did not author them.
    for claim in ("acr", "amr", "idp", "iss"):
        if claim in claims:
            evidence[claim] = claims[claim]
    evidence["method"] = "oidc_reauth"


def _verify_password_proof(db: Session, user: User, password: str) -> None:
    """Throttled password re-entry. Returns only when the password was right."""
    # Fail closed, and check the lock BEFORE the hash: a locked account cannot
    # step up even with the right password, or the lockout would only be slowing
    # an attacker down rather than stopping them.
    expiry = auth_throttle.lock_expiry(user)
    if expiry is not None:
        raise StepUpLocked(_locked_message(expiry))
    if not user.password_hash:
        # An SSO-only account. Burn the equalising verification so "this account
        # has no password" is not readable off the clock, then count it: an
        # attacker must not get a free, unmetered oracle here either.
        auth_throttle.burn_password_check(password)
        _refuse_password(db, user)
    elif not security.verify_password(password, user.password_hash):
        _refuse_password(db, user)
    auth_throttle.record_success(db, user)


def _refuse_password(db: Session, user: User) -> None:
    """Record the failed attempt durably, then refuse. Never returns.

    The counter is committed before the refusal is raised — a failure the
    transaction rolls back is a failure that never happened, which is precisely
    the hole P0-4 described.
    """
    expiry = auth_throttle.record_failure(db, user)
    if expiry is not None:
        raise StepUpLocked(_locked_message(expiry))
    raise StepUpFailed(_REAUTH_FAILED)


def find_enabled_by_issuer_audience_for(
    db: Session, ctx: TenantContext, id_token: str
) -> SsoConnection:
    """Resolve the org's SSO connection for a presented token.

    Issuer and audience come from the STORED connection, never from the token —
    the same rule the login path follows.
    """
    unverified = security.unverified_claims(id_token)
    issuer = str(unverified.get("iss", ""))
    connection = find_enabled_by_issuer_audience(
        db, issuer=issuer, audience=unverified.get("aud", "")
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

    if row.user_id != user_id or row.package_id != package_id or row.signing_role != signing_role:
        raise StepUpFailed("This signing authorisation was issued for a different action.")
    if row.certification_digest != certification_digest:
        raise StepUpFailed(
            "The figures changed after you re-authenticated. Review the return and sign again."
        )

    # Burn it as a CONDITIONAL update, not a read-then-write. Two concurrent
    # certifications used to both read ``consumed_at IS NULL`` and both write
    # it, spending one authorisation twice — two signatures from one act of
    # presence. Making the burn itself the race means exactly one statement can
    # match, and the loser is refused. Still uncommitted: if the signature that
    # follows fails, the rollback correctly hands the authorisation back.
    burned_at = datetime.now(UTC)
    burned = cast(
        "CursorResult[Any]",
        db.execute(
            update(SigningAuthorization)
            .where(
                SigningAuthorization.id == row.id,
                SigningAuthorization.organization_id == ctx.organization_id,
                SigningAuthorization.consumed_at.is_(None),
            )
            .values(consumed_at=burned_at)
            .execution_options(synchronize_session=False)
        ),
    )
    if burned.rowcount != 1:
        raise StepUpFailed("This signing authorisation has already been used.")
    # Let the object re-read what the statement wrote rather than marking it
    # dirty for a second, redundant UPDATE at the next flush.
    db.expire(row, ["consumed_at"])
    return row
