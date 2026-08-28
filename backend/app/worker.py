"""The live-engine background worker: claim → dispatch → complete/retry.

Run as a process (``python -m app.worker``) or in-process alongside the API
(``start_inprocess_worker`` gated by ``RUN_INPROCESS_WORKER``). Tests never run
the poll loop; they call the handlers directly.

RLS note: ``claim_next`` reads the jobs table across tenants, so on Postgres the
worker connection needs to see all tenants (a BYPASSRLS worker role). Per-job
work then runs on a session scoped to that job's organization.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import utc_now
from app.db.session import assert_worker_database_access, get_worker_sessionmaker
from app.models import Job, WorkerHeartbeat
from app.services import (
    database_direct_jobs,
    etl_dedup_jobs,
    job_queue,
    market_data_jobs,
    notification_email_mirror,
    pipeline,
    reporting_deadline_scan,
    scheduler,
    temenos_jobs,
)
from app.services.market_desk import capture_job as desk_capture_job

logger = logging.getLogger(__name__)

Handler = Callable[[Session, Job], None]

# HAZARD: HANDLERS and job_queue.JOB_TYPES must cover each other exactly — a
# JOB_TYPES entry with no handler here is enqueueable but never claimable, so
# its jobs sit "queued" forever (how notification_email_mirror was orphaned
# until 2026-08-09: enqueued by every tick, absent here). The parity test in
# tests/services/test_desk_capture_job.py enforces the invariant.
HANDLERS: dict[str, Handler] = {
    "pipeline_refresh": pipeline.run_refresh,
    "official_run": pipeline.run_official,
    "scheduled_tick": scheduler.run_tick,
    "market_data_pull": market_data_jobs.run_market_data_pull,
    "temenos_pull": temenos_jobs.run_temenos_pull,
    "etl_dedup": etl_dedup_jobs.run_etl_dedup,
    "reporting_deadline_scan": reporting_deadline_scan.run_reporting_deadline_scan,
    "notification_email_mirror": notification_email_mirror.run_notification_email_mirror,
    "database_direct_health": database_direct_jobs.run_database_direct_health,
    "desk_capture": desk_capture_job.run_desk_capture,
}


def _new_session(organization_id=None) -> Session:
    session = get_worker_sessionmaker()()
    if organization_id is not None:
        session.info["organization_id"] = organization_id
    return session


def _runtime_identity() -> str:
    configured = get_settings().worker.worker_id
    if configured is not None:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}"


def _record_heartbeat(
    session: Session,
    worker_id: str,
    *,
    worked: bool | None = None,
    error: Exception | None = None,
) -> None:
    """Upsert durable liveness evidence without coupling it to a tenant job."""
    now = utc_now()
    heartbeat = session.get(WorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(
            worker_id=worker_id,
            started_at=now,
            last_seen_at=now,
        )
        session.add(heartbeat)
    else:
        heartbeat.last_seen_at = now
    if worked:
        heartbeat.last_job_at = now
    if error is not None:
        heartbeat.last_error_at = now
        heartbeat.last_error = str(error) or type(error).__name__
    session.commit()


def _heartbeat_safely(
    worker_id: str,
    *,
    worked: bool | None = None,
    error: Exception | None = None,
) -> None:
    try:
        with _new_session() as session:
            _record_heartbeat(session, worker_id, worked=worked, error=error)
    except Exception:  # noqa: BLE001 - liveness must not terminate the worker
        logger.exception("Worker heartbeat write failed")


def run_once(
    job_types: tuple[str, ...] | None = None,
    *,
    worker_id: str | None = None,
) -> bool:
    """Claim and dispatch a single job. Returns True if one was processed."""
    job_types = job_types or tuple(HANDLERS)
    worker_id = worker_id or _runtime_identity()
    with _new_session() as claim_session:
        job = job_queue.claim_next(
            claim_session, utc_now(), job_types, claimed_by=worker_id
        )
        if job is None:
            return False
        organization_id = job.organization_id
        job_id = job.id

    with _new_session(organization_id) as session:
        claimed = session.get(Job, job_id)
        if claimed is None:  # pragma: no cover - claimed row must exist
            return True
        try:
            HANDLERS[claimed.job_type](session, claimed)
            job_queue.complete(session, claimed)
        except Exception as exc:  # noqa: BLE001 - any handler failure retries
            logger.exception("Job %s (%s) failed", job_id, claimed.job_type)
            session.rollback()
            job_queue.fail_with_retry(
                session,
                claimed,
                str(exc) or type(exc).__name__,
                commit=False,
            )
            if isinstance(exc, pipeline.TransientLiveRefreshError):
                pipeline.persist_transient_retry_state(session, claimed, exc)
            session.commit()
    return True


def _reap_stale(stale_after: timedelta) -> None:
    """Requeue jobs orphaned in ``running`` by a dead worker. Never raises."""
    try:
        with _new_session() as session:
            reclaimed = job_queue.reclaim_stale(session, utc_now(), stale_after=stale_after)
        if reclaimed:
            logger.warning("Reclaimed %d stale running job(s)", reclaimed)
    except Exception:  # noqa: BLE001 - the reaper must never kill the loop
        logger.exception("Stale-job reaper failed")


def run_worker(
    poll_interval: float | None = None,
    job_types: tuple[str, ...] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Poll loop: process due jobs, sleeping ``poll_interval`` when idle.

    On startup and then periodically, reclaims jobs left ``running`` by a crashed
    worker (this process's own prior instance, or any peer) so a mid-handler death
    doesn't strand a job forever.
    """
    assert_worker_database_access()
    settings = get_settings()
    poll_interval = (
        poll_interval if poll_interval is not None else settings.worker.worker_poll_seconds
    )
    stale_after = timedelta(seconds=settings.worker.worker_stale_job_seconds)
    reap_interval = max(stale_after.total_seconds() / 2, poll_interval)
    job_types = job_types or tuple(HANDLERS)
    worker_id = _runtime_identity()
    _heartbeat_safely(worker_id)
    # Seed when ANY scheduled feature is on — gating this on official runs
    # alone stranded every other scheduled feature (live refresh, connection
    # probes, vendor pulls) with no tick chain to run them.
    if scheduler.any_scheduling_enabled(settings):
        try:
            with _new_session() as session:
                scheduler.seed_ticks(session)
        except Exception:  # noqa: BLE001 - seeding is best-effort at startup
            logger.exception("Failed to seed scheduler ticks")
    _reap_stale(stale_after)  # clear orphans left by a prior crashed worker
    next_reap = time.monotonic() + reap_interval
    logger.info(
        "Live-engine worker started (job_types=%s, stale_after=%ss)",
        job_types,
        stale_after.total_seconds(),
    )
    while stop_event is None or not stop_event.is_set():
        try:
            worked = run_once(job_types, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001 - a claim failure must not kill the loop
            logger.exception("Worker poll iteration failed")
            _heartbeat_safely(worker_id, error=exc)
            worked = False
        else:
            _heartbeat_safely(worker_id, worked=worked)
        if time.monotonic() >= next_reap:
            _reap_stale(stale_after)
            next_reap = time.monotonic() + reap_interval
        if not worked:
            time.sleep(poll_interval)


def start_inprocess_worker() -> threading.Thread | None:
    """Start the poll loop on a daemon thread when RUN_INPROCESS_WORKER is set.

    The claim-visibility check runs HERE, on the calling thread, rather than
    only inside ``run_worker``: an exception raised on a daemon thread is
    printed by the interpreter's thread hook and the API carries on serving, so
    a misconfigured in-process worker would be exactly as silent as the bug this
    guards against (audit finding P0-16).
    """
    settings = get_settings()
    if not settings.worker.run_inprocess_worker:
        return None
    assert_worker_database_access()
    thread = threading.Thread(target=run_worker, name="live-engine-worker", daemon=True)
    thread.start()
    logger.info("In-process live-engine worker thread started")
    return thread


def healthcheck() -> None:
    """Container health probe: can this worker claim, and is it still polling?

    Two questions, because they have different answers and only one of them was
    ever askable. ``assert_worker_database_access`` catches the configuration
    that makes ``claim_next`` match zero rows forever (audit finding P0-16);
    the heartbeat catches a loop that is wedged or dead while the process is
    still alive. Raises on failure — the compose healthcheck reads the exit
    code.
    """
    assert_worker_database_access()
    settings = get_settings()
    stale_after = timedelta(seconds=settings.worker.worker_stale_job_seconds)
    with _new_session() as session:
        latest = session.query(func.max(WorkerHeartbeat.last_seen_at)).scalar()
    if latest is None:
        msg = "No worker heartbeat has ever been recorded."
        raise RuntimeError(msg)
    age = utc_now() - latest
    if age > stale_after:
        msg = (
            f"Worker heartbeat is {age.total_seconds():.0f}s old "
            f"(limit {stale_after.total_seconds():.0f}s)."
        )
        raise RuntimeError(msg)


def main() -> None:  # pragma: no cover - process entrypoint
    settings = get_settings()
    configure_logging(settings.logging.log_level)
    run_worker()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
