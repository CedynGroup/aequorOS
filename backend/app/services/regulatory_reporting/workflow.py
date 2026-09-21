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
from typing import Any, Final, Literal
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
from app.services.regulatory_reporting import artifact_versions, family_hooks
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
from app.services.regulatory_reporting.registry import ReturnDefinition, get_definition

# §2 lifecycle. "generated" is re-entered on approval rejection (rework) and
# on a failed re-validation; "superseded" is reachable from any non-terminal
# state via regeneration (enforced in generation.py, listed here for audit).
# "submitted -> submitted" exists ONLY for the BG/FMD/2026/07 re-upload of a
# downtime email submission via ORASS; submit_package_via_channel guards it
# (prior channel must be email with pending_orass_reupload still set).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"generated", "superseded"}),
    # "generated -> pending_approval" is the RESEND after a send-back: the
    # return went back to the Preparer, its checks still pass, and it is sent
    # again without a re-run. Entry to the chain is gated by ``checks_passed``,
    # not by a status, so the status must be able to follow the chain there.
    "generated": frozenset({"validated", "pending_approval", "superseded"}),
    "validated": frozenset({"pending_approval", "generated", "superseded"}),
    "pending_approval": frozenset({"approved", "generated", "superseded"}),
    # "approved -> pending_approval / generated" is the Validator's send-back
    # BEFORE filing: the chain completed, the Validator looked again and
    # returned it to the Approver or the Preparer. Only a recorded send-back
    # decision produces it (``filing_workflow.chain``), and it never weakens the
    # filing gate — ``-> submitted`` still requires the chain to be complete.
    "approved": frozenset({"submitted", "pending_approval", "generated", "superseded"}),
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

type ArtifactKind = Literal["xlsx", "csv", "pdf", "xlsx_working", "docx_working"]
#: Artifacts whose content RECALCULATES: the figures move if a reader edits a
#: cell, so none of them is ever signed and none of them is a record of truth.
#: "xlsx_working" is the copy of an official form carrying the template's own
#: live formulas, "docx_working" the Word copy of an ICAAP report.
WORKING_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset({"xlsx_working", "docx_working"})
#: The working kinds that ALSO go to the regulator, named one at a time.
#: Founder decision 2026-09-20: supervisors prefer the Excel form with its
#: formulas live, so the formula workbook now rides ALONGSIDE the protected
#: values-only workbook in the filing set. Filed is not signed — certification
#: still pins its revision to the values-only PDF
#: (``attestation.artifact_signing.ARTIFACT_KIND``), which stays the record of
#: truth; nothing here moves it.
FILABLE_WORKING_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset({"xlsx_working"})
#: What a filing leaves behind on the KIND axis. DERIVED, never written out: a
#: new working kind added to ``WORKING_ARTIFACT_KINDS`` lands here by default
#: and has to be named in ``FILABLE_WORKING_ARTIFACT_KINDS`` to reach a
#: regulator. That is the property the single set used to give by excluding
#: everything — a working kind still cannot be admitted to a filing by
#: omission, it must opt in.
UNFILABLE_WORKING_ARTIFACT_KINDS: Final[frozenset[str]] = (
    WORKING_ARTIFACT_KINDS - FILABLE_WORKING_ARTIFACT_KINDS
)
#: ...and which returns may carry each opted-in kind, by GENERATOR. The second
#: axis, and it is deny-by-default in the same way: a filable kind whose
#: generator is not listed here is not filed.
#:
#: The founder's decision named one thing — *"BoG appear to prefer Excel with
#: formulas"* — and the workbook it named is the official Bank of Ghana form,
#: produced by ``bog_form`` from BoG's own committed template by evaluating the
#: template's own formulas. An SDI packet's working copy is an AequorOS
#: calculation sheet, not a regulator's workbook; filing it would be inferring
#: a second regulator's preference from a decision that stated one. So it stays
#: internal, and its "not a filing artifact" label stays true.
#: A kind with no entry here reaches nobody: adding a kind to
#: ``FILABLE_WORKING_ARTIFACT_KINDS`` is not enough, the generators that may
#: file it have to be named too.
WORKING_ARTIFACT_FILING_GENERATORS: Final[dict[str, frozenset[str]]] = {
    "xlsx_working": frozenset({"bog_form"}),
}


