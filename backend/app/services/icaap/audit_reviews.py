"""The independent review of the ICAAP (¶42, ¶49(n)).

Independence is checked against this cycle's own record, not against a job
title: anybody who wrote its text, bound its figures, quantified a Pillar 2
item or answered a challenge is a participant, and a participant cannot record
the review of their own work. The check runs twice — once in the route's
authority dependency, so the refusal is an authorization decision, and once
here, so a race between the two cannot slip through.

A finalised review is SEALED. Correcting it means recording a superseding
review, which leaves the original exactly as it was written; the database
refuses an update either way.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.models.icaap import IcaapAttachment, IcaapCycle
from app.models.icaap_risk_capital import IcaapAuditReview
from app.schemas.icaap_risk_capital import (
    IcaapAuditFindingWrite,
    IcaapAuditReviewCreate,
    IcaapAuditReviewListRead,
    IcaapAuditReviewRead,
    IcaapAuditReviewUpdate,
    IcaapReason,
)
from app.services.audit import record_event
from app.services.icaap import guards, participants

#: The attachment kind a reviewer's own report is filed under. Uploading it
#: must never make the reviewer a participant of the cycle they reviewed.
REPORT_KIND = participants.REVIEWER_ATTACHMENT_KIND

_OPEN = "open"

#: The only reviews an impersonated examiner may read. A draft is an internal
#: audit opinion the reviewer has not finalised — findings still being written,
#: an overall opinion that may yet change. A supervisor reading one would be
#: reading a working paper and could quote a finding the bank's own auditor
#: later withdrew, which is the boundary ``guards.get_cycle_or_404`` draws for
#: the cycle itself.
EXAMINER_VISIBLE: tuple[str, ...] = ("finalised", "superseded")


def _rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapAuditReview]:
    statement = select(IcaapAuditReview).where(
        IcaapAuditReview.organization_id == access.ctx.organization_id,
        IcaapAuditReview.cycle_id == cycle.id,
    )
    if access.examiner:
        statement = statement.where(IcaapAuditReview.status.in_(EXAMINER_VISIBLE))
    return list(
        db.scalars(
            statement.order_by(
                IcaapAuditReview.performed_on.desc(), IcaapAuditReview.created_at.desc()
            )
        )
    )


def _findings(row: IcaapAuditReview) -> list[IcaapAuditFindingWrite]:
    out: list[IcaapAuditFindingWrite] = []
    for entry in row.findings or []:
        if isinstance(entry, dict):
            out.append(IcaapAuditFindingWrite.model_validate(entry))
    return out


def _read(row: IcaapAuditReview) -> IcaapAuditReviewRead:
    findings = _findings(row)
    return IcaapAuditReviewRead(
        id=row.id,
        status=row.status,  # pyright: ignore[reportArgumentType]
        review_kind=row.review_kind,  # pyright: ignore[reportArgumentType]
        reviewer_function=row.reviewer_function,
        scope=row.scope,
        frequency_statement=row.frequency_statement,
        performed_from=row.performed_from,
        performed_on=row.performed_on,
        period_covered=row.period_covered,
        reviewed_cycle_id=row.reviewed_cycle_id,
        overall_opinion=row.overall_opinion,  # pyright: ignore[reportArgumentType]
        findings=findings,
        open_findings_count=len([entry for entry in findings if entry.status == _OPEN]),
        report_attachment_id=row.report_attachment_id,
        independence_statement=row.independence_statement,
        recorded_by=row.recorded_by,
        finalised_at=row.finalised_at,
        finalised_by=row.finalised_by,
        supersedes_review_id=row.supersedes_review_id,
        superseded_at=row.superseded_at,
        superseded_by_review_id=row.superseded_by_review_id,
        row_rev=row.row_rev,
        created_at=row.created_at,
    )


def list_reviews(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapAuditReviewListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    rows = _rows(db, access, cycle)
    reads = [_read(row) for row in rows]
    finalised = [read for read in reads if read.status == "finalised"]
    latest = finalised[0] if finalised else None
    return IcaapAuditReviewListRead(
        cycle_id=cycle.id,
        reviews=reads,
        latest_review_date=None if latest is None else latest.performed_on,
        latest_review_opinion=None if latest is None else latest.overall_opinion,
        open_findings_count=sum(read.open_findings_count for read in finalised),
    )


def independence_conditions_passed(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, user_id: UUID | None
) -> bool:
    if user_id is None:
        return False
    return user_id not in participants.cycle_participants(db, access, cycle)


def _require_independent(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> UUID:
    actor = guards.actor_id(access)
    reasons = participants.participation_reasons(db, access, cycle, actor)
    if reasons:
        raise guards.conflict(
            "reviewer_not_independent",
            "You prepared parts of this ICAAP, so you cannot record its independent "
            "review. Somebody outside the preparation has to.",
            reasons=list(reasons),
        )
    return actor


def _check_report(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, attachment_id: UUID | None
) -> None:
    if attachment_id is None:
        return
    row = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.id == attachment_id,
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
    )
    if row is None:
        raise guards.unprocessable(
            "evidence_required", "That report file is not part of this ICAAP."
        )
    if row.kind != REPORT_KIND:
        raise guards.unprocessable(
            "evidence_required",
            "The review report has to be attached as an independent review report.",
            kind=row.kind,
        )


def _review_or_404(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, review_id: UUID
) -> IcaapAuditReview:
    row = db.scalar(
        select(IcaapAuditReview).where(
            IcaapAuditReview.id == review_id,
            IcaapAuditReview.organization_id == access.ctx.organization_id,
            IcaapAuditReview.cycle_id == cycle.id,
        )
    )
    if row is None:
        guards.not_found()
    return row


def create_review(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapAuditReviewCreate
) -> IcaapAuditReviewRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    actor = _require_independent(db, access, cycle)
    _check_report(db, access, cycle, payload.report_attachment_id)
    if payload.supersedes_review_id is not None:
        predecessor = _review_or_404(db, access, cycle, payload.supersedes_review_id)
        if predecessor.status != "finalised":
            raise guards.conflict(
                "review_sealed",
                "Only a finalised review can be superseded.",
                status=predecessor.status,
            )
    row = IcaapAuditReview(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        status="draft",
        review_kind=payload.review_kind,
        reviewer_function=payload.reviewer_function,
        scope=payload.scope,
        frequency_statement=payload.frequency_statement,
        performed_from=payload.performed_from,
        performed_on=payload.performed_on,
        period_covered=payload.period_covered,
        reviewed_cycle_id=payload.reviewed_cycle_id or cycle.id,
        overall_opinion=payload.overall_opinion,
        findings=[finding.model_dump(mode="json") for finding in payload.findings],
        report_attachment_id=payload.report_attachment_id,
        independence_statement=payload.independence_statement,
        recorded_by=actor,
        supersedes_review_id=payload.supersedes_review_id,
        row_rev=0,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.audit_review.created",
        entity_type="icaap_audit_review",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "review_kind": row.review_kind,
            "performed_on": row.performed_on.isoformat(),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def update_review(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    review_id: UUID,
    payload: IcaapAuditReviewUpdate,
) -> IcaapAuditReviewRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _review_or_404(db, access, cycle, review_id)
    if row.status != "draft":
        raise guards.conflict(
            "review_sealed",
            "A finalised review cannot be edited. Record a superseding review instead.",
            status=row.status,
        )
    _require_independent(db, access, cycle)
    if payload.base_rev != row.row_rev:
        raise guards.conflict(
            "row_rev_conflict",
            "Somebody else changed this review while you were editing it.",
            current_rev=row.row_rev,
        )
    _check_report(db, access, cycle, payload.report_attachment_id)
    for field in (
        "reviewer_function",
        "scope",
        "frequency_statement",
        "performed_on",
        "performed_from",
        "period_covered",
        "overall_opinion",
        "report_attachment_id",
        "independence_statement",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if payload.findings is not None:
        row.findings = [finding.model_dump(mode="json") for finding in payload.findings]
    row.row_rev += 1
    record_event(
        db,
        access.ctx,
        event_type="icaap.audit_review.updated",
        entity_type="icaap_audit_review",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "row_rev": row.row_rev, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def finalise_review(
    db: Session, access: IcaapAccess, cycle_id: UUID, review_id: UUID, payload: IcaapReason
) -> IcaapAuditReviewRead:
    """Seal the review. After this it can only be superseded."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_editable(cycle)
    row = _review_or_404(db, access, cycle, review_id)
    if row.status != "draft":
        raise guards.conflict(
            "review_sealed", "This review has already been finalised.", status=row.status
        )
    actor = _require_independent(db, access, cycle)
    now = utc_now()
    row.status = "finalised"
    row.finalised_at = now
    row.finalised_by = actor
    if row.supersedes_review_id is not None:
        predecessor = _review_or_404(db, access, cycle, row.supersedes_review_id)
        if predecessor.status == "finalised":
            predecessor.status = "superseded"
            predecessor.superseded_at = now
            predecessor.superseded_by_review_id = row.id
            record_event(
                db,
                access.ctx,
                event_type="icaap.audit_review.superseded",
                entity_type="icaap_audit_review",
                entity_id=predecessor.id,
                details={"superseded_by": str(row.id)},
            )
    record_event(
        db,
        access.ctx,
        event_type="icaap.audit_review.finalised",
        entity_type="icaap_audit_review",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "overall_opinion": row.overall_opinion,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def review_payload(read: IcaapAuditReviewListRead) -> dict[str, Any]:
    return {
        "reviews": [
            {
                "review_kind": review.review_kind,
                "reviewer_function": review.reviewer_function,
                "performed_on": review.performed_on.isoformat(),
                "overall_opinion": review.overall_opinion,
                "open_findings": review.open_findings_count,
            }
            for review in read.reviews
            if review.status == "finalised"
        ]
    }


def latest_finalised(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> tuple[date | None, str | None, int]:
    rows = [row for row in _rows(db, access, cycle) if row.status == "finalised"]
    if not rows:
        return None, None, 0
    latest = rows[0]
    open_findings = sum(
        len([entry for entry in _findings(row) if entry.status == _OPEN]) for row in rows
    )
    return latest.performed_on, latest.overall_opinion, open_findings


__all__ = [
    "EXAMINER_VISIBLE",
    "REPORT_KIND",
    "create_review",
    "finalise_review",
    "independence_conditions_passed",
    "latest_finalised",
    "list_reviews",
    "review_payload",
    "update_review",
]
