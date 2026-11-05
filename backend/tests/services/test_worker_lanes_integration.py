"""The worker split: only the AI worker may claim, or reap, AI work.

The parity test requires ``icaap_ai_draft`` to be in ``HANDLERS``, which without
lanes would make EVERY worker able to claim it — including the daemon thread
inside the API process, which is the one process that must never hold a model
credential. Lanes make the safe answer the default: opting in is explicit and
per-process.

This module's other-suite cases live in ``test_worker_lanes.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job
from app.services import job_queue
from app.worker import resolve_job_types
from tests.support.helpers import ORG_1


def test_a_core_worker_never_reaps_a_bi_hop(db_session: Session) -> None:
    """The core worker's selection excludes the bi lane, so its reaper does too."""
    now = datetime.now(UTC)
    for job_type in ("pipeline_refresh", "bi_mart_backfill"):
        db_session.add(
            Job(
                organization_id=ORG_1,
                job_type=job_type,
                status="running",
                payload={},
                attempts=0,
                max_attempts=3,
                started_at=now - timedelta(hours=6),
            )
        )
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(
        db_session,
        now,
        stale_after=timedelta(seconds=900),
        job_types=resolve_job_types("lane:core"),
    )
    assert reclaimed == 1
    hop = db_session.query(Job).filter(Job.job_type == "bi_mart_backfill").one()
    assert hop.status == "running"

    # The BI worker reaps its own lane against the derived window.
    assert (
        job_queue.reclaim_stale(
            db_session,
            now,
            stale_after=timedelta(seconds=900),
            job_types=resolve_job_types("lane:bi"),
        )
        == 1
    )
    db_session.refresh(hop)
    assert hop.status == "queued"


def test_reclaim_only_touches_the_callers_own_lane(db_session: Session) -> None:
    """A core worker must never reap an AI job against the fleet default."""
    now = datetime.now(UTC)
    long_ago = now - timedelta(hours=6)
    for job_type in ("pipeline_refresh", "icaap_ai_draft"):
        db_session.add(
            Job(
                organization_id=ORG_1,
                job_type=job_type,
                status="running",
                payload={},
                attempts=0,
                max_attempts=3,
                started_at=long_ago,
            )
        )
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(
        db_session,
        now,
        stale_after=timedelta(seconds=900),
        job_types=resolve_job_types("lane:core"),
    )
    assert reclaimed == 1
    ai_job = db_session.query(Job).filter(Job.job_type == "icaap_ai_draft").one()
    assert ai_job.status == "running"


def test_reclaim_without_a_selection_keeps_the_legacy_behaviour(db_session: Session) -> None:
    """Existing callers and tests are unchanged by the new parameter."""
    now = datetime.now(UTC)
    db_session.add(
        Job(
            organization_id=ORG_1,
            job_type="pipeline_refresh",
            status="running",
            payload={},
            attempts=0,
            max_attempts=3,
            started_at=now - timedelta(hours=6),
        )
    )
    db_session.commit()
    assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 1


def test_healthcheck_filters_to_this_workers_own_heartbeat(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that passes on a PEER's heartbeat can never fail."""
    from app import worker as worker_module  # noqa: PLC0415
    from app.db.base import utc_now  # noqa: PLC0415
    from app.models import WorkerHeartbeat  # noqa: PLC0415

    db_session.add(
        WorkerHeartbeat(worker_id="risk-worker", started_at=utc_now(), last_seen_at=utc_now())
    )
    db_session.commit()

    monkeypatch.setenv("WORKER_ID", "risk-worker-ai")
    get_settings.cache_clear()
    monkeypatch.setattr(worker_module, "assert_worker_database_access", lambda: None)
    monkeypatch.setattr(worker_module, "_new_session", lambda *a, **k: _NoCloseSession(db_session))

    with pytest.raises(RuntimeError, match="No heartbeat"):
        worker_module.healthcheck()


class _NoCloseSession:
    """Wrap the test session so the worker's ``with`` block cannot close it."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *exc: object) -> bool:
        return False


def test_an_ai_job_gets_exactly_one_crash_rerun(db_session: Session) -> None:
    """``AI_JOB_MAX_ATTEMPTS`` = 1 bounds the at-least-once exposure.

    A worker that dies before the model call must recover, so one re-run is the
    point. A second death exhausts the attempt and the row is left for an
    operator rather than spending again.
    """
    now = datetime.now(UTC)
    window = job_queue.stale_after_for("icaap_ai_draft", timedelta(seconds=900))
    job = Job(
        organization_id=ORG_1,
        job_type="icaap_ai_draft",
        status="running",
        payload={},
        attempts=0,
        max_attempts=get_settings().ai.job_max_attempts,
        started_at=now - window - timedelta(seconds=60),
    )
    db_session.add(job)
    db_session.commit()

    ai_lane = resolve_job_types("lane:ai")
    assert job_queue.reclaim_stale(db_session, now, stale_after=window, job_types=ai_lane) == 1
    db_session.refresh(job)
    assert (job.status, job.attempts) == ("queued", 1)

    # The re-run dies too: the attempt is spent, so it fails rather than spending again.
    job.status = "running"
    job.started_at = now - window - timedelta(seconds=60)
    db_session.commit()
    assert job_queue.reclaim_stale(db_session, now, stale_after=window, job_types=ai_lane) == 1
    db_session.refresh(job)
    assert job.status == "failed"
