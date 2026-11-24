"""Automatic regulator status sync for filed returns.

See backend/docs/notifications.md §Regulator sync.

Before this job, a return the regulator had already accepted stayed "submitted"
in AequorOS until somebody pressed "check status". ``regulatory_submission_sync``
asks every submitted return's channel where it stands, using the same code path
as that button (``workflow.check_regulator_status``), and records the
regulator's decision the moment it exists — which moves the return and notifies
everyone who worked on it. It also reads the regulator's open queries on
gateway filings and notifies the same people once per query.

Cadence follows the youngest return still waiting, because regulators decide
most filings within minutes of their automated validation and a few take days:

* every minute for the first 15 minutes after a filing,
* every 5 minutes for the first hour, every 15 minutes for the first day,
* then hourly, for up to 14 days, after which an officer checks by hand.

The job schedules itself (``run_after``), so it needs no hourly tick; the tick
only re-seeds a chain that was lost. A poll that only says "still validating"
writes nothing to the append-only submission log.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.identity.public import Bank
from app.models.job import Job
from app.reporting.models.packages import RegulatoryPackage, RegulatorySubmissionEvent
from app.services import job_queue

JOB_TYPE = "regulatory_submission_sync"

#: Channels the platform can ask about an outcome.
POLLABLE_CHANNELS = frozenset({"orass_api", "orass_sandbox", "reporting_gateway"})
#: After this, a return still waiting is an officer's to chase.
GIVE_UP_AFTER = timedelta(days=14)
#: How long after a gateway filing its queries are still read.
QUERY_WATCH = timedelta(days=60)
_CADENCE: tuple[tuple[timedelta, timedelta], ...] = (
    (timedelta(minutes=15), timedelta(minutes=1)),
    (timedelta(hours=1), timedelta(minutes=5)),
    (timedelta(days=1), timedelta(minutes=15)),
)
_SLOW = timedelta(hours=1)
_FIRST_CHECK = timedelta(minutes=1)


def next_interval(age: timedelta) -> timedelta:
    """How long to wait before asking again about a return filed ``age`` ago."""
    for limit, interval in _CADENCE:
        if age < limit:
            return interval
    return _SLOW


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def _schedule(db: Session, organization_id: str, due_at: datetime) -> None:
    """Queue a sync no later than ``due_at`` (pulling an existing one earlier)."""
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
        provenance=job_queue.worker_provenance("worker.regulatory_submission_sync"),
    )


def schedule_after_submission(
    db: Session, package: RegulatoryPackage, *, channel: str, now: datetime | None = None
) -> None:
    """Start checking a just-filed return a minute after it was sent (no commit)."""
    if channel not in POLLABLE_CHANNELS:
        return
    _schedule(db, package.organization_id, (now or datetime.now(UTC)) + _FIRST_CHECK)


def ensure_scheduled(db: Session, organization_id: str, *, now: datetime) -> bool:
    """Re-seed a lost chain from the hourly tick. Returns whether anything is being watched."""
    due = _next_due(db, organization_id, now)
    if due is None:
        return False
    _schedule(db, organization_id, due)
    return True


# ---------------------------------------------------------------------------
# What is being watched
# ---------------------------------------------------------------------------


def _latest_submissions(
    db: Session, organization_id: str, *, since: datetime
) -> dict[UUID, RegulatorySubmissionEvent]:
    """Each package's most recent ``submitted`` event through a pollable channel."""
    latest: dict[UUID, RegulatorySubmissionEvent] = {}
    for event in db.scalars(
        select(RegulatorySubmissionEvent)
        .where(
            RegulatorySubmissionEvent.organization_id == organization_id,
            RegulatorySubmissionEvent.event == "submitted",
            RegulatorySubmissionEvent.channel.in_(POLLABLE_CHANNELS),
            RegulatorySubmissionEvent.occurred_at >= since,
        )
        .order_by(RegulatorySubmissionEvent.occurred_at)
    ):
        latest[event.package_id] = event
    return latest


def _waiting(
    db: Session, organization_id: str, now: datetime
) -> list[tuple[RegulatoryPackage, RegulatorySubmissionEvent]]:
    submissions = _latest_submissions(db, organization_id, since=now - GIVE_UP_AFTER)
    if not submissions:
        return []
    packages = db.scalars(
        select(RegulatoryPackage).where(
            RegulatoryPackage.organization_id == organization_id,
            RegulatoryPackage.id.in_(list(submissions)),
            RegulatoryPackage.status == "submitted",
        )
    )
    return [(package, submissions[package.id]) for package in packages]


