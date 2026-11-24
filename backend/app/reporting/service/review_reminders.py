"""Reminders and escalation for returns waiting in review.

See backend/docs/notifications.md §Escalation.

A return that sits at one review stage longer than the organisation's
reminder window earns ONE reminder to everyone who can take that stage; past
the escalation window it earns ONE escalation to those people, the officers who
worked on it, and the organisation's owners — the people who can reassign it.
Both windows are the organisation's own settings (Settings → Notifications),
and both are idempotent per return, round and stage: a return sent back and
re-submitted starts a fresh clock.

"Waiting since" is the last decision recorded on the return in its current
round — the moment it was sent for approval, approved at the previous stage,
or sent back — because that is when the stage it is on started waiting.

``filing_review_reminders`` schedules itself for the next threshold any
waiting return will cross; a stage entering review seeds it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import RoleBundle
from app.identity.public import AuthorizationBinding
from app.models.job import Job
from app.notifications.public import Action, Fact, Message, OrgDelivery, catalog, org_delivery
from app.reporting.models.filing_workflow import PackageStageDecision, PackageWorkflowStage
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import filing_notifications
from app.services import job_queue

JOB_TYPE = "filing_review_reminders"


def _schedule(db: Session, organization_id: str, due_at: datetime) -> None:
    queued = db.scalars(
        select(Job)
        .where(
            Job.organization_id == organization_id,
            Job.job_type == JOB_TYPE,
            Job.status == "queued",
            Job.started_at.is_(None),
        )
        .order_by(Job.run_after)
    ).first()
    if queued is not None:
        current = _aware(queued.run_after)
        if current is None or current > due_at:
            queued.run_after = due_at
        return
    job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        payload={},
        run_after=due_at,
        max_attempts=3,
        locator=None,
        provenance=job_queue.worker_provenance("worker.filing_review_reminders"),
    )


def schedule_for_stage(db: Session, organization_id: str, *, now: datetime | None = None) -> None:
    """A return just started waiting: check back when its reminder would be due (no commit)."""
    settings = org_delivery(db, organization_id)
    if not settings.reminders_enabled:
        return
    _schedule(
        db,
        organization_id,
        (now or datetime.now(UTC)) + timedelta(hours=settings.reminder_after_hours),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _waiting(db: Session, organization_id: str) -> list[tuple[RegulatoryPackage, datetime]]:
    """Returns waiting at a review stage, with when they started waiting."""
    packages = list(
        db.scalars(
            select(RegulatoryPackage).where(
                RegulatoryPackage.organization_id == organization_id,
                RegulatoryPackage.status == "pending_approval",
                RegulatoryPackage.current_stage_seq.is_not(None),
            )
        )
    )
    waiting: list[tuple[RegulatoryPackage, datetime]] = []
    for package in packages:
        since = db.scalar(
            select(func.max(PackageStageDecision.created_at)).where(
                PackageStageDecision.organization_id == organization_id,
                PackageStageDecision.package_id == package.id,
            )
        )
        aware = _aware(since)
        if aware is not None:
            waiting.append((package, aware))
    return waiting


def _owners(db: Session, organization_id: str) -> list[UUID]:
    return list(
        db.scalars(
            select(AuthorizationBinding.principal_user_id).where(
                AuthorizationBinding.organization_id == organization_id,
                AuthorizationBinding.role_bundle == RoleBundle.ORG_OWNER.value,
                AuthorizationBinding.status == "active",
                AuthorizationBinding.principal_user_id.is_not(None),
            )
        )
    )


def _stage(db: Session, package: RegulatoryPackage) -> PackageWorkflowStage | None:
    return db.scalar(
        select(PackageWorkflowStage).where(
            PackageWorkflowStage.organization_id == package.organization_id,
            PackageWorkflowStage.package_id == package.id,
            PackageWorkflowStage.seq == package.current_stage_seq,
        )
    )


def _hours(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours >= 48:  # noqa: PLR2004 - two days reads better in days
        return f"{hours // 24} days"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def run_filing_review_reminders(session: Session, job: Job, *, now: datetime | None = None) -> None:
    """Worker handler: remind, escalate, and schedule the next check."""
    moment = now or datetime.now(UTC)
    ctx = TenantContext(organization_id=job.organization_id)
    settings = org_delivery(session, ctx.organization_id)
    if not settings.reminders_enabled:
        job.progress = {"skipped": "reminders_disabled"}
        return
    reminded = escalated = 0
    next_due: datetime | None = None
    for package, since in _waiting(session, ctx.organization_id):
        stage = _stage(session, package)
        if stage is None or stage.decision_kind == "prepare":
            continue
        waited = moment - since
        key = f"{package.id}:{package.workflow_round}:{stage.seq}"
        remind_at = since + timedelta(hours=settings.reminder_after_hours)
        escalate_at = since + timedelta(hours=settings.escalate_after_hours)
        if moment >= escalate_at:
            escalated += _escalate(session, ctx, package, stage, waited, key)
        elif moment >= remind_at:
            reminded += _remind(session, ctx, package, stage, waited, key, settings)
        upcoming = (
            remind_at if moment < remind_at else escalate_at if moment < escalate_at else None
        )
        if upcoming is not None and (next_due is None or upcoming < next_due):
            next_due = upcoming
    if next_due is not None:
        _schedule(session, ctx.organization_id, next_due)
    job.progress = {
        "reminded": reminded,
        "escalated": escalated,
        "next_check": None if next_due is None else next_due.isoformat(),
    }
    session.commit()


def _remind(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    stage: PackageWorkflowStage,
    waited: timedelta,
    key: str,
    settings: OrgDelivery,
) -> int:
    label = filing_notifications.return_label(package)
    return filing_notifications.publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_REVIEW_OVERDUE,
        message=Message(
            title=f"Reminder: {label} has waited {_hours(waited)} for {stage.title}",
            summary=(
                f"{label} has been waiting for {stage.title} for {_hours(waited)}. "
                f"It escalates to your organisation's owners after "
                f"{_hours(timedelta(hours=settings.escalate_after_hours))}."
            ),
            facts=(
                Fact("Return", label),
                Fact("Waiting at", stage.title),
                Fact("Waiting for", _hours(waited)),
            ),
            action=Action(f"Open {package.return_code}", filing_notifications.return_path(package)),
        ),
        recipients=filing_notifications.recipients_for_review(db, ctx, package),
        dedupe_key=f"{key}:reminder",
    )


def _escalate(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    stage: PackageWorkflowStage,
    waited: timedelta,
    key: str,
) -> int:
    label = filing_notifications.return_label(package)
    audience = [
        *filing_notifications.recipients_for_review(db, ctx, package),
        *filing_notifications.participants(db, package),
        *_owners(db, ctx.organization_id),
    ]
    return filing_notifications.publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_REVIEW_ESCALATED,
        message=Message(
            title=f"Escalation: {label} is stuck at {stage.title}",
            summary=(
                f"{label} has waited {_hours(waited)} for {stage.title} with no decision. "
                "Decide it, or reassign the stage to an officer who can."
            ),
            facts=(
                Fact("Return", label),
                Fact("Waiting at", stage.title),
                Fact("Waiting for", _hours(waited)),
            ),
            action=Action(f"Open {package.return_code}", filing_notifications.return_path(package)),
        ),
        recipients=audience,
        dedupe_key=f"{key}:escalation",
    )


__all__ = ["JOB_TYPE", "run_filing_review_reminders", "schedule_for_stage"]
