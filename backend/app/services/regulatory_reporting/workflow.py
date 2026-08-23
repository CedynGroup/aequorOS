"""Package lifecycle state machine (docs/regulatory_reporting.md §2, §5).

Explicit allowed-transition table; every transition is audit-logged via
``record_event`` and submission-bound transitions additionally append a
``RegulatorySubmissionEvent``. Maker-checker: the approval decision must come
from a different user than the package generator (409 otherwise).

Channel dispatch (this wave): ``submit_package_via_channel`` resolves the
channel (override or registry default), assembles the filing set (``_filing_set``
— the signed revision once officers have certified, plus the registry's required
template format, auto-exported through the lazy exporter seam when absent),
delegates to the concrete channel plugin, and records the outcome — including
exactly which files went. ``poll_submission`` maps the latest
external_ref onto the regulator-side status and records regulator decisions.
Downtime semantics (BoG Notice BG/FMD/2026/07): an email fallback submission
carries ``{"pending_orass_reupload": true}`` and is deemed complete only
after the subsequent ORASS re-upload — the one narrow case where
``submitted -> submitted`` is allowed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    RegulatoryPackage,
    RegulatoryPackageApproval,
    RegulatoryPackageArtifact,
    RegulatoryResubmissionRequest,
    RegulatorySubmissionEvent,
)
from app.schemas.regulatory_reporting import (
    EmailFallbackInstructionsRead,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryArtifactRead,
    RegulatoryPackageRead,
    ResubmissionRequestCreate,
    ResubmissionRequestListRead,
    ResubmissionRequestRead,
    SubmissionEventListRead,
    SubmissionEventRead,
    SubmissionPollRead,
)
from app.services import filing_reconciliation, institution_profile, notifications
from app.services.audit import record_event
from app.services.regulatory_reporting import artifact_versions
from app.services.regulatory_reporting.channel_config import (
    channel_config_row,
    decrypt_channel_credentials,
)
from app.services.regulatory_reporting.channels import (
    ChannelDowntimeError,
    ChannelError,
    ChannelPreconditionError,
    EmailFallbackChannel,
    FiledArtifact,
    OrassApiChannel,
    OrassSandboxChannel,
    build_email_bundle,
)
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
    require_actor,
)
from app.services.regulatory_reporting.registry import get_definition

# §2 lifecycle. "generated" is re-entered on approval rejection (rework) and
# on a failed re-validation; "superseded" is reachable from any non-terminal
# state via regeneration (enforced in generation.py, listed here for audit).
# "submitted -> submitted" exists ONLY for the BG/FMD/2026/07 re-upload of a
# downtime email submission via ORASS; submit_package_via_channel guards it
# (prior channel must be email with pending_orass_reupload still set).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"generated", "superseded"}),
    "generated": frozenset({"validated", "superseded"}),
    "validated": frozenset({"pending_approval", "generated", "superseded"}),
    "pending_approval": frozenset({"approved", "generated", "superseded"}),
    "approved": frozenset({"submitted", "superseded"}),
    # Regulator outcomes (ORASS parity): "rejected" is returned-for-correction
    # (rework via a superseding version), "declined" is the final refusal.
    "submitted": frozenset({"acknowledged", "rejected", "declined", "submitted"}),
    # Acknowledged is terminal for status transitions; a correction after
    # acknowledgement requires a GRANTED resubmission request, which authorizes
    # the superseding regeneration (enforced in generation.py — supersession
    # is a direct regeneration effect, not a table transition).
    "acknowledged": frozenset(),
    "rejected": frozenset({"superseded"}),
    "declined": frozenset({"superseded"}),
    "superseded": frozenset(),
}

type ArtifactKind = Literal["xlsx", "csv", "pdf", "xlsx_working"]
type Exporter = Callable[
    [Session, TenantContext, RegulatoryPackage, ArtifactKind], RegulatoryPackageArtifact
]


def ensure_transition_allowed(package: RegulatoryPackage, new_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(package.status, frozenset())
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"A package in status '{package.status}' cannot transition to '{new_status}'."),
        )


def transition(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    new_status: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    """Apply one allowed status transition and audit it (no commit)."""
    ensure_transition_allowed(package, new_status)
    previous = package.status
    package.status = new_status
    record_event(
        db,
        ctx,
        event_type="regulatory_package.status_changed",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "version": package.version,
            "from_status": previous,
            "to_status": new_status,
            **(details or {}),
        },
    )


def _add_approval(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    action: str,
    actor_user_id: UUID,
    reason: str | None,
) -> RegulatoryPackageApproval:
    approval = RegulatoryPackageApproval(
        organization_id=package.organization_id,
        package_id=package.id,
        action=action,
        actor_user_id=actor_user_id,
        reason=reason,
        occurred_at=datetime.now(UTC),
    )
    db.add(approval)
    record_event(
        db,
        ctx,
        event_type=f"regulatory_package.approval_{action}",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "version": package.version,
            "action": action,
            "actor_user_id": str(actor_user_id),
        },
    )
    return approval


def request_approval(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    payload: PackageApprovalRequestCreate,
) -> RegulatoryPackageRead:
    actor_user_id = require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status != "validated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Approval can only be requested for a validated package; this package "
                f"is '{package.status}'. Validate it first."
            ),
        )
    report = package.validation_report or {}
    if report.get("error_count", 0) or not report.get("passed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The latest validation report carries ERROR findings; resolve them and "
                "re-validate before requesting approval."
            ),
        )
    _add_approval(
        db, ctx, package, action="requested", actor_user_id=actor_user_id, reason=payload.reason
    )
    notifications.emit(
        db,
        ctx,
        type="reporting.package.pending_approval",
        severity="warning",
        title=f"{package.return_code} {package.reporting_date.isoformat()} awaits approval",
        body=(
            f"Version {package.version} of {package.return_code} for "
            f"{package.reporting_date.isoformat()} is pending an approval decision."
            + (f" Requester note: {payload.reason}" if payload.reason else "")
        ),
        entity_type="regulatory_package",
        entity_id=package.id,
        recipient_role="approver",
    )
    transition(db, ctx, package, "pending_approval")
    db.commit()
    return read_package(db, package)


def _notify_decision(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    approved: bool,
    reason: str | None,
) -> None:
    """Tell the maker what the checker decided (no commit).

    Addressed to ``generated_by`` rather than to a role: an approval decision is
    an answer to one officer's request, and the person who has to act on a
    rework is the one who prepared it.
    """
    notifications.emit(
        db,
        ctx,
        type="reporting.package.approved" if approved else "reporting.package.approval_rejected",
        severity="info" if approved else "warning",
        title=(
            f"{package.return_code} {package.reporting_date.isoformat()} "
            + ("approved" if approved else "returned for rework")
        ),
        body=(
            f"Version {package.version} of {package.return_code} for "
            f"{package.reporting_date.isoformat()} was "
            + ("approved for submission." if approved else "rejected at approval.")
            + (f" Reason: {reason}" if reason else "")
        ),
        entity_type="regulatory_package",
        entity_id=package.id,
        recipient_user_id=package.generated_by,
    )


def ensure_decidable(package: RegulatoryPackage, actor_user_id: UUID) -> None:
    """The two guards every approval decision passes, whichever act carries it.

    Extracted so the signing ceremony's approve-and-sign is subject to the SAME
    maker-checker rule as the bare decision, rather than a second reading of it.

    There are exactly two routes to ``approved`` and both pass through here: the
    bare decision below, and certification, which calls this per checker
    signature from ``attestation.workflow.ensure_checked_release`` at the moment
    it would release the package. Keep it that way — a rule with two
    implementations is a rule with two behaviours.
    """
    if package.status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only a package pending approval can be decided; this package is "
                f"'{package.status}'."
            ),
        )
    if actor_user_id == package.generated_by:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Maker-checker: the approval decision must be made by a different user "
                "than the one who generated the package."
            ),
        )


def _ensure_approval_is_not_the_signature(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> None:
    """Refuse a bare approval when the approver's signature is what approves.

    Without this the two halves of one act stay separable in the other
    direction: a return could be approved for submission by a checker who never
    signed the figures they approved. It is scoped to returns whose policy
    actually requires a signature — an institution that has relaxed signing for
    a return has no ceremony to route the decision through, and the bare
    decision is then the whole of the checker act.
    """
    from app.services.attestation import workflow as attestation  # noqa: PLC0415 - import cycle

    policy = attestation.package_policy(db, ctx, package)
    if not policy.require_signature:
        return
    outstanding = attestation.outstanding_slots(
        policy, attestation.current_signatures(db, ctx, package)
    )
    if not outstanding:
        return
    detail = ", ".join(f"{role} x{count}" for role, count in outstanding)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "approval_requires_signature",
            "message": (
                "This return requires signatures, so approving it and signing it are "
                f"one act. Outstanding: {detail}. Approve and sign it from the signing "
                "workspace, or send it back for corrections."
            ),
        },
    )


def record_certification_approval(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    actor_user_id: UUID,
    reason: str | None,
) -> RegulatoryPackageApproval:
    """The approval decision a checker's certification IS (no commit).

    An approver's signature over the frozen figures and their approval of the
    filing are one act, not two — so the decision row is written in the same
    transaction as the signature. Called from the attestation workflow, which is
    already where ``pending_approval -> approved`` happens, so the row and the
    status can never disagree.

    The status transition is deliberately NOT repeated here: the caller has
    already moved it, and ``transition`` would refuse the ``validated ->
    approved`` shape a single-slot policy produces.
    """
    approval = _add_approval(
        db,
        ctx,
        package,
        action="approved",
        actor_user_id=actor_user_id,
        reason=reason,
    )
    _notify_decision(db, ctx, package, approved=True, reason=reason)
    return approval


def decide_approval(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    payload: PackageApprovalDecisionCreate,
) -> RegulatoryPackageRead:
    actor_user_id = require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    ensure_decidable(package, actor_user_id)
    if payload.action == "approved":
        _ensure_approval_is_not_the_signature(db, ctx, package)
        # Data-integrity gate (audit 2026-08-22 D-2). Mint-time already asked
        # the balance-sheet control; a monthly return then waits days for its
        # approver, and the book can break in that window. Asked BEFORE any row
        # is written so a refusal leaves the package exactly as it was.
        filing_reconciliation.assert_package_reconciled(
            db, ctx, package, purpose="package_approval"
        )
    _add_approval(
        db,
        ctx,
        package,
        action=payload.action,
        actor_user_id=actor_user_id,
        reason=payload.reason,
    )
    approved = payload.action == "approved"
    _notify_decision(db, ctx, package, approved=approved, reason=payload.reason)
    new_status = "approved" if approved else "generated"
    transition(db, ctx, package, new_status, details={"decision": payload.action})
    db.commit()
    return read_package(db, package)


def send_back_for_corrections(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    actor_user_id: UUID,
    reason: str,
) -> RegulatoryPackageApproval:
    """The reviewing checker's other exit: return the package with a note (no commit).

    Identical to a rejected decision — same row, same audit event, same
    notification to the maker — because it IS one: "send back for corrections"
    is what a rejection at approval has always meant. It is exposed as its own
    act only so the reviewer can take it from the surface where they read the
    figures, and so the note is required rather than optional.

    No commit: the caller withdraws the attestation in the same transaction, and
    a package returned for rework while its figures stay frozen would be a
    package nobody can correct.
    """
    ensure_decidable(package, actor_user_id)
    approval = _add_approval(
        db, ctx, package, action="rejected", actor_user_id=actor_user_id, reason=reason
    )
    _notify_decision(db, ctx, package, approved=False, reason=reason)
    transition(db, ctx, package, "generated", details={"decision": "rejected"})
    return approval


def add_submission_event(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    channel: str,
    event: str,
    external_ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> RegulatorySubmissionEvent:
    """Append one channel interaction and audit it (no commit)."""
    row = RegulatorySubmissionEvent(
        organization_id=package.organization_id,
        package_id=package.id,
        channel=channel,
        event=event,
        external_ref=external_ref,
        detail=detail or {},
        occurred_at=datetime.now(UTC),
    )
    db.add(row)
    record_event(
        db,
        ctx,
        event_type=f"regulatory_package.submission_{event}",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "version": package.version,
            "channel": channel,
            "event": event,
            "external_ref": external_ref,
        },
    )
    return row


def submit_package(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    channel: str,
    external_ref: str,
    detail: dict[str, Any] | None = None,
) -> RegulatoryPackageRead:
    """Record a channel submission: ``approved -> submitted`` + submission event.

    Concrete channels (export/submission wave) obtain ``external_ref`` from
    :class:`~app.services.regulatory_reporting.channels.base.SubmissionChannel`
    and delegate here; this function never talks to a channel itself.
    """
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    # Transmission is the last moment the platform can refuse (audit D-2).
    filing_reconciliation.assert_package_reconciled(
        db, ctx, package, purpose="package_submission"
    )
    transition(db, ctx, package, "submitted", details={"channel": channel})
    add_submission_event(
        db,
        ctx,
        package,
        channel=channel,
        event="submitted",
        external_ref=external_ref,
        detail=detail,
    )
    db.commit()
    return read_package(db, package)


def record_regulator_decision(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    channel: str,
    event: str,
    external_ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> RegulatoryPackageRead:
    """Record the regulator outcome: ``submitted -> acknowledged | rejected | declined``.

    Rejection/decline responses carry supervisor comments (ORASS "View
    Comments" parity); they are sealed onto the package for the UI.
    """
    if event not in ("acknowledged", "rejected", "declined"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The regulator decision must be 'acknowledged', 'rejected' or 'declined'.",
        )
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    transition(db, ctx, package, event, details={"channel": channel})
    if event in ("rejected", "declined"):
        comments = (detail or {}).get("comments") or (detail or {}).get("message")
        if comments:
            package.regulator_comments = str(comments)
    add_submission_event(
        db,
        ctx,
        package,
        channel=channel,
        event=event,
        external_ref=external_ref,
        detail=detail,
    )
    _notify_regulator_decision(db, ctx, package, event=event, detail=detail)
    db.commit()
    return read_package(db, package)


_REGULATOR_DECISION_SEVERITIES = {
    "acknowledged": "info",
    "rejected": "warning",
    "declined": "critical",
}
_REGULATOR_DECISION_LABELS = {
    "acknowledged": "acknowledged",
    "rejected": "rejected (returned for correction)",
    "declined": "declined (final refusal)",
}
_COMMENT_SNIPPET_CHARS = 300


def _notify_regulator_decision(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    event: str,
    detail: dict[str, Any] | None,
) -> None:
    """Notify approver-class users AND the package generator (no commit).

    The generator gets a direct row only when the approver fan-out did not
    already reach them (an approver who generated the package gets one row).
    """
    label = _REGULATOR_DECISION_LABELS[event]
    body = (
        f"The regulator {label} {package.return_code} for "
        f"{package.reporting_date.isoformat()} (version {package.version})."
    )
    comments = (detail or {}).get("comments") or (detail or {}).get("message")
    if comments:
        body += f" Supervisor comments: {str(comments)[:_COMMENT_SNIPPET_CHARS]}"
    envelope: dict[str, Any] = {
        "type": f"reporting.regulator.{event}",
        "severity": _REGULATOR_DECISION_SEVERITIES[event],
        "title": f"{package.return_code} {package.reporting_date.isoformat()} {label}",
        "body": body,
        "entity_type": "regulatory_package",
        "entity_id": package.id,
    }
    rows = notifications.emit(db, ctx, **envelope, recipient_role="approver")
    if all(row.recipient_user_id != package.generated_by for row in rows):
        notifications.emit(db, ctx, **envelope, recipient_user_id=package.generated_by)


def list_submission_events(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> SubmissionEventListRead:
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    conditions = (
        RegulatorySubmissionEvent.organization_id == ctx.organization_id,
        RegulatorySubmissionEvent.package_id == package.id,
    )
    rows = list(
        db.scalars(
            select(RegulatorySubmissionEvent)
            .where(*conditions)
            .order_by(
                RegulatorySubmissionEvent.occurred_at.desc(),
                RegulatorySubmissionEvent.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    total = (
        db.scalar(select(func.count()).select_from(RegulatorySubmissionEvent).where(*conditions))
        or 0
    )
    return SubmissionEventListRead(
        package_id=package.id,
        events=[SubmissionEventRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


# ---------------------------------------------------------------------------
# Channel dispatch (export/submission wave)
# ---------------------------------------------------------------------------


def _resolve_exporter() -> Exporter:
    """Lazy seam for the export wave's ``exports.export_package``.

    Imported inside the function body on purpose: the channel dispatch is
    testable (and shippable) independently of the concrete exporters — tests
    monkeypatch this function with a fake producing an artifact row.
    """
    from app.services.regulatory_reporting.exports import export_package  # noqa: PLC0415

    return export_package


def _filing_set(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, *, mint_missing: bool = True
) -> tuple[list[FiledArtifact], dict[str, Any]]:
    """What actually goes to the regulator, and the record of exactly what went.

    Certification puts the officers' signatures ON the return document
    (``attestation.artifact_signing``) and archives that revision as its own
    immutable version. Until this existed, submission read
    ``regulatory_package_artifacts`` — the upserted row that only ever names the
    UNSIGNED export — so a fully certified return was filed with the Bank of
    Ghana as the document nobody had signed. The signed revision replaces the
    unsigned PDF here, and nowhere else does the choice get made.

    The registry's ``filing_format`` rides ALONGSIDE the signed PDF rather than
    being dropped for it. Whether ORASS accepts a PDF as the filing at all is
    unconfirmed (docs/attestation_esignature.md §8 C1); silently replacing a
    required template with a document format the portal may reject would turn a
    signature improvement into a missed statutory deadline (Act 930 s.93(3)).

    An uncertified package is unaffected: no signed revision exists, so the set
    is the artifacts it already had, with the filing format auto-exported when
    there are none — the operator's main path, unchanged.

    ``mint_missing=False`` for the read-only preview of the same set: a GET that
    renders the downtime email bundle must not mint an artifact as a side effect.
    """
    artifacts = _package_artifacts(db, package)
    signed = artifact_versions.latest_signed_version(db, ctx, package)
    filed: list[FiledArtifact] = [
        artifact
        for artifact in artifacts
        # the ALM/Finance working copy (live formulas) is an internal review
        # artifact and is NEVER filed with the regulator
        if artifact.kind != "xlsx_working"
        and (signed is None or artifact.kind != signed.version.kind)
    ]
    detail: dict[str, Any] = {}
    if signed is not None:
        filed.insert(0, signed.version)
        detail["signed_artifact_version_id"] = str(signed.version.id)
        detail["signed_by"] = [
            {
                "signing_role": revision.signature.signing_role,
                "signer_id": revision.signature.signer_id,
                "signed_at": (
                    revision.signature.tsa_time or revision.signature.declared_at
                ).isoformat(),
            }
            for revision in artifact_versions.signed_revisions(db, ctx, package)
        ]
    definition = get_definition(package.return_code)
    required = definition.filing_format if definition is not None else "xlsx"
    exported: list[str] = []
    if (
        mint_missing
        and required is not None
        and required not in {artifact.kind for artifact in filed}
    ):
        # Minted only when the filing would otherwise not carry that format —
        # which is also what keeps this away from a kind a live signature covers,
        # since the signed revision is already IN ``filed`` under its own kind
        # and re-export of a signed kind is refused (``exports._refuse_if_signed``).
        exporter = _resolve_exporter()
        filed.append(exporter(db, ctx, package, required))
        exported.append(required)
    if exported:
        detail["auto_exported_kinds"] = exported
    detail["filed_artifacts"] = [
        {
            "kind": artifact.kind,
            "object_path": artifact.object_path,
            "checksum_sha256": artifact.checksum_sha256,
            "size_bytes": artifact.size_bytes,
            "signed": signed is not None and artifact is signed.version,
        }
        for artifact in filed
    ]
    return filed, detail


def _package_artifacts(db: Session, package: RegulatoryPackage) -> list[RegulatoryPackageArtifact]:
    return list(
        db.scalars(
            select(RegulatoryPackageArtifact)
            .where(
                RegulatoryPackageArtifact.organization_id == package.organization_id,
                RegulatoryPackageArtifact.package_id == package.id,
            )
            .order_by(
                RegulatoryPackageArtifact.created_at,
                RegulatoryPackageArtifact.id,
            )
        )
    )


def _submission_events_asc(
    db: Session, package: RegulatoryPackage
) -> list[RegulatorySubmissionEvent]:
    return list(
        db.scalars(
            select(RegulatorySubmissionEvent)
            .where(
                RegulatorySubmissionEvent.organization_id == package.organization_id,
                RegulatorySubmissionEvent.package_id == package.id,
            )
            .order_by(
                RegulatorySubmissionEvent.occurred_at,
                RegulatorySubmissionEvent.id,
            )
        )
    )


def _latest_submitted_event(
    events: list[RegulatorySubmissionEvent],
) -> RegulatorySubmissionEvent | None:
    for event in reversed(events):
        if event.event == "submitted":
            return event
    return None


def has_pending_orass_reupload(db: Session, package: RegulatoryPackage) -> bool:
    """True while a downtime email submission awaits its ORASS re-upload.

    The flag lives in the append-only submission-event chain: the LATEST
    ``submitted`` event's detail decides — an email fallback sets
    ``pending_orass_reupload: true``, the subsequent ORASS re-upload records a
    new ``submitted`` event without it (BG/FMD/2026/07 "deemed complete").
    """
    if package.status != "submitted":
        return False
    latest = _latest_submitted_event(_submission_events_asc(db, package))
    if latest is None:
        return False
    return bool(latest.detail.get("pending_orass_reupload"))


type _ChannelPlugin = OrassApiChannel | OrassSandboxChannel | EmailFallbackChannel


def _build_channel(
    channel_code: str,
    *,
    config: dict[str, Any],
    credentials: dict[str, Any] | None,
    prior_events: list[RegulatorySubmissionEvent],
    institution_code_fallback: str | None = None,
) -> _ChannelPlugin:
    if channel_code == "orass_api":
        return OrassApiChannel(config=config, credentials=credentials, prior_events=prior_events)
    if channel_code == "orass_sandbox":
        return OrassSandboxChannel(config=config, prior_events=prior_events)
    if channel_code == "email":
        return EmailFallbackChannel(
            config=config,
            prior_events=prior_events,
            institution_code_fallback=institution_code_fallback,
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Channel '{channel_code}' has no automated submission plugin.",
    )


def _load_channel_context(
    db: Session, ctx: TenantContext, bank_id: str, channel_code: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The channel's config JSON plus decrypted credentials (or None).

    Credentials are retrieved per submission cycle via the channel_config
    vault helpers for the ORASS channels only, handed to the channel plugin
    for the one request cycle, and never persisted or logged. The sandbox
    receives none (it transmits nothing); the API channel authenticates
    with them.
    """
    row = channel_config_row(db, ctx, bank_id, channel_code)
    if row is None:
        return {}, None
    credentials: dict[str, Any] | None = None
    if channel_code in ("orass_api", "orass_sandbox") and row.credential_ciphertext is not None:
        credentials = decrypt_channel_credentials(row)
    if channel_code == "orass_sandbox":
        del credentials  # the simulator must never hold credential material
        credentials = None
    return dict(row.config), credentials


def _ensure_attested(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> None:
    """Attestation gate: no return reaches ANY channel — including the manual
    record — without every signature its policy requires.

    Lives in the service rather than the route so a future caller cannot bypass
    it (docs/attestation_esignature.md §4.1 T5).

    An institution that has configured NO signing policy is NOT exempt: since
    2026-07-25 ``policy.default_policy`` requires a preparer and an approver, so
    the unconfigured case is the strictest one, not a hole. (This docstring
    previously described the gate as a no-op without a policy, which had been
    untrue since that change.) The gate is suspended only by an explicit,
    audited relaxation row or by ``ATTESTATION_ESIGN_REQUIRED=0`` — and neither
    suspends maker-checker, which the transition table and ``ensure_decidable``
    enforce on the bare approval path that a suspended ceremony falls back to.
    """
    from app.services.attestation.workflow import (  # noqa: PLC0415 - breaks an import cycle
        ensure_submittable,
    )

    ensure_submittable(db, ctx, package)


def _ensure_channel_submittable(
    db: Session, package: RegulatoryPackage, channel_code: str
) -> tuple[bool, str | None]:
    """Guard the narrow submitted->submitted re-upload; returns
    ``(is_reupload, prior_email_ref)``."""
    if package.status != "submitted":
        # Everything else defers to the transition table (approved -> submitted).
        ensure_transition_allowed(package, "submitted")
        return False, None
    latest = _latest_submitted_event(_submission_events_asc(db, package))
    pending = latest is not None and bool(latest.detail.get("pending_orass_reupload"))
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This package has already been submitted; only a downtime email "
                "submission awaiting its ORASS re-upload can be submitted again."
            ),
        )
    if channel_code not in ("orass_api", "orass_sandbox"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A downtime email submission is deemed complete only after "
                "re-upload through ORASS (Notice BG/FMD/2026/07); submit via "
                "an ORASS channel."
            ),
        )
    assert latest is not None
    return True, latest.external_ref


