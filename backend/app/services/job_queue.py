"""Durable job queue for the live ALM engine.

A thin service over the ``jobs`` table: ``enqueue`` (with debounce coalescing),
``claim_next`` (``FOR UPDATE SKIP LOCKED`` on Postgres so many workers can poll
one table safely), ``complete``, and ``fail_with_retry`` (exponential backoff up
to ``max_attempts``). ``jobs.job_type`` carries no DB CHECK, so the app-level
allow-list here is the single source of truth for valid types.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import Job


def _as_aware(value: datetime | None) -> datetime | None:
    """Treat a naive datetime as UTC.

    ``DateTime(timezone=True)`` round-trips as naive on SQLite, so a value read
    back from the DB must be normalized before it is compared to a fresh
    timezone-aware ``run_after``.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# Live-engine job types (jobs.job_type has no DB CHECK — validate in code).
#
# HAZARD: a new job type must land in THREE places in the same change —
# this tuple, ``worker.HANDLERS``, and (when the scheduler enqueues it behind
# a flag) ``scheduler.any_scheduling_enabled``. A type listed here but absent
# from HANDLERS is enqueueable yet never claimable: its jobs sit "queued"
# forever (the notification_email_mirror precedent — enqueued by the tick,
# orphaned until its handler entry landed). The parity test
# ``tests/services/test_desk_capture_job.py::test_every_job_type_has_a_worker_handler``
# enforces this; keep it green.
JOB_TYPES = (
    "pipeline_refresh",
    "official_run",
    "scheduled_tick",
    "market_data_pull",
    "temenos_pull",
    "etl_dedup",
    "reporting_deadline_scan",
    "notification_email_mirror",
    "database_direct_health",
    "desk_capture",
)

# Retry backoff is 2**attempts * base seconds (10s, 20s, 40s at base=5).
_BACKOFF_BASE_SECONDS = 5

#: Per-job-type stale-job windows, in seconds, overriding the deployment-wide
#: ``WORKER_STALE_JOB_SECONDS`` for handlers whose legitimate runtime is far
#: longer than the fleet default.
#:
#: ``reclaim_stale`` requires its window to EXCEED the longest legitimate handler
#: runtime, or a slow-but-alive job is reclaimed and run twice. The default
#: window is 900s; ``etl_dedup`` measurably ran **2h02m** on a 168k-record batch
#: on the primary, so three of its jobs were reclaimed as "worker presumed dead"
#: while still working, one of them twice — three concurrent copies of the same
#: handler. The fix is per-type, not a bigger global number: the global default
#: also governs how quickly a genuinely dead worker's jobs come back, so raising
#: it fleet-wide to accommodate one long handler would slow recovery for the nine
#: short ones.
#:
#: ``etl_dedup``'s 4h is ~2x its worst measured run, with the pairwise
#: counterparty pass now bounded (``etl_dedup_jobs._DEFERRED_COUNTERPARTY_MAX_RECORDS``)
#: so the measured 2h02m is an upper bound, not a typical case. A deployment that
#: raises that bound to get the full pass must raise this window with it.
#:
#: Everything NOT listed here is on the deployment default and therefore asserts
#: that it completes well inside it — see ``WorkerSettings.worker_stale_job_seconds``.
STALE_AFTER_OVERRIDES_SECONDS: dict[str, float] = {
    "etl_dedup": 4 * 60 * 60,
}


def stale_after_for(job_type: str, default: timedelta) -> timedelta:
    """The reclaim window for ``job_type``: its override, else the deployment default."""
    override = STALE_AFTER_OVERRIDES_SECONDS.get(job_type)
    return timedelta(seconds=override) if override is not None else default


class UnknownJobTypeError(ValueError):
    """A job_type outside the app-level allow-list was requested."""

    def __init__(self, job_type: str) -> None:
        super().__init__(f"Unknown job type {job_type!r}; expected one of {JOB_TYPES}.")
        self.job_type = job_type


def _validate_job_type(job_type: str) -> None:
    if job_type not in JOB_TYPES:
        raise UnknownJobTypeError(job_type)


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=(2**attempts) * _BACKOFF_BASE_SECONDS)


