"""Job-queue foundation: enqueue/coalesce, claim, retry backoff, allow-list."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import Job
from app.services import job_queue
from tests.api.helpers import ORG_1


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

    claimed = job_queue.claim_next(db_session, utc_now(), ("pipeline_refresh",))
    assert claimed is not None
    assert claimed.id == ready.id
    assert claimed.status == "running"
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
