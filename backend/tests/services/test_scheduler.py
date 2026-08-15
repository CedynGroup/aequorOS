"""Scheduler: due official runs enqueued + self-perpetuating tick, inert by default."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models import BankReportingPeriod, Job, LiveMetric
from app.services import job_queue, scheduler
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
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
    materialize_canonical_test_book(db_session)
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
    materialize_canonical_test_book(db_session)
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


def test_run_tick_enqueues_live_refreshes_when_enabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LIVE_REFRESH_ENABLED alone keeps the tick alive and refreshes a bank
    whose live tier has never been computed — "Live" must mean today, not the
    last ingestion."""
    monkeypatch.setenv("LIVE_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    materialize_canonical_test_book(db_session)
    db_session.commit()
    _tick_job(db_session)
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    refreshes = list(db_session.scalars(select(Job).where(Job.job_type == "pipeline_refresh")))
    assert len(refreshes) == 1
    assert refreshes[0].bank_id == SAMPLE_BANK_ID
    assert refreshes[0].payload.get("as_of_date")
    assert tick.progress["live_refreshes_enqueued"] == 1
    assert _count(db_session, "scheduled_tick", status="queued") == 1
    get_settings.cache_clear()


def test_live_refresh_skips_a_fresh_live_tier(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live tier computed within the interval is left alone — the scheduled
    refresh exists to cure staleness, not to churn."""
    monkeypatch.setenv("LIVE_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    materialize_canonical_test_book(db_session)
    period_id = db_session.scalar(
        select(BankReportingPeriod.id)
        .where(
            BankReportingPeriod.organization_id == ORG_1,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    assert period_id is not None
    db_session.add(
        LiveMetric(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            reporting_period_id=period_id,
            module="liquidity",
            status="green",
            metrics={},
            computed_at=utc_now(),
        )
    )
    db_session.commit()
    _tick_job(db_session)
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    assert _count(db_session, "pipeline_refresh") == 0
    assert tick.progress["live_refreshes_enqueued"] == 0
    get_settings.cache_clear()
