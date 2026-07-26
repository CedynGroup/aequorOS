"""The attestation workflow — certify → route → certify → unlock submission.

An additive dimension on the existing package lifecycle, so nothing already
proven (supersession, ORASS parity, resubmission, regulator outcomes) has to be
re-proved (docs/attestation_esignature.md §4).

    attestation_state: unsigned → preparer_certified → fully_certified
                                        ↓                    ↓
                                      void ←────────────────┘

Package status and attestation state move together in ONE transaction: T1 also
performs ``validated → pending_approval``, T2 also performs
``pending_approval → approved``. There is deliberately no window in which a
package is approved but uncertified, or certified but unapproved.

What certification freezes, and why four independent mechanisms guard the gap
between the two signatures:

1. the server recomputes ``certification_digest`` at every later signature and
   refuses on any difference from the frozen value;
2. regeneration is refused while a certified, unsubmitted package exists;
3. re-export of a signed artifact kind is refused;
4. both signatures cover the same digest, so a mismatch is provable offline,
   forever, by anyone.

Signatures are strictly append-only. A void does NOT mutate them: it increments
the package's ``attestation_cycle``, so a withdrawn attestation stays legible
(legal register L15) and "current" signatures are those matching the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import AttestationSignature, RegulatoryPackage
from app.services.attestation import digests
from app.services.attestation.policy import SigningPolicy, resolve_policy
from app.services.audit import record_event

GENESIS_HASH = "0" * 64

#: Certification may only begin from a clean validated package: the figures
#: must have passed validation with zero errors before anyone attests to them.
CERTIFIABLE_STATUSES: frozenset[str] = frozenset({"validated"})


class AttestationConflict(HTTPException):
    """A 409 with a stable error code the dashboard can branch on.

    The code is also exposed as a typed attribute: callers and tests should not
    have to index into ``HTTPException.detail``, which upstream types loosely.
    """

    error_code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": code, "message": message},
        )
        self.error_code = code


@dataclass(frozen=True)
class BindingInputs:
    """Everything needed to compute a package's certification digest."""

    binding_class: digests.BindingClass
    content_digest: str
    certification_digest: str
    register_state_digest: str | None
    source_runs: list[dict[str, object]]


def compute_binding(package: RegulatoryPackage) -> BindingInputs:
    """Derive the binding digests from a package's own persisted state.

    Recomputed (never read from a cache) at certification, at every later
    signature, and at verification — that recomputation IS the tamper check.
    """
    source_runs = list(package.source_runs or [])
    binding_class: digests.BindingClass = "engine_run" if source_runs else "master_data"

    content = package.content_digest or digests.content_digest(package.snapshot or {})
    register_state = package.register_state_digest
    if binding_class == "master_data" and not register_state:
        raise AttestationConflict(
            "register_state_missing",
            "This return binds no engine run, so it requires a register-state "
            "digest before it can be certified. Regenerate the pack so its "
            "master-data provenance is recorded.",
        )

    certification = digests.certification_digest(
        organization_id=package.organization_id,
        bank_id=package.bank_id,
        package_id=str(package.id),
        package_version=package.version,
        return_code=package.return_code,
        reporting_date=package.reporting_date.isoformat(),
        basis=package.basis,
        content_digest_value=content,
        binding_class=binding_class,
        source_runs=source_runs if binding_class == "engine_run" else None,
        register_state_digest_value=register_state if binding_class == "master_data" else None,
    )
    return BindingInputs(
        binding_class=binding_class,
        content_digest=content,
        certification_digest=certification,
        register_state_digest=register_state,
        source_runs=source_runs,
    )