def submit_package_via_channel(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    channel_override: str | None = None,
) -> RegulatoryPackageRead:
    """Resolve the channel, deliver the package, and record the outcome.

    - Channel: explicit override, else the registry default for the return.
    - Preconditions: approved package (or the guarded email->ORASS re-upload);
      when no artifact exists yet, an xlsx export is minted first through the
      lazy exporter seam — the operator's main path.
    - Downtime: a ChannelDowntimeError becomes a structured 409 directing the
      operator to the email fallback endpoint.
    """
    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    definition = get_definition(package.return_code)
    channel_code = channel_override or (
        definition.default_channel if definition is not None else "manual"
    )

    is_reupload, prior_email_ref = _ensure_channel_submittable(db, package, channel_code)

    _ensure_attested(db, ctx, package)
    # Sits beside the attestation gate for the same reason it lives in the
    # service rather than the route: no channel — including the manual record —
    # transmits a return whose book does not reconcile (audit 2026-08-22 D-2).
    filing_reconciliation.assert_package_reconciled(
        db, ctx, package, purpose="package_submission"
    )

    if channel_code == "manual":
        transition(db, ctx, package, "submitted", details={"channel": channel_code})
        add_submission_event(
            db,
            ctx,
            package,
            channel="manual",
            event="submitted",
            external_ref=None,
            detail={"note": "Submission recorded as completed manually outside AequorOS."},
        )
        db.commit()
        return read_package(db, package)

    artifacts, filing_detail = _filing_set(db, ctx, package)

    prior_events = _submission_events_asc(db, package)
    config, credentials = _load_channel_context(db, ctx, bank.id, channel_code)
    credentials_present = credentials is not None
    # ORASS-style references are form-set sequences; inject the per-(bank,
    # return) submission sequence so the sandbox mints deterministic refs.
    config["_submission_sequence"] = _next_submission_sequence(db, ctx, bank.id, package)
    channel = _build_channel(
        channel_code,
        config=config,
        credentials=credentials,
        prior_events=prior_events,
        institution_code_fallback=institution_profile.orass_institution_code(db, ctx, bank.id),
    )
    try:
        external_ref = channel.submit(package, artifacts)
    except ChannelDowntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "channel_downtime",
                "message": exc.operator_message,
                "fallback": {
                    "channel": "email",
                    "endpoint": (
                        f"/api/v1/banks/{bank.id}/regulatory-packages/{package.id}/submit"
                    ),
                    "payload": {"channel": "email"},
                    "instructions_endpoint": (
                        f"/api/v1/banks/{bank.id}/regulatory-packages/"
                        f"{package.id}/email-fallback-instructions"
                    ),
                },
            },
        ) from exc
    except ChannelPreconditionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.operator_message
        ) from exc
    except ChannelError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
        ) from exc
    finally:
        del credentials  # per-cycle retrieval: discard, never persist or log

    detail = dict(channel.last_detail)
    # The filing record wins over the channel's own summary of it: what was
    # sent is a fact about this submission, not a channel opinion.
    detail.update(filing_detail)
    if credentials_present:
        detail["credentials_used"] = True  # fingerprint-level fact only
    transition_details: dict[str, Any] = {"channel": channel_code}
    if is_reupload:
        detail["pending_orass_reupload"] = False
        detail["reupload_of"] = prior_email_ref
        transition_details["orass_reupload_of"] = prior_email_ref
    # ORASS revision semantics: 1.<granted resubmissions in this chain>.
    package.submission_revision = _submission_revision(db, ctx, bank.id, package)
    detail["submission_revision"] = package.submission_revision
    transition(db, ctx, package, "submitted", details=transition_details)
    add_submission_event(
        db,
        ctx,
        package,
        channel=channel_code,
        event="submitted",
        external_ref=external_ref,
        detail=detail,
    )
    db.commit()
    return read_package(db, package)


