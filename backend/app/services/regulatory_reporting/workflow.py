"""Package lifecycle state machine (docs/regulatory_reporting.md §2, §5).

Explicit allowed-transition table; every transition is audit-logged via
``record_event`` and submission-bound transitions additionally append a
``RegulatorySubmissionEvent``. Maker-checker: the approval decision must come
from a different user than the package generator (409 otherwise).

Channel dispatch (this wave): ``submit_package_via_channel`` resolves the
channel (override or registry default), auto-exports an xlsx artifact through
the lazy exporter seam when none exists, delegates to the concrete channel
plugin, and records the outcome. ``poll_submission`` maps the latest
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
    RegulatorySubmissionEvent,
)
from app.schemas.regulatory_reporting import (
    EmailFallbackInstructionsRead,
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryArtifactRead,
    RegulatoryPackageRead,
    SubmissionEventListRead,
    SubmissionEventRead,
    SubmissionPollRead,
)
from app.services.audit import record_event
from app.services.regulatory_reporting.channel_config import (
    channel_config_row,
    decrypt_channel_credentials,
)
from app.services.regulatory_reporting.channels import (
    ChannelDowntimeError,
    ChannelPreconditionError,
    EmailFallbackChannel,
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
    "submitted": frozenset({"acknowledged", "rejected", "submitted"}),
    "acknowledged": frozenset(),
    "rejected": frozenset({"superseded"}),
    "superseded": frozenset(),
}

type ArtifactKind = Literal["xlsx", "csv", "pdf"]
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
    bank_id: UUID,
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
    transition(db, ctx, package, "pending_approval")
    db.commit()
    return read_package(db, package)


def decide_approval(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    package_id: UUID,
    payload: PackageApprovalDecisionCreate,
) -> RegulatoryPackageRead:
    actor_user_id = require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
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
    _add_approval(
        db,
        ctx,
        package,
        action=payload.action,
        actor_user_id=actor_user_id,
        reason=payload.reason,
    )
    new_status = "approved" if payload.action == "approved" else "generated"
    transition(db, ctx, package, new_status, details={"decision": payload.action})
    db.commit()
    return read_package(db, package)


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
    bank_id: UUID,
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
    bank_id: UUID,
    package_id: UUID,
    *,
    channel: str,
    event: str,
    external_ref: str | None = None,
    detail: dict[str, Any] | None = None,
) -> RegulatoryPackageRead:
    """Record the regulator outcome: ``submitted -> acknowledged | rejected``."""
    if event not in ("acknowledged", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The regulator decision must be 'acknowledged' or 'rejected'.",
        )
    require_actor(ctx)
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    transition(db, ctx, package, event, details={"channel": channel})
    add_submission_event(
        db,
        ctx,
        package,
        channel=channel,
        event=event,
        external_ref=external_ref,
        detail=detail,
    )
    db.commit()
    return read_package(db, package)


def list_submission_events(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
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


def _build_channel(
    channel_code: str,
    *,
    config: dict[str, Any],
    prior_events: list[RegulatorySubmissionEvent],
) -> OrassSandboxChannel | EmailFallbackChannel:
    if channel_code == "orass_sandbox":
        return OrassSandboxChannel(config=config, prior_events=prior_events)
    if channel_code == "email":
        return EmailFallbackChannel(config=config, prior_events=prior_events)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Channel '{channel_code}' has no automated submission plugin.",
    )


def _load_channel_context(
    db: Session, ctx: TenantContext, bank_id: UUID, channel_code: str
) -> tuple[dict[str, Any], bool]:
    """The channel's config JSON plus whether stored credentials decrypted.

    Credentials are retrieved per submission cycle via the channel_config
    vault helpers ONLY for the ORASS channel when a config row exists, then
    discarded immediately — the sandbox transmits nothing and works
    credential-less; the decrypt run keeps the real-ORASS seam honest.
    """
    row = channel_config_row(db, ctx, bank_id, channel_code)
    if row is None:
        return {}, False
    credentials_present = False
    if channel_code == "orass_sandbox" and row.credential_ciphertext is not None:
        credentials = decrypt_channel_credentials(row)
        credentials_present = credentials is not None
        del credentials  # per-cycle retrieval: discard, never persist or log
    return dict(row.config), credentials_present


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
    if channel_code != "orass_sandbox":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A downtime email submission is deemed complete only after "
                "re-upload through ORASS (Notice BG/FMD/2026/07); submit via "
                "the 'orass_sandbox' channel."
            ),
        )
    assert latest is not None
    return True, latest.external_ref


def submit_package_via_channel(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
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

    artifacts = _package_artifacts(db, package)
    auto_exported = False
    if not artifacts:
        exporter = _resolve_exporter()
        artifacts = [exporter(db, ctx, package, "xlsx")]
        auto_exported = True

    prior_events = _submission_events_asc(db, package)
    config, credentials_present = _load_channel_context(db, ctx, bank.id, channel_code)
    channel = _build_channel(channel_code, config=config, prior_events=prior_events)
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

    detail = dict(channel.last_detail)
    if auto_exported:
        detail["auto_exported_kinds"] = ["xlsx"]
    if credentials_present:
        detail["credentials_used"] = True  # fingerprint-level fact only
    transition_details: dict[str, Any] = {"channel": channel_code}
    if is_reupload:
        detail["pending_orass_reupload"] = False
        detail["reupload_of"] = prior_email_ref
        transition_details["orass_reupload_of"] = prior_email_ref
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
    db: Session, ctx: TenantContext, bank_id: UUID, package_id: UUID
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
    config, _ = _load_channel_context(db, ctx, bank_id, latest.channel)
    channel = _build_channel(latest.channel, config=config, prior_events=events)
    poll_status, poll_detail = channel.poll_with_detail(latest.external_ref)
    poll_event = add_submission_event(
        db,
        ctx,
        package,
        channel=latest.channel,
        event="status_poll",
        external_ref=latest.external_ref,
        detail={**poll_detail, "result": poll_status},
    )
    if poll_status in ("acknowledged", "rejected"):
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


def export_package_artifact(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
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
    db: Session, ctx: TenantContext, bank_id: UUID, artifact_id: UUID
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
    db: Session, ctx: TenantContext, bank_id: UUID, artifact_id: UUID
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
    db: Session, ctx: TenantContext, bank_id: UUID, package_id: UUID
) -> EmailFallbackInstructionsRead:
    """Preview the send-ready email fallback bundle without submitting."""
    bank = get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    row = channel_config_row(db, ctx, bank.id, "email")
    config = dict(row.config) if row is not None else {}
    bundle = build_email_bundle(package, _package_artifacts(db, package), config)
    return EmailFallbackInstructionsRead(package_id=package.id, **bundle)