def _watched_gateway_banks(db: Session, organization_id: str, now: datetime) -> set[str]:
    since = now - QUERY_WATCH
    rows = db.execute(
        select(RegulatoryPackage.bank_id)
        .join(
            RegulatorySubmissionEvent,
            (RegulatorySubmissionEvent.package_id == RegulatoryPackage.id)
            & (RegulatorySubmissionEvent.organization_id == RegulatoryPackage.organization_id),
        )
        .where(
            RegulatoryPackage.organization_id == organization_id,
            RegulatorySubmissionEvent.channel == "reporting_gateway",
            RegulatorySubmissionEvent.event == "submitted",
            RegulatorySubmissionEvent.occurred_at >= since,
        )
        .distinct()
    ).scalars()
    return set(rows)


def _next_due(db: Session, organization_id: str, now: datetime) -> datetime | None:
    waiting = _waiting(db, organization_id, now)
    if waiting:
        youngest = min(now - (_aware(event.occurred_at) or now) for _package, event in waiting)
        return now + next_interval(youngest)
    if _watched_gateway_banks(db, organization_id, now):
        return now + _SLOW
    return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# The worker handler
# ---------------------------------------------------------------------------


def run_regulatory_submission_sync(
    session: Session, job: Job, *, now: datetime | None = None
) -> None:
    """Worker handler: check every waiting return, read open queries, reschedule."""
    moment = now or datetime.now(UTC)
    ctx = TenantContext(organization_id=job.organization_id)
    decided, unchanged, failed = _check_statuses(
        session, ctx, _waiting(session, ctx.organization_id, moment)
    )
    queries = _read_queries(session, ctx, moment)
    due = _next_due(session, ctx.organization_id, moment)
    if due is not None:
        _schedule(session, ctx.organization_id, due)
    job.progress = {
        "decided": decided,
        "still_waiting": unchanged,
        "failed": failed,
        "queries_notified": queries,
        "next_check": None if due is None else due.isoformat(),
    }
    session.commit()


def _check_statuses(
    session: Session,
    ctx: TenantContext,
    waiting: Sequence[tuple[RegulatoryPackage, RegulatorySubmissionEvent]],
) -> tuple[int, int, int]:
    from app.reporting.service import workflow  # noqa: PLC0415 - workflow imports this module
    from app.reporting.service.channels import ChannelError  # noqa: PLC0415

    decided = unchanged = failed = 0
    for package, _event in waiting:
        try:
            status, _poll_event = workflow.check_regulator_status(
                session, ctx, package, record_pending=False
            )
        except (workflow.SubmissionNotPollable, ChannelError) as exc:
            # Downtime and refusals are the channel's to report to an officer
            # who presses "check status"; the sync simply tries again later.
            session.rollback()
            logger.info("regulator status sync skipped {}: {}", package.id, type(exc).__name__)
            failed += 1
            continue
        except Exception:  # noqa: BLE001 - one return must never stop the others
            session.rollback()
            logger.exception("regulator status sync failed for {}", package.id)
            failed += 1
            continue
        session.commit()
        if status in ("acknowledged", "rejected", "declined"):
            decided += 1
        else:
            unchanged += 1
    return decided, unchanged, failed


def _read_queries(session: Session, ctx: TenantContext, now: datetime) -> int:
    from app.reporting.service import filing_notifications, gateway_queries  # noqa: PLC0415

    notified = 0
    for bank_id in sorted(_watched_gateway_banks(session, ctx.organization_id, now)):
        bank = session.get(Bank, bank_id)
        if bank is None:
            continue
        try:
            queries = gateway_queries.fetch_queries(session, ctx, bank)
        except Exception:  # noqa: BLE001 - a bad query read must not stop status sync
            session.rollback()
            logger.exception("regulator query read failed for {}", bank_id)
            continue
        for query in queries or ():
            if query.status != "awaiting_institution" or query.filing is None:
                continue
            package = session.get(RegulatoryPackage, query.filing.package_id)
            if package is None:
                continue
            question = next(
                (message.body for message in query.messages if message.author == "supervisor"),
                None,
            )
            reached = filing_notifications.regulator_query(
                session,
                ctx,
                package,
                number=query.number,
                subject=query.subject or None,
                question=question,
                due=None if query.due_at is None else query.due_at.date(),
            )
            notified += 1 if reached else 0
        session.commit()
    return notified


__all__ = [
    "GIVE_UP_AFTER",
    "JOB_TYPE",
    "POLLABLE_CHANNELS",
    "ensure_scheduled",
    "next_interval",
    "run_regulatory_submission_sync",
    "schedule_after_submission",
]
