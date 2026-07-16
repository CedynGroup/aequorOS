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
JOB_TYPES = ("pipeline_refresh", "official_run", "scheduled_tick", "market_data_pull")

# Retry backoff is 2**attempts * base seconds (10s, 20s, 40s at base=5).
_BACKOFF_BASE_SECONDS = 5


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
    organization_id: UUID,
    job_type: str,
    *,
    bank_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    coalesce_key: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
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
        entity_id=entity_id,
        bank_id=bank_id,
        payload=payload,
        run_after=run_after,
        coalesce_key=coalesce_key,
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def claim_next(db: Session, now: datetime, job_types: tuple[str, ...]) -> Job | None:
    """Claim the oldest due, queued job of one of ``job_types``.

    Uses ``FOR UPDATE SKIP LOCKED`` on Postgres so concurrent workers never
    claim the same row; the claim (status → running) is committed before return.
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
    job.started_at = now
    db.commit()
    return job


def complete(db: Session, job: Job, progress: dict[str, Any] | None = None) -> Job:
    job.status = "succeeded"
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