def filing_admits_artifact(kind: str, *, generator: str | None) -> bool:
    """Is an artifact of ``kind``, produced by ``generator``, part of a filing?

    Deny-by-default on both axes, and the ONE place either question is asked.
    An ordinary artifact (pdf/xlsx/csv) is always admitted; a recalculable one
    must have opted in by kind AND have its generator named for that kind.
    """
    if kind not in WORKING_ARTIFACT_KINDS:
        return True
    if kind in UNFILABLE_WORKING_ARTIFACT_KINDS:
        return False
    return generator is not None and generator in WORKING_ARTIFACT_FILING_GENERATORS.get(
        kind, frozenset()
    )
#: Presentation order of a filing set and of the artifact list behind it. The
#: signed record leads (pinned below, outside this map); then the Excel copies
#: — the format 44 of 47 returns declare as ``filing_format`` — and the CSV
#: convenience export last. Rank only: ties keep their export order.
FILING_PRESENTATION_ORDER: Final[dict[str, int]] = {
    "pdf": 0,
    "xlsx": 1,
    "xlsx_working": 2,
    "csv": 3,
}
_UNRANKED_ARTIFACT_ORDER: Final[int] = 9
#: Channels that send nothing to a regulator. A REHEARSAL package (D-029/D-068)
#: may only be submitted through one of these: the dry run exercises the act of
#: submitting, it does not perform it.
NON_TRANSMITTING_CHANNELS: Final[frozenset[str]] = frozenset({"manual"})
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
    """Apply one allowed status transition and audit it (no commit).

    This is the ONE writer of ``-> submitted``, which is why the filing chain's
    transmission gate is asked here rather than on a route. Two filing gates
    have already been lost at a seam in this codebase because they sat at one
    mint site and a second was added (D-069; the withdrawn-evidence check
    missing from ``generate_frozen_package``). A gate at the choke point is one
    a new path cannot forget.
    """
    ensure_transition_allowed(package, new_status)
    if new_status == "submitted":
        from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415 - cycle

        filing_chain.assert_transmission_permitted(db, ctx, package)
    previous = package.status
    package.status = new_status
    # The family's own reaction to the move (ICAAP: the cycle follows its
    # package into submitted / acknowledged). Runs BEFORE the audit event so a
    # hook that refuses leaves no record of a transition that did not happen.
    family_hooks.on_transition(db, ctx, package, previous=previous, new_status=new_status)
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
    from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415 - cycle

    actor_user_id = require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id, for_update=True)
    # The Preparer's act. The chain owns the preconditions — ``checks_passed``
    # gates ENTRY, which is what machine validation is for; it is not a stage
    # and not a person. The status follows the chain, not the other way round.
    filing_chain.start_chain(
        db, ctx, package, note=payload.reason, actor=actor_user_id
    )
    # The coarse approval log stays: it is append-only evidence with its own
    # readers (``RegulatoryPackageRead.approvals``). The CHAIN is the authority.
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
    filing are one act, not two — so the chain decision is written in the same
    transaction as the signature. Nobody has to approve a second time in the
    workspace because they signed.

    It is the CHAIN that moves, not the status: the status is the chain's
    projection. So a checker's signature advances the return to the stage after
    theirs, and with the default chain that stage is the Validator — the return
    becomes ``approved``, i.e. filable, only when the Validator has taken it.
    This used to write ``approved`` directly, which is the conflation the
    redesign removes: a signature by the Approver is not authority to file.
    """
    from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415 - cycle

    approval = _add_approval(
        db,
        ctx,
        package,
        action="approved",
        actor_user_id=actor_user_id,
        reason=reason,
    )
    _notify_decision(db, ctx, package, approved=True, reason=reason)
    filing_chain.record_stage_approval(db, ctx, package, actor=actor_user_id, comment=reason)
    filing_chain.apply_projection(db, ctx, filing_chain.load_state(db, ctx, package))
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
    package = get_package_or_404(db, ctx, bank_id, package_id, for_update=True)
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
    # The chain decides where the return goes; the status is its projection.
    # This route is the COMPATIBILITY surface: it decides the stage the return
    # is actually waiting at, on the state as the server holds it. The chain
    # route (``POST .../workflow/decisions``) is the one that pins the round and
    # the review digest the officer was looking at, and is what the redesigned
    # workspace uses.
    _decide_on_chain(db, ctx, package, approved=approved, reason=payload.reason)
    db.commit()
    return read_package(db, package)


def _decide_on_chain(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    approved: bool,
    reason: str | None,
) -> None:
    """Record this approve/reject as the chain decision it is."""
    from app.schemas.filing_workflow import PackageStageDecisionCreate  # noqa: PLC0415
    from app.services.filing_workflow import chain as filing_chain  # noqa: PLC0415 - cycle

    state = filing_chain.load_state(db, ctx, package)
    filing_chain.decide(
        db,
        ctx,
        package,
        PackageStageDecisionCreate(
            decision="approved" if approved else "returned",
            round=package.workflow_round,
            review_digest=state.review_digest,
            # A rejection at approval has always meant "back to the preparer",
            # and now it says so by name instead of by a status going backwards.
            return_to_seq=None if approved else 1,
            comment=reason,
        ),
    )


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
    _decide_on_chain(db, ctx, package, approved=False, reason=reason)
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


#: Every format a filing carries when the return can produce it. The required
#: ``filing_format`` is always attempted first; these ride alongside it.
_FULL_FILING_PACK: Final[tuple[ArtifactKind, ...]] = (
    "xlsx",
    "pdf",
    "xlsx_working",
    "csv",
)


def _produces_working_copy(definition: ReturnDefinition | None) -> bool:
    """Can this return render a live-formula workbook at all?

    Two sources, and they are different things: an official regulator template
    yields the REGULATOR's workbook with its own formulas, while
    ``supports_working_copy`` yields an AequorOS calculation sheet. A return
    with neither — LMT, for one — has no formula workbook in existence.
    """
    if definition is None:
        return False
    from app.services.regulatory_reporting.bog_forms.registry_entries import (  # noqa: PLC0415 - cycle
        is_bog_official_template,
    )

    return bool(
        is_bog_official_template(definition.template_id) or definition.supports_working_copy
    )


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

    The formula workbook (``xlsx_working``) rides along too, by founder
    decision of 2026-09-20: supervisors prefer the Excel form with its formulas
    live, so both Excel copies are filed. It is filed and NOT signed — the
    protected values-only artifact the officers certified remains the record of
    truth, and the workbook says so on its own face.

    That applies to the official BoG forms ONLY — the decision named BoG's own
    workbook, and ``filing_admits_artifact`` is where both halves of the
    question (which kind, which generator) are asked. An SDI packet's working
    sheet is an AequorOS calculation aid, stays internal, and keeps saying so.

    ``mint_missing=False`` for the read-only preview of the same set: a GET that
    renders the downtime email bundle must not mint an artifact as a side effect.
    """
    artifacts = _package_artifacts(db, package)
    signed = artifact_versions.latest_signed_version(db, ctx, package)
    definition = get_definition(package.return_code)
    generator = definition.generator if definition is not None else None
    filed: list[FiledArtifact] = [
        artifact
        for artifact in artifacts
        # A recalculable artifact reaches the regulator only if its kind opted
        # in AND this return's generator is named for that kind — the official
        # BoG forms, and nothing else. The Word ICAAP draft and every SDI
        # working sheet stay internal.
        if filing_admits_artifact(artifact.kind, generator=generator)
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
    required = definition.filing_format if definition is not None else "xlsx"
    exported: list[str] = []
    if mint_missing:
        # THE FULL PACK, not just the required format (founder decision
        # 2026-09-20). Until now a filing carried whatever the preparer had
        # happened to export, plus the one required format minted on the way
        # out — so an identical return filed twice could carry three files or
        # one, decided by which buttons somebody pressed. A supervisor should
        # receive the same set every time.
        #
        # Order is the presentation order: the values-only workbook and the PDF
        # are the record, the formula workbook (where the return produces one)
        # carries the regulator's own live formulas, and the CSV is the
        # machine-readable copy.
        #
        # ``filing_admits_artifact`` still decides whether a recalculable kind
        # may go at all, and a kind a live signature covers is never re-exported
        # (``exports._refuse_if_signed``) because the signed revision is already
        # in ``filed`` under its own kind.
        wanted: list[ArtifactKind] = [required] if required is not None else []
        for kind in _FULL_FILING_PACK:
            if kind not in wanted:
                wanted.append(kind)
        exporter = _resolve_exporter()
        for kind in wanted:
            if kind in {artifact.kind for artifact in filed}:
                continue
            if not filing_admits_artifact(kind, generator=generator):
                continue
            if kind in WORKING_ARTIFACT_KINDS and not _produces_working_copy(definition):
                # No formula workbook exists for this return — BoG publishes no
                # template for it and no calculation sheet is built. Nothing is
                # being withheld; there is nothing to mint.
                continue
            try:
                filed.append(exporter(db, ctx, package, kind))
            except HTTPException:
                # One format failing to render must not sink the filing: the
                # required format is attempted first and its failure still
                # raises, because a filing without it is not a filing.
                if kind == required:
                    raise
                continue
            exported.append(kind)
    if exported:
        detail["auto_exported_kinds"] = exported
    # Order what goes, and what the record says went: the signed revision
    # first — never buried behind a copy nobody signed — then the Excel copies
    # ahead of the CSV. Stable, so artifacts of one kind keep their export
    # order, and the auto-exported filing format lands in its own rank rather
    # than at the tail.
    filed.sort(
        key=lambda artifact: (
            -1
            if signed is not None and artifact is signed.version
            else FILING_PRESENTATION_ORDER.get(artifact.kind, _UNRANKED_ARTIFACT_ORDER)
        )
    )
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
    # The documents the return was filed WITH, by hash. Channels transmit only
    # artifacts — attachment transport over ORASS or email is not in scope, and
    # ICAAP is manual-only — so this is a MANIFEST: the record that answers
    # "which Board resolution did we file with the FY2026 ICAAP" from the
    # database, years later, without trusting a folder.
    from app.services.regulatory_reporting import (  # noqa: PLC0415 - breaks an import cycle
        attachments as reporting_attachments,
    )

    detail["attachments"] = reporting_attachments.manifest(db, ctx, package)
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
    if package.is_rehearsal and channel_code not in NON_TRANSMITTING_CHANNELS:
        # The cross-table half of D-029/D-068 that a CHECK cannot express: the
        # channel lives on ``regulatory_submission_events``, so the DB cannot
        # see it from the package row. A rehearsal records that it was
        # submitted; it never actually transmits anything to a regulator.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "rehearsal_channel_not_permitted",
                "message": (
                    "This is a rehearsal, not a filing. Record its submission "
                    "manually; it is never transmitted to the regulator."
                ),
                "allowed_channels": sorted(NON_TRANSMITTING_CHANNELS),
            },
        )
    definition = get_definition(package.return_code)
    allowed = definition.allowed_channels if definition is not None else None
    if allowed is not None and channel_code not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "channel_not_supported_for_return",
                "message": (
                    f"'{package.return_code}' is filed through "
                    f"{', '.join(allowed)}; '{channel_code}' is not one of them."
                ),
                "allowed_channels": list(allowed),
            },
        )
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