def poll_submission(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> SubmissionPollRead:
    """Poll the latest channel submission and record regulator decisions."""
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only a submitted package can be polled; this package is '{package.status}'.",
        )
    events = _submission_events_asc(db, package)
    latest = _latest_submitted_event(events)
    if latest is None or latest.external_ref is None or latest.channel == "manual":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No pollable channel submission exists for this package; record "
                "the regulator decision manually instead."
            ),
        )
    config, credentials = _load_channel_context(db, ctx, bank_id, latest.channel)
    channel = _build_channel(
        latest.channel, config=config, credentials=credentials, prior_events=events
    )
    try:
        poll_status, poll_detail = channel.poll_with_detail(latest.external_ref)
    except ChannelDowntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "channel_downtime", "message": exc.operator_message},
        ) from exc
    except ChannelError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
        ) from exc
    finally:
        del credentials  # per-cycle retrieval: discard, never persist or log
    poll_event = add_submission_event(
        db,
        ctx,
        package,
        channel=latest.channel,
        event="status_poll",
        external_ref=latest.external_ref,
        detail={**poll_detail, "result": poll_status},
    )
    if poll_status in ("acknowledged", "rejected", "declined"):
        record_regulator_decision(
            db,
            ctx,
            bank_id,
            package.id,
            channel=latest.channel,
            event=poll_status,
            external_ref=latest.external_ref,
            detail=poll_detail,
        )  # commits
    else:
        db.commit()
    return SubmissionPollRead(
        poll_status=poll_status,
        event=SubmissionEventRead.model_validate(poll_event),
        package=read_package(db, package),
    )


