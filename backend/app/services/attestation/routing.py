"""Named recipient routing — the policy's roles, filled by actual people.

``return_signing_policies`` says "one preparer and one approver, and the approver
must hold one of these officer titles". That is the control. It says nothing about
*who*, which is why a preparer who finishes a return has no way to tell a
particular colleague it is waiting for them.

This module adds the missing half and is careful to ADD it. A recipient row
satisfies a policy slot; it never stands in for one. When the nominee actually
signs, every guard in ``attestation.workflow`` runs unchanged —
``ensure_certifiable``, ``ensure_digest_unchanged``, ``ensure_maker_checker``,
``ensure_signing_configured`` — and the submission gate is still
``ensure_submittable``. Nomination is validated by calling the very same
``ensure_maker_checker`` against the nominee's identity and prospective role, so
"can this person satisfy this slot?" is answered by one implementation rather than
two that could drift.

Two consequences worth stating plainly:

* Nomination is **early enforcement**, not extra permission. Anything refused at
  signing time is refused at nomination time, so a preparer learns immediately
  that the colleague they picked cannot approve their own return.
* Once a role has been routed, only the named people may fill it
  (:func:`ensure_routed_signer`). A nomination the audit trail cannot rely on
  would be decoration; and because a nominee can be unavailable, the routing is
  re-assignable by an approver (reason-required) rather than only escapable
  through a void.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core import security
from app.models import (
    AttestationSignature,
    PackageSignatureRecipient,
    RegulatoryPackage,
    User,
)
from app.services import notifications
from app.services.attestation import workflow
from app.services.attestation.identity import ensure_signer_identity
from app.services.attestation.policy import SigningPolicy
from app.services.attestation.workflow import AttestationConflict
from app.services.audit import record_event

#: Slots whose holder must carry the approver platform role. Mirrors
#: ``attestation_api.CHECKER_ROLES``: a nominee who could not pass the route's own
#: gate must not be routed, or the preparer would be told at signing time that the
#: person they nominated was never eligible.
CHECKER_ROLES: frozenset[str] = frozenset({"approver", "board"})


@dataclass(frozen=True)
class Nomination:
    """One person put forward for one signing slot, in routing order."""

    signing_role: str
    user_id: UUID


def current_recipients(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[PackageSignatureRecipient]:
    """Recipients belonging to the package's CURRENT attestation cycle."""
    return list(
        db.scalars(
            select(PackageSignatureRecipient)
            .where(
                PackageSignatureRecipient.organization_id == ctx.organization_id,
                PackageSignatureRecipient.package_id == package.id,
                PackageSignatureRecipient.attestation_cycle == package.attestation_cycle,
            )
            .order_by(
                PackageSignatureRecipient.routing_order,
                PackageSignatureRecipient.created_at,
            )
        )
    )


def route(  # noqa: PLR0913 - one act over the full nomination context
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    policy: SigningPolicy,
    nominations: Sequence[Nomination],
    signatures: Sequence[AttestationSignature],
    reason: str,
) -> list[PackageSignatureRecipient]:
    """Validate and record the routing for every slot still outstanding.

    ``signatures`` must be the package's signatures AS THEY WILL STAND once this
    transaction commits — including the preparer's, when this runs inside
    "certify and send". That is what lets ``ensure_maker_checker`` catch a
    preparer nominating themselves without this module knowing anything about
    segregation of duties.
    """
    outstanding = dict(workflow.outstanding_slots(policy, list(signatures)))
    _require_covers_outstanding(nominations, outstanding)

    rows: list[PackageSignatureRecipient] = []
    seen: set[UUID] = set()
    for order, nomination in enumerate(nominations, start=1):
        if nomination.user_id in seen:
            # Not delegated to ensure_maker_checker: that function compares a
            # candidate against SIGNATURES, and neither of two nominees has one
            # yet. Distinctness within the list is this module's own invariant.
            raise AttestationConflict(
                "recipient_duplicated",
                "The same person is nominated for more than one outstanding signature. "
                "Each required signature must come from a different person.",
            )
        seen.add(nomination.user_id)
        rows.append(
            _nominate(
                db,
                ctx,
                package,
                policy=policy,
                nomination=nomination,
                signatures=signatures,
                order=order,
            )
        )

    record_event(
        db,
        ctx,
        event_type="attestation.routed_for_signature",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "reason": reason,
            "attestation_cycle": package.attestation_cycle,
            "recipients": [
                {
                    "signing_role": row.signing_role,
                    "recipient_signer_id": row.recipient_signer_id,
                    "routing_order": row.routing_order,
                }
                for row in rows
            ],
        },
    )
    for row in rows:
        _notify(db, ctx, package, row)
    db.flush()
    return rows


