"""Scheduler: due official runs enqueued + self-perpetuating tick, inert by default."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Job
from app.services import job_queue, scheduler
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.api.helpers import ORG_1


def _tick_job(db_session: Session) -> Job:
    job = job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()
    return job


def _count(db_session: Session, job_type: str, *, status: str | None = None) -> int:
    stmt = select(func.count()).select_from(Job).where(Job.job_type == job_type)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return db_session.scalar(stmt) or 0


def test_run_tick_is_inert_when_disabled(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.commit()
    tick = _tick_job(db_session)

    scheduler.run_tick(db_session, tick)

    assert tick.progress.get("status") == "inert"
    assert _count(db_session, "official_run") == 0
    # No reschedule: only the original tick exists.
    assert _count(db_session, "scheduled_tick") == 1


def test_run_tick_enqueues_official_and_reschedules_when_enabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_RUN_ENABLED", "true")
    get_settings.cache_clear()
    seed_sample_bank(db_session)
    db_session.commit()
    _tick_job(db_session)
    # Claim it the way the worker would, so it is running (not re-selected).
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    officials = list(db_session.scalars(select(Job).where(Job.job_type == "official_run")))
    assert len(officials) == 1
    assert officials[0].bank_id == SAMPLE_BANK_ID
    assert officials[0].status == "queued"
    assert tick.progress["official_runs_enqueued"] == [str(SAMPLE_BANK_ID)]

    # A fresh tick is queued for the next boundary (self-perpetuating).
    queued_ticks = list(
        db_session.scalars(
            select(Job).where(Job.job_type == "scheduled_tick", Job.status == "queued")
        )
    )
    assert len(queued_ticks) == 1
    assert queued_ticks[0].run_after is not None
    get_settings.cache_clear()