def _next_submission_sequence(
    db: Session, ctx: TenantContext, bank_id: str, package: RegulatoryPackage
) -> int:
    """Monotonic per-(bank, return) sequence for ORASS-style references."""
    prior = (
        db.scalar(
            select(func.count())
            .select_from(RegulatorySubmissionEvent)
            .join(
                RegulatoryPackage,
                (RegulatoryPackage.id == RegulatorySubmissionEvent.package_id)
                & (RegulatoryPackage.organization_id == RegulatorySubmissionEvent.organization_id),
            )
            .where(
                RegulatorySubmissionEvent.organization_id == ctx.organization_id,
                RegulatorySubmissionEvent.event == "submitted",
                RegulatoryPackage.bank_id == bank_id,
                RegulatoryPackage.return_code == package.return_code,
            )
        )
        or 0
    )
    return prior + 1


def _version_chain_ids(db: Session, package: RegulatoryPackage) -> list[UUID]:
    """All package ids for this (bank, return_code, reporting_date, basis) chain.

    ``basis`` belongs in the key because solo and consolidated are independent
    current-version chains for the same return and reporting date (see
    ``generation.generate_package``). Without it, a granted solo resubmission
    bumped the consolidated return's ORASS revision too (gap G14).
    """
    return list(
        db.scalars(
            select(RegulatoryPackage.id).where(
                RegulatoryPackage.organization_id == package.organization_id,
                RegulatoryPackage.bank_id == package.bank_id,
                RegulatoryPackage.return_code == package.return_code,
                RegulatoryPackage.reporting_date == package.reporting_date,
                RegulatoryPackage.basis == package.basis,
            )
        )
    )


