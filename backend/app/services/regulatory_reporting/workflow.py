"""Package lifecycle state machine (docs/regulatory_reporting.md §2, §5).

Explicit allowed-transition table; every transition is audit-logged via
``record_event`` and submission-bound transitions additionally append a
``RegulatorySubmissionEvent``. Maker-checker: the approval decision must come
from a different user than the package generator (409 otherwise).

The API-facing submit/export endpoints are stubbed until the export/submission
wave ships concrete channels; the service-level ``submit_package`` /
``record_regulator_decision`` functions below already drive the full lifecycle
(``approved -> submitted -> acknowledged | rejected``) so channel plugins only
need to supply the ``external_ref``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    RegulatoryPackage,
    RegulatoryPackageApproval,
    RegulatorySubmissionEvent,
)
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageRead,
    SubmissionEventListRead,
    SubmissionEventRead,
)
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
    require_actor,
)

# §2 lifecycle. "generated" is re-entered on approval rejection (rework) and
# on a failed re-validation; "superseded" is reachable from any non-terminal
# state via regeneration (enforced in generation.py, listed here for audit).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"generated", "superseded"}),
    "generated": frozenset({"validated", "superseded"}),
    "validated": frozenset({"pending_approval", "generated", "superseded"}),
    "pending_approval": frozenset({"approved", "generated", "superseded"}),
    "approved": frozenset({"submitted", "superseded"}),
    "submitted": frozenset({"acknowledged", "rejected"}),
    "acknowledged": frozenset(),
    "rejected": frozenset({"superseded"}),
    "superseded": frozenset(),
}


def ensure_transition_allowed(package: RegulatoryPackage, new_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(package.status, frozenset())
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A package in status '{package.status}' cannot transition to "
                f"'{new_status}'."
            ),
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
        db.scalar(
            select(func.count()).select_from(RegulatorySubmissionEvent).where(*conditions)
        )
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
