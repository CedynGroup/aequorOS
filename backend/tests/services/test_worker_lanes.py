"""The worker split: only the AI worker may claim, or reap, AI work.

The parity test requires ``icaap_ai_draft`` to be in ``HANDLERS``, which without
lanes would make EVERY worker able to claim it — including the daemon thread
inside the API process, which is the one process that must never hold a model
credential. Lanes make the safe answer the default: opting in is explicit and
per-process.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job
from app.services import job_queue
from app.worker import HANDLERS, WorkerConfigurationError, resolve_job_types
from tests.api.helpers import ORG_1


def test_the_default_selection_never_includes_the_ai_lane() -> None:
    """The safety property: adding an AI handler cannot widen the core fleet."""
    assert "icaap_ai_draft" not in resolve_job_types(None)
    assert "pipeline_refresh" in resolve_job_types(None)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_an_unset_selection_is_the_core_lane(raw: str | None) -> None:
    assert resolve_job_types(raw) == resolve_job_types(None)


def test_the_ai_lane_selects_exactly_the_ai_types() -> None:
    assert resolve_job_types("lane:ai") == ("icaap_ai_draft",)


def test_the_core_lane_is_the_default_selection() -> None:
    assert resolve_job_types("lane:core") == resolve_job_types(None)


def test_explicit_job_types_are_accepted() -> None:
    assert resolve_job_types("pipeline_refresh,official_run") == (
        "pipeline_refresh",
        "official_run",
    )


def test_an_unknown_job_type_is_refused() -> None:
    with pytest.raises(WorkerConfigurationError, match="unknown job type"):
        resolve_job_types("does_not_exist")


def test_an_unknown_lane_is_refused() -> None:
    with pytest.raises(WorkerConfigurationError, match="unknown or empty lane"):
        resolve_job_types("lane:nonexistent")


def test_mixing_the_ai_lane_with_another_lane_is_refused() -> None:
    """The process holding the model credential runs nothing else."""
    with pytest.raises(WorkerConfigurationError, match="mixes the ai lane"):
        resolve_job_types("lane:ai,pipeline_refresh")
    with pytest.raises(WorkerConfigurationError, match="mixes the ai lane"):
        resolve_job_types("icaap_ai_draft,official_run")


def test_the_inprocess_worker_uses_the_core_lane_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API process must not claim AI work even if misconfigured to."""
    import threading  # noqa: PLC0415

    from app import worker as worker_module  # noqa: PLC0415

    monkeypatch.setenv("RUN_INPROCESS_WORKER", "1")
    monkeypatch.setenv("WORKER_JOB_TYPES", "lane:ai")
    get_settings.cache_clear()
    monkeypatch.setattr(worker_module, "assert_worker_database_access", lambda: None)

    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(threading, "Thread", _Thread)
    worker_module.start_inprocess_worker()
    job_types = captured["kwargs"]["job_types"]  # type: ignore[index]
    assert "icaap_ai_draft" not in job_types


# --- lanes in the queue -----------------------------------------------------


def test_job_lanes_only_names_declared_job_types() -> None:
    assert set(job_queue.JOB_LANES) <= set(job_queue.JOB_TYPES)


def test_handlers_and_job_types_still_cover_each_other() -> None:
    """The existing parity invariant, restated beside the lanes that depend on it."""
    assert set(HANDLERS) == set(job_queue.JOB_TYPES)


def test_the_ai_reclaim_window_is_derived_from_settings() -> None:
    """Shorter than one legitimate call would reclaim a live job and resend it."""
    ai = get_settings().ai
    expected = ai.request_timeout_seconds * (ai.max_retries + 1) + ai.stale_margin_seconds
    window = job_queue.stale_after_for("icaap_ai_draft", timedelta(seconds=900))
    assert window == timedelta(seconds=expected)
    assert window > timedelta(seconds=ai.request_timeout_seconds)


def test_core_job_types_keep_the_fleet_default() -> None:
    default = timedelta(seconds=900)
    assert job_queue.stale_after_for("pipeline_refresh", default) == default


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