def _submission_revision(
    db: Session, ctx: TenantContext, bank_id: str, package: RegulatoryPackage
) -> str:
    """ORASS revision: ``1.<granted resubmissions in this version chain>``."""
    chain_ids = _version_chain_ids(db, package)
    granted = (
        db.scalar(
            select(func.count())
            .select_from(RegulatoryResubmissionRequest)
            .where(
                RegulatoryResubmissionRequest.organization_id == ctx.organization_id,
                RegulatoryResubmissionRequest.package_id.in_(chain_ids),
                RegulatoryResubmissionRequest.status == "granted",
            )
        )
        or 0
    )
    return f"1.{granted}"


# ---------------------------------------------------------------------------
# Resubmission requests (ORASS "Request Resubmission", LRT guide §5.3)
# ---------------------------------------------------------------------------

_RESUBMITTABLE_STATUSES = ("submitted", "acknowledged")


def _read_resubmission(row: RegulatoryResubmissionRequest) -> ResubmissionRequestRead:
    return ResubmissionRequestRead.model_validate(row)


def request_resubmission(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    payload: ResubmissionRequestCreate,
) -> ResubmissionRequestRead:
    """File a resubmission request; ORASS channels decide it in-cycle.

    A submitted/acknowledged return is immutable at the regulator; this is the
    only path to a correcting version. On grant, the next regeneration for the
    same return and reporting date consumes the request and the subsequent
    submission carries revision +0.1. Email/manual submissions leave the
    request ``requested`` for a manual decision (decideResubmission).
    """
    actor_user_id = require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status not in _RESUBMITTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Resubmission can only be requested for a submitted or "
                f"acknowledged package; this package is '{package.status}'."
            ),
        )
    open_request = db.scalar(
        select(RegulatoryResubmissionRequest).where(
            RegulatoryResubmissionRequest.organization_id == ctx.organization_id,
            RegulatoryResubmissionRequest.package_id == package.id,
            RegulatoryResubmissionRequest.status == "requested",
        )
    )
    if open_request is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A resubmission request is already pending for this package.",
        )
    events = _submission_events_asc(db, package)
    latest = _latest_submitted_event(events)
    channel_code = latest.channel if latest is not None else "manual"

    row = RegulatoryResubmissionRequest(
        organization_id=package.organization_id,
        package_id=package.id,
        reason=payload.reason,
        status="requested",
        requested_by=actor_user_id,
        detail={},
        occurred_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()

    if channel_code in ("orass_api", "orass_sandbox") and latest is not None:
        config, credentials = _load_channel_context(db, ctx, bank_id, channel_code)
        channel = _build_channel(
            channel_code, config=config, credentials=credentials, prior_events=events
        )
        try:
            if isinstance(channel, OrassSandboxChannel):
                decision, detail = channel.decide_resubmission(
                    latest.external_ref or "", payload.reason
                )
            else:
                assert isinstance(channel, OrassApiChannel)
                decision, detail = channel.request_resubmission(
                    latest.external_ref or "", payload.reason
                )
        except ChannelDowntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": "channel_downtime", "message": exc.operator_message},
            ) from exc
        except ChannelError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message
            ) from exc
        finally:
            del credentials
        if decision in ("granted", "denied"):
            row.status = decision
            row.decided_at = datetime.now(UTC)
        row.detail = detail

    record_event(
        db,
        ctx,
        event_type="regulatory_package.resubmission_requested",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "version": package.version,
            "request_id": str(row.id),
            "status": row.status,
        },
    )
    db.commit()
    return _read_resubmission(row)