def reroute(  # noqa: PLR0913 - replaces a routing, so needs the full context
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    policy: SigningPolicy,
    nominations: Sequence[Nomination],
    signatures: Sequence[AttestationSignature],
    reason: str,
) -> list[PackageSignatureRecipient]:
    """Re-assign the outstanding slots — the escape hatch for an absent nominee.

    Without this, :func:`ensure_routed_signer` would make an unavailable approver
    a dead end escapable only by voiding a certified return: heavy, and it
    discards a perfectly good signature. Rows already marked ``signed`` are left
    alone; a completed signature is not re-assignable.
    """
    existing = current_recipients(db, ctx, package)
    for row in existing:
        if row.status == "pending":
            db.delete(row)
    db.flush()
    return route(
        db,
        ctx,
        package,
        policy=policy,
        nominations=nominations,
        signatures=signatures,
        reason=reason,
    )


def ensure_routed_signer(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    user_id: UUID,
) -> None:
    """When a role has been routed, only a named recipient may fill it.

    Silent when nothing was routed, so every pre-existing ceremony (and every
    return whose institution does not use routing) behaves exactly as before.
    This is a gate that only exists where somebody chose to create it.
    """
    routed = [
        row
        for row in current_recipients(db, ctx, package)
        if row.signing_role == role and row.status == "pending"
    ]
    if not routed:
        return
    if any(row.recipient_user_id == user_id for row in routed):
        return
    named = ", ".join(row.recipient_display_name or row.recipient_signer_id for row in routed)
    raise AttestationConflict(
        "not_the_named_signer",
        f"This return was sent to {named} for the '{role}' signature. Have them sign, or "
        f"re-assign the signature to somebody else with a recorded reason — signing "
        f"around a nomination would leave a routing record the audit trail contradicts.",
    )


def mark_signed(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    signature: AttestationSignature,
) -> None:
    """Close the recipient row the signature satisfied, if there is one."""
    for row in current_recipients(db, ctx, package):
        if (
            row.status == "pending"
            and row.signing_role == signature.signing_role
            and row.recipient_user_id == signature.signer_user_id
        ):
            row.status = "signed"
            row.signed_at = datetime.now(UTC)
            row.signature_id = signature.id
            db.flush()
            return


def awaiting_signature(
    db: Session, ctx: TenantContext, user_id: UUID
) -> list[tuple[PackageSignatureRecipient, RegulatoryPackage]]:
    """Returns routed to this person and still unsigned, newest first.

    Joined on the package's CURRENT cycle: a nomination from a cycle that was
    later voided is history, not an outstanding request, and must not sit in
    somebody's queue forever.
    """
    rows = db.execute(
        select(PackageSignatureRecipient, RegulatoryPackage)
        .join(
            RegulatoryPackage,
            (RegulatoryPackage.id == PackageSignatureRecipient.package_id)
            & (RegulatoryPackage.organization_id == PackageSignatureRecipient.organization_id),
        )
        .where(
            PackageSignatureRecipient.organization_id == ctx.organization_id,
            PackageSignatureRecipient.recipient_user_id == user_id,
            PackageSignatureRecipient.status == "pending",
            PackageSignatureRecipient.attestation_cycle == RegulatoryPackage.attestation_cycle,
        )
        .order_by(RegulatoryPackage.reporting_date.desc(), RegulatoryPackage.return_code)
    ).all()
    return [(recipient, package) for recipient, package in rows]


# --- internals --------------------------------------------------------------