def current_signatures(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> list[AttestationSignature]:
    """Signatures belonging to the package's CURRENT attestation cycle."""
    return list(
        db.scalars(
            select(AttestationSignature)
            .where(
                AttestationSignature.organization_id == ctx.organization_id,
                AttestationSignature.package_id == package.id,
                AttestationSignature.attestation_cycle == package.attestation_cycle,
            )
            .order_by(AttestationSignature.created_at, AttestationSignature.id)
        )
    )


def package_policy(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> SigningPolicy:
    return resolve_policy(
        db,
        ctx,
        bank_id=package.bank_id,
        return_code=package.return_code,
        return_family=package.return_family,
        basis=package.basis,
        as_at=package.reporting_date,
    )


def outstanding_slots(
    policy: SigningPolicy, signatures: list[AttestationSignature]
) -> list[tuple[str, int]]:
    """``[(role, still_needed)]`` for every unsatisfied slot."""
    outstanding: list[tuple[str, int]] = []
    for slot in policy.slots:
        have = sum(1 for signature in signatures if signature.signing_role == slot.role)
        if have < slot.min_count:
            outstanding.append((slot.role, slot.min_count - have))
    return outstanding


def is_fully_certified(policy: SigningPolicy, signatures: list[AttestationSignature]) -> bool:
    return not outstanding_slots(policy, signatures)


def chain_head(db: Session, ctx: TenantContext) -> str:
    """The tenant's signature-chain head, or the genesis value.

    A per-tenant hash chain makes removal of a signature detectable even though
    DELETE is reachable by deleting the owning package: the next entry's
    ``prev_hash`` would no longer resolve.
    """
    latest = db.scalars(
        select(AttestationSignature)
        .where(AttestationSignature.organization_id == ctx.organization_id)
        .order_by(AttestationSignature.created_at.desc(), AttestationSignature.id.desc())
        .limit(1)
    ).first()
    return latest.entry_hash if latest is not None else GENESIS_HASH


def chain_entry_hash(*, prev_hash: str, payload_digest: str, signature_value: bytes) -> str:
    """Chain link over the previous head, the signed payload, and the signature."""
    return digests.digest_of(
        {
            "prev_hash": prev_hash,
            "payload_digest": payload_digest,
            "signature_sha256": digests.sha256_hex(signature_value.hex()),
        }
    )


def verify_chain(db: Session, ctx: TenantContext) -> tuple[bool, str | None]:
    """Walk the tenant's signature chain. ``(ok, first_broken_signature_id)``."""
    prev = GENESIS_HASH
    rows = db.scalars(
        select(AttestationSignature)
        .where(AttestationSignature.organization_id == ctx.organization_id)
        .order_by(AttestationSignature.created_at, AttestationSignature.id)
    )
    for row in rows:
        expected = chain_entry_hash(
            prev_hash=prev,
            payload_digest=row.payload_digest,
            signature_value=row.signature_value,
        )
        if row.prev_hash != prev or row.entry_hash != expected:
            return False, str(row.id)
        prev = row.entry_hash
    return True, None


# --- guards -----------------------------------------------------------------


def ensure_certifiable(
    package: RegulatoryPackage, policy: SigningPolicy, role: str
) -> None:
    """Guard the preconditions common to every certification act."""
    if not policy.require_signature:
        raise AttestationConflict(
            "signature_not_required",
            "The signing policy in force for this return does not require a "
            "signature. Configure a policy first if that is wrong.",
        )
    if policy.slot_for(role) is None:
        roles = ", ".join(slot.role for slot in policy.slots) or "none"
        raise AttestationConflict(
            "role_not_in_policy",
            f"The policy in force has no '{role}' slot (it requires: {roles}).",
        )
    if package.attestation_state == "fully_certified":
        raise AttestationConflict(
            "already_fully_certified",
            "Every required signature is already present on this return.",
        )
    if role == "preparer":
        if package.attestation_state != "unsigned":
            raise AttestationConflict(
                "already_certified",
                "This return has already been certified by a preparer. Void the "
                "current attestation to start again.",
            )
        if package.status not in CERTIFIABLE_STATUSES:
            raise AttestationConflict(
                "not_validated",
                "A return must pass validation with zero errors before it can be "
                f"certified (current status: '{package.status}').",
            )
        report = package.validation_report or {}
        if report.get("error_count") or not report.get("passed"):
            raise AttestationConflict(
                "validation_not_clean",
                "Validation has not passed cleanly; the figures cannot be attested to yet.",
            )
    elif package.attestation_state != "preparer_certified":
        raise AttestationConflict(
            "preparer_certification_missing",
            "The preparer must certify this return before an approver can.",
        )


def ensure_maker_checker(  # noqa: PLR0913 - every argument is a distinct SoD input
    package: RegulatoryPackage,
    policy: SigningPolicy,
    signatures: list[AttestationSignature],
    *,
    role: str,
    user_id: UUID,
    signer_id: str,
    job_title: str | None,
) -> None:
    """Segregation of duties, enforced server-side.

    Compared on BOTH the user id and the permanent signer id — the latter
    catches a person who was somehow re-provisioned under a new user row.
    """
    slot = policy.slot_for(role)
    if slot is not None and not slot.accepts_title(job_title):
        expected = ", ".join(slot.officer_titles)
        raise AttestationConflict(
            "officer_title_mismatch",
            f"This return requires a '{role}' signature from one of: {expected}. "
            f"Your recorded job title is {job_title or 'not set'}.",
        )
    if role == "preparer":
        return
    if user_id == package.generated_by:
        raise AttestationConflict(
            "maker_checker",
            "Maker-checker: the person who generated this return cannot approve it.",
        )
    if not policy.distinct_signers:
        return
    for signature in signatures:
        if signature.signer_user_id == user_id or signature.signer_id == signer_id:
            raise AttestationConflict(
                "maker_checker",
                "Maker-checker: each required signature must come from a "
                f"different person (you already signed as '{signature.signing_role}').",
            )


def ensure_digest_unchanged(package: RegulatoryPackage, binding: BindingInputs) -> None:
    """The frozen figures must still be the figures being signed."""
    frozen = package.certification_digest
    if frozen and frozen != binding.certification_digest:
        raise AttestationConflict(
            "figures_changed_since_certification",
            "The figures no longer match what the preparer certified "
            f"(frozen {frozen[:12]}…, current {binding.certification_digest[:12]}…). "
            "This return must be voided and re-certified.",
        )


def ensure_signing_configured() -> None:
    """Refuse the filing path when the deployment cannot sign at all.

    Signing is required by default, so a deployment with no signing key would
    otherwise deadlock: the ceremony refuses (``signing_disabled``) and the
    submission gate refuses (``attestation_incomplete``), and neither message
    tells the operator that the cause is unset configuration rather than a
    colleague who has not signed yet. Discovering that at 10:00 on a filing
    deadline is the failure this exists to prevent, so the error names the
    settings and is distinct from a genuinely outstanding signature.
    """
    gaps = get_settings().attestation.signing_readiness_gaps()
    if not gaps:
        return
    raise AttestationConflict(
        "signing_not_configured",
        "Every return requires signatures, but this deployment cannot produce "
        f"one — {' and '.join(gaps)} must be configured. Until then no return "
        "can be certified or filed. An administrator can also relax the signing "
        "policy for a specific return under Regulatory Reporting → Settings.",
    )


def ensure_submittable(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> None:
    """The submission gate: every required signature must be present.

    Called from the existing submission path, so no return can reach a channel
    without its attestation complete.
    """
    policy = package_policy(db, ctx, package)
    if not policy.require_signature:
        return
    ensure_signing_configured()
    signatures = current_signatures(db, ctx, package)
    missing = outstanding_slots(policy, signatures)
    if missing:
        detail = ", ".join(f"{role} ×{count}" for role, count in missing)
        raise AttestationConflict(
            "attestation_incomplete",
            f"This return cannot be submitted until it is fully certified. Outstanding: {detail}.",
        )
    if package.attestation_state != "fully_certified":
        raise AttestationConflict(
            "attestation_incomplete",
            "This return is not fully certified and cannot be submitted.",
        )
    ensure_digest_unchanged(package, compute_binding(package))


# --- transitions ------------------------------------------------------------


def apply_certification(  # noqa: PLR0913 - transition needs the full context
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    role: str,
    binding: BindingInputs,
    policy: SigningPolicy,
    signatures_after: list[AttestationSignature],
    reason: str | None = None,
) -> None:
    """Move the package's attestation state after a signature is recorded.

    T1 (preparer) freezes the digest and routes for approval; T2 (final
    required signature) completes certification and releases for submission.

    T2 also writes the APPROVAL DECISION, because a checker's signature over the
    frozen figures is their approval of the filing — one act. This is the only
    place ``pending_approval -> approved`` happens by certification, so putting
    the decision row here is what makes "signed but not approved" unreachable
    from any endpoint rather than from one.
    """
    now = datetime.now(UTC)
    if role == "preparer":
        package.attestation_state = "preparer_certified"
        package.certification_digest = binding.certification_digest
        package.content_digest = binding.content_digest
        package.certified_at = now
        # Status and attestation state move together — never a window where a
        # package is certified but not routed.
        if package.status == "validated":
            package.status = "pending_approval"

    if is_fully_certified(policy, signatures_after):
        package.attestation_state = "fully_certified"
        package.fully_certified_at = now
        released = package.status in {"validated", "pending_approval"}
        if released:
            package.status = "approved"
        # Only a CHECKER's signature is an approval decision. A policy that
        # requires the preparer alone still releases the package — that is the
        # institution's own choice — but recording it as a checker decision
        # would attribute an approval to the officer who prepared the return,
        # which is the opposite of what the row is for.
        if released and role != "preparer" and ctx.actor_user_id is not None:
            # Lazily imported: the reporting workflow reaches back into this
            # module, so a module-level import would close the cycle.
            from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415
                record_certification_approval,
            )

            record_certification_approval(
                db, ctx, package, actor_user_id=ctx.actor_user_id, reason=reason
            )

    record_event(
        db,
        ctx,
        event_type=f"attestation.certified_{role}",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "attestation_state": package.attestation_state,
            "certification_digest": binding.certification_digest,
            "binding_class": binding.binding_class,
            "outstanding": [
                {"role": r, "count": c} for r, c in outstanding_slots(policy, signatures_after)
            ],
        },
    )


def void_attestation(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    reason: str,
    commit: bool = True,
) -> RegulatoryPackage:
    """Withdraw the current attestation without destroying any evidence.

    Increments ``attestation_cycle`` so existing signatures stay readable and
    attributable forever; the package returns to ``generated`` for rework.

    ``commit=False`` is for a caller with more to do in the SAME act — sending a
    reviewed return back for corrections both records the decision and withdraws
    the certification that froze the figures, and half of that is a package
    nobody can correct.
    """
    if package.attestation_state == "unsigned":
        raise AttestationConflict(
            "nothing_to_void", "This return has no attestation to void."
        )
    if package.status in {"submitted", "acknowledged"}:
        raise AttestationConflict(
            "already_submitted",
            "This return has already been submitted; use the resubmission path instead.",
        )
    voided_cycle = package.attestation_cycle
    package.attestation_cycle = voided_cycle + 1
    package.attestation_state = "unsigned"
    package.certification_digest = None
    package.certified_at = None
    package.fully_certified_at = None
    package.voided_at = datetime.now(UTC)
    package.void_reason = reason
    if package.status in {"pending_approval", "approved"}:
        package.status = "generated"

    record_event(
        db,
        ctx,
        event_type="attestation.voided",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={"reason": reason, "voided_cycle": voided_cycle},
    )
    if commit:
        db.commit()
    return package