def decide_resubmission(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    request_id: UUID,
    *,
    decision: str,
    note: str | None = None,
) -> ResubmissionRequestRead:
    """Record a manual grant/deny for email/manual-channel submissions."""
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    row = db.scalar(
        select(RegulatoryResubmissionRequest).where(
            RegulatoryResubmissionRequest.id == request_id,
            RegulatoryResubmissionRequest.organization_id == ctx.organization_id,
            RegulatoryResubmissionRequest.package_id == package.id,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resubmission request not found."
        )
    if row.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This resubmission request is already '{row.status}'.",
        )
    if decision not in ("granted", "denied"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The decision must be 'granted' or 'denied'.",
        )
    row.status = decision
    row.decided_at = datetime.now(UTC)
    row.detail = {**row.detail, "manual_decision": True, "note": note}
    record_event(
        db,
        ctx,
        event_type=f"regulatory_package.resubmission_{decision}",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={"request_id": str(row.id), "manual": True},
    )
    db.commit()
    return _read_resubmission(row)


def list_resubmission_requests(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> ResubmissionRequestListRead:
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    rows = list(
        db.scalars(
            select(RegulatoryResubmissionRequest)
            .where(
                RegulatoryResubmissionRequest.organization_id == ctx.organization_id,
                RegulatoryResubmissionRequest.package_id == package.id,
            )
            .order_by(
                RegulatoryResubmissionRequest.occurred_at,
                RegulatoryResubmissionRequest.id,
            )
        )
    )
    return ResubmissionRequestListRead(
        package_id=package.id, requests=[_read_resubmission(row) for row in rows]
    )


def granted_unconsumed_resubmission(
    db: Session, package: RegulatoryPackage
) -> RegulatoryResubmissionRequest | None:
    """The one-shot authorization an acknowledged package needs to regenerate."""
    return db.scalar(
        select(RegulatoryResubmissionRequest)
        .where(
            RegulatoryResubmissionRequest.organization_id == package.organization_id,
            RegulatoryResubmissionRequest.package_id == package.id,
            RegulatoryResubmissionRequest.status == "granted",
            RegulatoryResubmissionRequest.consumed_by_package_id.is_(None),
        )
        .order_by(RegulatoryResubmissionRequest.occurred_at)
        .limit(1)
    )


def list_package_artifacts(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> list[RegulatoryPackageArtifact]:
    """All artifacts for a package (persisted list; UI must not rely on
    session-local export caches)."""
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    return _package_artifacts(db, package)


def export_package_artifact(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    kind: ArtifactKind,
) -> RegulatoryArtifactRead:
    """Mint one artifact through the lazy exporter seam and audit it."""
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status == "superseded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A superseded package is immutable history; export the current "
                "version for this return and reporting date instead."
            ),
        )
    exporter = _resolve_exporter()
    artifact = exporter(db, ctx, package, kind)
    record_event(
        db,
        ctx,
        event_type="regulatory_package.exported",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "version": package.version,
            "kind": kind,
            "artifact_id": str(artifact.id),
            "object_path": artifact.object_path,
        },
    )
    db.commit()
    return RegulatoryArtifactRead.model_validate(artifact)


