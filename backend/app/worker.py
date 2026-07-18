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
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import utc_now
from app.db.session import get_worker_sessionmaker
from app.models import Job
from app.services import (
    etl_dedup_jobs,
    job_queue,
    market_data_jobs,
    pipeline,
    scheduler,
    temenos_jobs,
)

logger = logging.getLogger(__name__)

Handler = Callable[[Session, Job], None]

HANDLERS: dict[str, Handler] = {
    "pipeline_refresh": pipeline.run_refresh,
    "official_run": pipeline.run_official,
    "scheduled_tick": scheduler.run_tick,
    "market_data_pull": market_data_jobs.run_market_data_pull,
    "temenos_pull": temenos_jobs.run_temenos_pull,
    "etl_dedup": etl_dedup_jobs.run_etl_dedup,
}


def _new_session(organization_id=None) -> Session:
    session = get_worker_sessionmaker()()
    if organization_id is not None:
        session.info["organization_id"] = organization_id
    return session


def run_once(job_types: tuple[str, ...] | None = None) -> bool:
    """Claim and dispatch a single job. Returns True if one was processed."""
    job_types = job_types or tuple(HANDLERS)
    with _new_session() as claim_session:
        job = job_queue.claim_next(claim_session, utc_now(), job_types)
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
            job_queue.fail_with_retry(session, claimed, str(exc) or type(exc).__name__)
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
    settings = get_settings()
    poll_interval = (
        poll_interval if poll_interval is not None else settings.worker.worker_poll_seconds
    )
    stale_after = timedelta(seconds=settings.worker.worker_stale_job_seconds)
    reap_interval = max(stale_after.total_seconds() / 2, poll_interval)
    job_types = job_types or tuple(HANDLERS)
    if settings.worker.official_run_enabled:
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
            worked = run_once(job_types)
        except Exception:  # noqa: BLE001 - a claim failure must not kill the loop
            logger.exception("Worker poll iteration failed")
            worked = False
        if time.monotonic() >= next_reap:
            _reap_stale(stale_after)
            next_reap = time.monotonic() + reap_interval
        if not worked:
            time.sleep(poll_interval)


def start_inprocess_worker() -> threading.Thread | None:
    """Start the poll loop on a daemon thread when RUN_INPROCESS_WORKER is set."""
    settings = get_settings()
    if not settings.worker.run_inprocess_worker:
        return None
    thread = threading.Thread(target=run_worker, name="live-engine-worker", daemon=True)
    thread.start()
    logger.info("In-process live-engine worker thread started")
    return thread


def main() -> None:  # pragma: no cover - process entrypoint
    settings = get_settings()
    configure_logging(settings.logging.log_level)
    run_worker()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
