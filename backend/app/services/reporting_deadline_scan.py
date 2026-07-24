"""Daily reporting-deadline scan (docs/submission_pipeline_plan.md §W3).

Walks every bank in one organization, grades its reporting obligations over a
two-month horizon (``calendar.list_obligations``), and emits org-wide
notifications: ``due_soon`` once per T-7/T-3/T-1 threshold, ``overdue`` once
per day, and pending ORASS re-upload (BG/FMD/2026/07 downtime email
submissions) once per day.

Idempotency: each emission's deterministic dedupe key IS its ``type`` value —
``reporting.deadline.<kind>:<return_code>:<reporting_date>[:<scan_date>]`` —
scoped to the bank via ``entity_type='bank'``/``entity_id``. A second scan of
the same day finds the existing row and emits nothing. Daily kinds append the
scan date; threshold kinds do not, so each threshold fires exactly once.

Scheduling: ``enqueue_due_deadline_scan`` is called from the hourly
``scheduled_tick`` (see ``scheduler.run_tick``); the scan date rides the job
coalesce key so each org gets at most one ``reporting_deadline_scan`` job per
day. The worker handler is ``run_reporting_deadline_scan``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import Bank, Job, Notification, RegulatoryPackage
from app.schemas.regulatory_reporting import ReportingObligationRead
from app.services import job_queue, notifications
from app.services.regulatory_reporting import calendar
from app.services.regulatory_reporting.workflow import has_pending_orass_reupload

DEADLINE_SCAN = "reporting_deadline_scan"
_HORIZON_MONTHS = 2
# Tightest first: one scan emits at most the closest applicable threshold.
_DUE_SOON_THRESHOLDS = (1, 3, 7)


def scan_reporting_deadlines(
    db: Session, organization_id: str, *, as_of: date | None = None
) -> dict[str, Any]:
    """Scan one org's banks and emit deadline notifications (no commit)."""
    ctx = TenantContext(organization_id=organization_id)
    today = as_of or date.today()
    banks = list(db.scalars(select(Bank).where(Bank.organization_id == organization_id)))
    emitted = 0
    for bank in banks:
        obligations = calendar.list_obligations(
            db, ctx, bank.id, _HORIZON_MONTHS, as_of=today
        ).obligations
        for obligation in obligations:
            emitted += _scan_obligation(db, ctx, bank, obligation, today)
    return {
        "as_of": today.isoformat(),
        "banks_scanned": len(banks),
        "notifications_emitted": emitted,
    }


def _scan_obligation(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    obligation: ReportingObligationRead,
    today: date,
) -> int:
    """Emit the due-soon / overdue / re-upload notifications one obligation earns."""
    scope = f"{obligation.return_code}:{obligation.reporting_date.isoformat()}"
    label = f"{obligation.return_code} {obligation.reporting_date.isoformat()}"
    emitted = 0
    if obligation.rag == "due_soon":
        days_left = (obligation.due_date - today).days
        threshold = next((t for t in _DUE_SOON_THRESHOLDS if days_left <= t), None)
        if threshold is not None:
            emitted += _emit_once(
                db,
                ctx,
                bank,
                key=f"reporting.deadline.due_soon_{threshold}:{scope}",
                severity="warning",
                title=f"{label} due in {days_left} day(s)",
                body=(
                    f"{obligation.title} for {obligation.reporting_date.isoformat()} is due "
                    f"on {obligation.due_date.isoformat()} ({days_left} day(s) remaining)."
                ),
            )
    elif obligation.rag == "overdue":
        days_over = (today - obligation.due_date).days
        emitted += _emit_once(
            db,
            ctx,
            bank,
            key=f"reporting.deadline.overdue:{scope}:{today.isoformat()}",
            severity="critical",
            title=f"{label} is overdue",
            body=(
                f"{obligation.title} for {obligation.reporting_date.isoformat()} was due "
                f"on {obligation.due_date.isoformat()} ({days_over} day(s) ago) and has "
                "not been submitted."
            ),
        )
    if obligation.package_id is not None and obligation.package_status == "submitted":
        package = db.get(RegulatoryPackage, obligation.package_id)
        if package is not None and has_pending_orass_reupload(db, package):
            emitted += _emit_once(
                db,
                ctx,
                bank,
                key=f"reporting.deadline.reupload_pending:{scope}:{today.isoformat()}",
                severity="critical",
                title=f"{label} awaits its ORASS re-upload",
                body=(
                    f"{obligation.title} for {obligation.reporting_date.isoformat()} was "
                    "submitted via the downtime email fallback and is deemed complete "
                    "only after re-upload through ORASS (Notice BG/FMD/2026/07)."
                ),
            )
    return emitted


def _emit_once(  # noqa: PLR0913 - dedupe key + display envelope
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    key: str,
    severity: str,
    title: str,
    body: str,
) -> int:
    """Emit one org-wide notification unless its dedupe key already exists."""
    existing = db.scalar(
        select(Notification.id)
        .where(
            Notification.organization_id == ctx.organization_id,
            Notification.type == key,
            Notification.entity_type == "bank",
            Notification.entity_id == bank.id,
        )
        .limit(1)
    )
    if existing is not None:
        return 0
    notifications.emit(
        db,
        ctx,
        type=key,
        severity=severity,
        title=title,
        body=body,
        entity_type="bank",
        entity_id=bank.id,
    )
    return 1


def enqueue_due_deadline_scan(
    session: Session, organization_id: str, *, now: datetime | None = None
) -> bool:
    """Enqueue today's scan for one org — at most one job per org per day.

    The scan date rides the coalesce key; a completed job keeps its key, so
    (unlike plain coalescing, which only merges still-queued jobs) the
    existence check here is what holds the daily cadence across hourly ticks.
    """
    now = now or utc_now()
    key = f"deadline_scan:{organization_id}:{now.date().isoformat()}"
    existing = session.scalar(
        select(Job.id)
        .where(
            Job.organization_id == organization_id,
            Job.job_type == DEADLINE_SCAN,
            Job.coalesce_key == key,
        )
        .limit(1)
    )
    if existing is not None:
        return False
    job_queue.enqueue(session, organization_id, DEADLINE_SCAN, coalesce_key=key)
    return True


def run_reporting_deadline_scan(session: Session, job: Job) -> None:
    """Worker handler: scan one org's reporting deadlines and commit."""
    summary = scan_reporting_deadlines(session, job.organization_id)
    session.commit()
    job.progress = summary