def _require_covers_outstanding(
    nominations: Sequence[Nomination], outstanding: dict[str, int]
) -> None:
    """Every outstanding slot must be named exactly as many times as it needs.

    Refused rather than partially accepted: "certify and send" that left a slot
    unrouted would produce a return nobody had been asked to sign, sitting in no
    queue, which is the failure the whole feature exists to remove.
    """
    counted: dict[str, int] = {}
    for nomination in nominations:
        counted[nomination.signing_role] = counted.get(nomination.signing_role, 0) + 1
    if counted == outstanding:
        return
    expected = _describe(outstanding)
    given = _describe(counted)
    raise AttestationConflict(
        "recipients_do_not_match_policy",
        f"The signing policy in force still needs: {expected}. This request nominates: "
        f"{given}. Name a signer for every outstanding signature and no others.",
    )


def _describe(counts: dict[str, int]) -> str:
    return ", ".join(f"{role} ×{count}" for role, count in sorted(counts.items())) or "none"


def _nominate(  # noqa: PLR0913 - one nominee against the full policy context
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    policy: SigningPolicy,
    nomination: Nomination,
    signatures: Sequence[AttestationSignature],
    order: int,
) -> PackageSignatureRecipient:
    user = db.scalar(
        select(User).where(
            User.id == nomination.user_id,
            User.organization_id == ctx.organization_id,
        )
    )
    if user is None:
        raise AttestationConflict(
            "recipient_unknown",
            "One of the nominated signers is not a user of this institution.",
        )
    if not user.is_active:
        raise AttestationConflict(
            "recipient_inactive",
            f"{user.display_name or user.email} is deactivated and cannot sign.",
        )
    # Machines do not attest (§2.4), and a service account holding an analyst role
    # would otherwise look like a perfectly good nominee.
    if user.auth_provider == "service":
        raise AttestationConflict(
            "recipient_not_human",
            "A service account cannot be nominated to sign a return.",
        )
    if policy.slot_for(nomination.signing_role) is None:
        roles = ", ".join(slot.role for slot in policy.slots) or "none"
        raise AttestationConflict(
            "recipient_role_not_in_policy",
            f"The policy in force has no '{nomination.signing_role}' slot "
            f"(it requires: {roles}).",
        )
    if nomination.signing_role in CHECKER_ROLES and not security.has_role(
        [user.role], "approver"
    ):
        raise AttestationConflict(
            "recipient_role_insufficient",
            f"{user.display_name or user.email} holds the '{user.role}' role, which cannot "
            f"provide the '{nomination.signing_role}' signature — maker-checker cannot be "
            f"satisfied by a preparer.",
        )

    identity = ensure_signer_identity(db, ctx, nomination.user_id)
    # The single source of truth for "may this person fill this slot": officer
    # title, the generated_by control, and distinct-signers all come from here.
    workflow.ensure_maker_checker(
        package,
        policy,
        list(signatures),
        role=nomination.signing_role,
        user_id=nomination.user_id,
        signer_id=identity.signer_id,
        job_title=user.job_title,
    )

    row = PackageSignatureRecipient(
        organization_id=ctx.organization_id,
        package_id=package.id,
        attestation_cycle=package.attestation_cycle,
        signing_role=nomination.signing_role,
        recipient_user_id=nomination.user_id,
        recipient_signer_id=identity.signer_id,
        recipient_display_name=user.display_name,
        recipient_job_title=user.job_title,
        routing_order=order,
        status="pending",
        nominated_by=ctx.actor_user_id,
    )
    db.add(row)
    db.flush()
    return row


def _notify(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    row: PackageSignatureRecipient,
) -> None:
    notifications.emit(
        db,
        ctx,
        type="attestation.signature_requested",
        severity="warning",
        title=(
            f"{package.return_code} {package.reporting_date.isoformat()} awaits your signature"
        ),
        body=(
            f"You have been asked to provide the '{row.signing_role}' signature on version "
            f"{package.version} of {package.return_code} for "
            f"{package.reporting_date.isoformat()} ({package.basis})."
        ),
        entity_type="regulatory_package",
        entity_id=package.id,
        recipient_user_id=row.recipient_user_id,
    )
    row.notified_at = datetime.now(UTC)


__all__ = [
    "CHECKER_ROLES",
    "Nomination",
    "awaiting_signature",
    "current_recipients",
    "ensure_routed_signer",
    "mark_signed",
    "reroute",
    "route",
]