def enqueue(  # noqa: PLR0913 - queue insert carries the full dispatch envelope
    db: Session,
    organization_id: str,
    job_type: str,
    *,
    bank_id: str | None = None,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    coalesce_key: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    max_attempts: int = 3,
) -> Job:
    """Insert a queued job, or coalesce into an existing un-started one.

    When ``coalesce_key`` is given and a still-queued, never-claimed job with the
    same (org, key) exists, that job's ``run_after`` is bumped to the later of
    the two and payloads are merged — a burst of ingestions debounces into one
    refresh instead of a queue full of duplicates.
    """
    _validate_job_type(job_type)
    payload = payload or {}
    if coalesce_key is not None:
        existing = db.scalar(
            select(Job)
            .where(
                Job.organization_id == organization_id,
                Job.coalesce_key == coalesce_key,
                Job.status == "queued",
                Job.started_at.is_(None),
            )
            .order_by(Job.queued_at)
            .limit(1)
        )
        if existing is not None:
            candidates = [
                aware
                for aware in (_as_aware(existing.run_after), _as_aware(run_after))
                if aware is not None
            ]
            existing.run_after = max(candidates) if candidates else None
            existing.payload = {**existing.payload, **payload}
            if bank_id is not None:
                existing.bank_id = bank_id
            db.flush()
            return existing

    job = Job(
        organization_id=organization_id,
        job_type=job_type,
        status="queued",
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        bank_id=bank_id,
        payload=payload,
        run_after=run_after,
        coalesce_key=coalesce_key,
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def claim_next(
    db: Session,
    now: datetime,
    job_types: tuple[str, ...],
    *,
    claimed_by: str | None = None,
) -> Job | None:
    """Claim the oldest due, queued job of one of ``job_types``.

    Uses ``FOR UPDATE SKIP LOCKED`` on Postgres so concurrent workers never
    claim the same row; the claim (status → running) is committed before return.
    ``claimed_by`` identifies the worker runtime for operator incident review.
    """
    for job_type in job_types:
        _validate_job_type(job_type)
    stmt = (
        select(Job)
        .where(
            Job.status == "queued",
            or_(Job.run_after.is_(None), Job.run_after <= now),
            Job.job_type.in_(job_types),
        )
        .order_by(Job.queued_at)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.scalar(stmt)
    if job is None:
        return None
    job.status = "running"
    job.claimed_by = claimed_by
    job.started_at = now
    db.commit()
    return job


def complete(db: Session, job: Job, progress: dict[str, Any] | None = None) -> Job:
    job.status = "succeeded"
    # Clear any interim error (e.g. a reaper "reclaimed" note written while this
    # slow-but-alive handler was still running) — a succeeded job must not read
    # as failed in the queue history.
    job.error = None
    job.completed_at = utc_now()
    if progress is not None:
        job.progress = progress
    db.commit()
    return job


def fail_with_retry(db: Session, job: Job, error: str, *, now: datetime | None = None) -> Job:
    """Requeue the job with exponential backoff, or fail it past max attempts."""
    now = now or utc_now()
    job.error = error
    if job.attempts < job.max_attempts:
        job.attempts += 1
        job.status = "queued"
        job.started_at = None
        job.run_after = now + backoff(job.attempts)
    else:
        job.status = "failed"
        job.completed_at = now
    db.commit()
    return job


def is_exhausted(job: Job) -> bool:
    """Whether the queue will never touch this job again.

    Terminal (``failed``) with every attempt used. A ``failed`` job with retries
    left does not exist — ``fail_with_retry`` requeues until ``max_attempts`` —
    so this is the precise "needs a human" state, distinct from "will retry
    shortly", which is what made four stranded batches on the primary
    indistinguishable from four pending ones.
    """
    return job.status == "failed" and job.attempts >= job.max_attempts


def latest_for_entity(
    db: Session,
    *,
    organization_id: str,
    job_type: str,
    entity_id: UUID | str,
) -> Job | None:
    """The most recently queued job of ``job_type`` for one entity, org-scoped.

    Explicitly organization-scoped: callers include the operator control plane,
    whose session bypasses RLS, so the filter here is the isolation.
    """
    _validate_job_type(job_type)
    return db.scalar(
        select(Job)
        .where(
            Job.organization_id == organization_id,
            Job.job_type == job_type,
            Job.entity_id == str(entity_id),
        )
        .order_by(Job.queued_at.desc())
        .limit(1)
    )


def reclaim_stale(db: Session, now: datetime, *, stale_after: timedelta) -> int:
    """Reclaim jobs stuck in ``running`` past ``stale_after`` and return the count.

    ``claim_next`` commits ``status='running'`` before the handler runs, and only
    ``complete``/``fail_with_retry`` move a job out of ``running``. A worker that
    dies *between* those points — SIGKILL/SIGTERM mid-handler, OOM, or a severed
    DB connection — leaves the row ``running`` forever, because a raising handler
    is the only failure the poll loop catches. Nothing else resets it.

    This reaper treats such a row as a used attempt (so a job that reliably kills
    its worker eventually lands in ``failed`` for investigation instead of being
    reclaimed forever) and otherwise requeues it for immediate re-dispatch.

    ``stale_after`` must exceed the longest legitimate handler runtime, or a
    slow-but-alive job will be reclaimed and run twice. That requirement is now
    met PER JOB TYPE: ``stale_after`` is the deployment-wide default and
    :data:`STALE_AFTER_OVERRIDES_SECONDS` widens it for the handlers that
    genuinely run for hours. Before that, ``etl_dedup`` (measured 2h02m) was
    reclaimed against the 900s default while still running — the reaper requeued
    one job twice, so three copies of the same handler ran concurrently, and the
    batch ended ``failed`` with "worker presumed dead" despite nothing having
    died.
    """
    running = db.scalars(
        select(Job).where(Job.status == "running", Job.completed_at.is_(None))
    ).all()
    reclaimed = 0
    for job in running:
        window = stale_after_for(job.job_type, stale_after)
        started = _as_aware(job.started_at)
        if started is None or started >= now - window:
            continue
        job.error = (
            f"reclaimed: running since {started.isoformat()} exceeded {window} "
            "without completing (worker presumed dead)"
        )
        if job.attempts < job.max_attempts:
            job.attempts += 1
            job.status = "queued"
            job.started_at = None
            job.run_after = now
        else:
            job.status = "failed"
            job.completed_at = now
        reclaimed += 1
    if reclaimed:
        db.commit()
    return reclaimed