def _resolve_external_ref(
    definition: ReturnDefinition | None,
    channel_code: str,
    *,
    external_ref: str | None,
) -> str | None:
    """The regulator-side reference a MANUAL submission is recorded under.

    Only the manual channel takes one from the client: every other channel
    MINTS its reference at the regulator and a client-supplied value there would
    be a claim about the regulator's own record. A return whose registry entry
    declares ``requires_external_ref`` — the paragraph 82 disclosure, whose
    "filing" IS the published URL — refuses without it, because recording the
    disclosure with no evidence of where it was published records nothing.
    """
    if channel_code != "manual":
        return None
    value = (external_ref or "").strip() or None
    if definition is not None and definition.requires_external_ref and value is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "external_ref_required",
                "message": (
                    f"'{definition.code}' is recorded against the reference the "
                    "regulator or the publication itself carries. Provide it with "
                    "the submission."
                ),
                "return_code": definition.code,
            },
        )
    return value


def submit_package_via_channel(  # noqa: PLR0913 - the submission key is its named parts
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    package_id: UUID,
    *,
    channel_override: str | None = None,
    external_ref: str | None = None,
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
    external_ref = _resolve_external_ref(
        definition, channel_code, external_ref=external_ref
    )

    _ensure_attested(db, ctx, package)
    # Sits beside the attestation gate for the same reason it lives in the
    # service rather than the route: no channel — including the manual record —
    # transmits a return whose book does not reconcile (audit 2026-08-22 D-2).
    filing_reconciliation.assert_package_reconciled(
        db, ctx, package, purpose="package_submission"
    )

    if channel_code == "manual":
        # ``mint_missing=False``: recording a submission that happened outside
        # the platform must not have the side effect of minting an artifact
        # nobody filed. What the record says was filed is what already existed.
        _filed, filing_detail = _filing_set(db, ctx, package, mint_missing=False)
        transition(db, ctx, package, "submitted", details={"channel": channel_code})
        add_submission_event(
            db,
            ctx,
            package,
            channel="manual",
            event="submitted",
            external_ref=external_ref,
            detail={
                "note": "Submission recorded as completed manually outside AequorOS.",
                **filing_detail,
            },
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
    session-local export caches).

    Returned in ``FILING_PRESENTATION_ORDER`` — the same order a filing goes
    in, so the download list a preparer reads cannot disagree with what the
    submission record says went: the submission document first, then both Excel
    copies, then the CSV.
    """
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    return sorted(
        _package_artifacts(db, package),
        key=lambda artifact: FILING_PRESENTATION_ORDER.get(
            artifact.kind, _UNRANKED_ARTIFACT_ORDER
        ),
    )


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