def get_artifact_or_404(
    db: Session, ctx: TenantContext, bank_id: str, artifact_id: UUID
) -> RegulatoryPackageArtifact:
    """Tenant-scoped artifact lookup, constrained to the bank via its package."""
    get_bank_or_404(db, ctx, bank_id)
    artifact = db.scalar(
        select(RegulatoryPackageArtifact)
        .join(
            RegulatoryPackage,
            (RegulatoryPackage.id == RegulatoryPackageArtifact.package_id)
            & (RegulatoryPackage.organization_id == RegulatoryPackageArtifact.organization_id),
        )
        .where(
            RegulatoryPackageArtifact.id == artifact_id,
            RegulatoryPackageArtifact.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
        )
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory artifact not found."
        )
    return artifact


def prepare_artifact_download(
    db: Session, ctx: TenantContext, bank_id: str, artifact_id: UUID
) -> tuple[RegulatoryPackageArtifact, str]:
    """Resolve the artifact + institution storage slug and audit the download."""
    bank = get_bank_or_404(db, ctx, bank_id)
    artifact = get_artifact_or_404(db, ctx, bank_id, artifact_id)
    # Lazy import: pulling app.services.ingestion at module import time would
    # drag the whole source-adapter registry into every workflow import.
    from app.services.ingestion import bank_slug  # noqa: PLC0415

    slug = bank_slug(db, bank)
    record_event(
        db,
        ctx,
        event_type="regulatory_artifact.downloaded",
        entity_type="regulatory_package_artifact",
        entity_id=artifact.id,
        details={
            "package_id": str(artifact.package_id),
            "kind": artifact.kind,
            "object_path": artifact.object_path,
        },
    )
    db.commit()
    return artifact, slug


def email_fallback_instructions(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> EmailFallbackInstructionsRead:
    """Preview the send-ready email fallback bundle without submitting."""
    bank = get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    row = channel_config_row(db, ctx, bank.id, "email")
    config = dict(row.config) if row is not None else {}
    # The same set the channel would send, so the operator's .eml carries the
    # signed return rather than the export it supersedes.
    filed, _detail = _filing_set(db, ctx, package, mint_missing=False)
    bundle = build_email_bundle(
        package,
        filed,
        config,
        institution_code_fallback=institution_profile.orass_institution_code(db, ctx, bank.id),
    )
    return EmailFallbackInstructionsRead(package_id=package.id, **bundle)
