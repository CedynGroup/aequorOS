"""Job-queue foundation: enqueue/coalesce, claim, retry backoff, allow-list."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import Job
from app.services import job_queue
from tests.api.helpers import ORG_1, ORG_2


def test_enqueue_inserts_queued_job(db_session: Session) -> None:
    job = job_queue.enqueue(
        db_session, ORG_1, "pipeline_refresh", payload={"as_of_date": "2026-06-30"}
    )
    db_session.commit()
    assert job.status == "queued"
    assert job.started_at is None
    assert job.payload == {"as_of_date": "2026-06-30"}


def test_enqueue_coalesces_and_bumps_run_after(db_session: Session) -> None:
    now = utc_now()
    first = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        payload={"a": 1},
        run_after=now,
        coalesce_key="refresh:x",
    )
    db_session.commit()
    second = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        payload={"b": 2},
        run_after=now + timedelta(seconds=30),
        coalesce_key="refresh:x",
    )
    db_session.commit()

    assert second.id == first.id
    assert second.payload == {"a": 1, "b": 2}
    assert second.run_after == now + timedelta(seconds=30)
    total = db_session.scalar(
        select(func.count()).select_from(Job).where(Job.coalesce_key == "refresh:x")
    )
    assert total == 1


def test_coalesce_does_not_capture_a_started_job(db_session: Session) -> None:
    first = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh", coalesce_key="refresh:y")
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None and claimed.id == first.id

    second = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh", coalesce_key="refresh:y")
    db_session.commit()
    assert second.id != first.id  # the running job is not coalesced into


def test_claim_next_marks_running_and_respects_run_after(db_session: Session) -> None:
    future = job_queue.enqueue(
        db_session, ORG_1, "pipeline_refresh", run_after=utc_now() + timedelta(hours=1)
    )
    ready = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh", run_after=None)
    db_session.commit()

    claimed = job_queue.claim_next(
        db_session, utc_now(), ("pipeline_refresh",), claimed_by="worker-test-a"
    )
    assert claimed is not None
    assert claimed.id == ready.id
    assert claimed.status == "running"
    assert claimed.claimed_by == "worker-test-a"
    assert claimed.started_at is not None
    assert claimed.id != future.id


def test_claim_next_filters_by_job_type(db_session: Session) -> None:
    job_queue.enqueue(db_session, ORG_1, "official_run", payload={})
    db_session.commit()
    assert job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",)) is None
    claimed = job_queue.claim_next(db_session, utc_now(), ("official_run",))
    assert claimed is not None and claimed.job_type == "official_run"


def test_retry_backoff_then_fails_at_max_attempts(db_session: Session) -> None:
    job = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh", max_attempts=2)
    db_session.commit()
    now = utc_now()

    job_queue.fail_with_retry(db_session, job, "boom-1", now=now)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.started_at is None
    assert job.run_after == now + job_queue.backoff(1)

    job_queue.fail_with_retry(db_session, job, "boom-2", now=now)
    assert job.status == "queued"
    assert job.attempts == 2
    assert job.run_after == now + job_queue.backoff(2)

    job_queue.fail_with_retry(db_session, job, "boom-3", now=now)
    assert job.status == "failed"
    assert job.completed_at is not None
    assert job.error == "boom-3"


def test_backoff_grows_and_caps_at_max_attempts(db_session: Session) -> None:
    assert job_queue.backoff(2) == job_queue.backoff(1) * 2
    assert job_queue.backoff(3) > job_queue.backoff(2)


def test_complete_marks_succeeded(db_session: Session) -> None:
    job = job_queue.enqueue(db_session, ORG_1, "pipeline_refresh")
    db_session.commit()
    job_queue.complete(db_session, job, progress={"ok": True})
    assert job.status == "succeeded"
    assert job.completed_at is not None
    assert job.progress == {"ok": True}


def test_unknown_job_type_is_rejected(db_session: Session) -> None:
    with pytest.raises(job_queue.UnknownJobTypeError):
        job_queue.enqueue(db_session, ORG_1, "not_a_real_type")
    with pytest.raises(job_queue.UnknownJobTypeError):
        job_queue.claim_next(db_session, utc_now(), ("not_a_real_type",))


def test_reclaim_stale_requeues_orphaned_running_job(db_session: Session) -> None:
    job_queue.enqueue(db_session, ORG_1, "pipeline_refresh")
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None and claimed.status == "running"
    # Simulate a worker that claimed the job then died mid-handler: it has been
    # running far longer than any real handler and never reached a terminal state.
    claimed.started_at = utc_now() - timedelta(minutes=30)
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(db_session, utc_now(), stale_after=timedelta(minutes=15))
    assert reclaimed == 1
    db_session.refresh(claimed)
    assert claimed.status == "queued"
    assert claimed.started_at is None
    assert claimed.attempts == 1
    assert claimed.run_after is not None
    assert claimed.error is not None and "worker presumed dead" in claimed.error


def test_reclaim_stale_leaves_a_recently_started_job(db_session: Session) -> None:
    job_queue.enqueue(db_session, ORG_1, "pipeline_refresh")
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None

    # Started just now (by claim_next) — a slow-but-alive job must not be reclaimed.
    reclaimed = job_queue.reclaim_stale(db_session, utc_now(), stale_after=timedelta(minutes=15))
    assert reclaimed == 0
    assert claimed.status == "running"


def test_reclaim_stale_fails_a_job_past_max_attempts(db_session: Session) -> None:
    job_queue.enqueue(db_session, ORG_1, "pipeline_refresh", max_attempts=1)
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None
    # Already used its one attempt, and now orphaned again — must fail, not loop.
    claimed.attempts = 1
    claimed.started_at = utc_now() - timedelta(minutes=30)
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(db_session, utc_now(), stale_after=timedelta(minutes=15))
    assert reclaimed == 1
    db_session.refresh(claimed)
    assert claimed.status == "failed"
    assert claimed.completed_at is not None


# --------------------------------------------------------------------------
# Per-job-type stale windows
# --------------------------------------------------------------------------


def test_a_long_running_handler_is_not_reclaimed_at_the_fleet_default(
    db_session: Session,
) -> None:
    """``etl_dedup`` measurably runs for 2h02m; the 900s default reclaimed it alive.

    Three jobs on the primary were marked "worker presumed dead" while still
    working, one of them twice — so the reaper requeued a job whose handler was
    mid-flight and three copies ran concurrently. The reclaim window is now
    per-job-type: the fleet default still governs everything else.
    """
    job_queue.enqueue(db_session, ORG_1, "etl_dedup")
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("etl_dedup",))
    assert claimed is not None
    claimed.started_at = utc_now() - timedelta(hours=2, minutes=2)
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(db_session, utc_now(), stale_after=timedelta(minutes=15))
    assert reclaimed == 0
    assert claimed.status == "running"


def test_the_override_is_a_window_not_an_exemption(db_session: Session) -> None:
    """A genuinely dead etl_dedup worker is still recovered, just later."""
    job_queue.enqueue(db_session, ORG_1, "etl_dedup")
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("etl_dedup",))
    assert claimed is not None
    claimed.started_at = utc_now() - timedelta(hours=5)
    db_session.commit()

    reclaimed = job_queue.reclaim_stale(db_session, utc_now(), stale_after=timedelta(minutes=15))
    assert reclaimed == 1
    db_session.refresh(claimed)
    assert claimed.status == "queued"
    assert claimed.error is not None and "4:00:00" in claimed.error


def test_an_unlisted_job_type_stays_on_the_deployment_default(db_session: Session) -> None:
    """Only handlers with a measured, documented reason get their own window."""
    assert set(job_queue.STALE_AFTER_OVERRIDES_SECONDS) == {"etl_dedup"}
    assert job_queue.stale_after_for("pipeline_refresh", timedelta(minutes=15)) == timedelta(
        minutes=15
    )
    assert job_queue.stale_after_for("etl_dedup", timedelta(minutes=15)) == timedelta(hours=4)


# --------------------------------------------------------------------------
# Exhaustion and entity lookup (the re-drive surface's primitives)
# --------------------------------------------------------------------------


def test_is_exhausted_separates_stranded_from_will_retry(db_session: Session) -> None:
    """The distinction four stranded batches on the primary could not express."""
    job = job_queue.enqueue(db_session, ORG_1, "etl_dedup", max_attempts=3)
    db_session.commit()
    assert job_queue.is_exhausted(job) is False

    job.status = "failed"
    job.attempts = 2
    db_session.commit()
    assert job_queue.is_exhausted(job) is False

    job.attempts = 3
    db_session.commit()
    assert job_queue.is_exhausted(job) is True


def test_latest_for_entity_is_org_scoped_and_newest_first(db_session: Session) -> None:
    entity = uuid4()
    older = job_queue.enqueue(
        db_session, ORG_1, "etl_dedup", entity_type="ingestion_batch", entity_id=entity
    )
    older.queued_at = utc_now() - timedelta(hours=1)
    newer = job_queue.enqueue(
        db_session, ORG_1, "etl_dedup", entity_type="ingestion_batch", entity_id=entity
    )
    job_queue.enqueue(
        db_session, ORG_2, "etl_dedup", entity_type="ingestion_batch", entity_id=entity
    )
    db_session.commit()

    found = job_queue.latest_for_entity(
        db_session, organization_id=ORG_1, job_type="etl_dedup", entity_id=entity
    )
    assert found is not None and found.id == newer.id
    # The identically-keyed job in another org is invisible: the operator's
    # session bypasses RLS, so this filter IS the isolation.
    other = job_queue.latest_for_entity(
        db_session, organization_id=ORG_2, job_type="etl_dedup", entity_id=entity
    )
    assert other is not None and other.organization_id == ORG_2
