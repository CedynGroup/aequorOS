"""Scheduler: due official runs enqueued + self-perpetuating tick, inert by default."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import BankReportingPeriod, Job, LiveMetric, RegulatoryRun, User
from app.schemas.live import OfficialRunRequest
from app.services import authorization, job_queue, live_view, pipeline, regulatory_fx, scheduler
from tests.api.helpers import ORG_1, USER_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def _grant_fx_run(
    session: Session,
    user_id: UUID,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
) -> None:
    authorization.create_role_binding(
        session,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.FX,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "scheduler-test"),
        reason="Authorize scheduled FX execution",
    )


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
    _grant_fx_run(db_session, USER_1)
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
    """LIVE_REFRESH_ENABLED keeps the recovery tick alive and initializes a
    bank whose accepted input has no live materialisation."""
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
    """A live tier newer than accepted ingestion is left alone regardless of age."""
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


def test_live_refresh_timer_does_not_retry_structural_unavailability(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            module="rating",
            status="na",
            metrics={"availability": "unavailable"},
            retry_classification="structural_unavailable",
            computed_at=utc_now() - timedelta(days=7),
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


def test_scheduled_fx_uses_later_authorized_analyst(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    owner = db_session.get(User, USER_1)
    assert owner is not None
    owner.created_at = utc_now() - timedelta(days=1)
    analyst = User(
        id=uuid4(),
        organization_id=ORG_1,
        email="scheduled.analyst@example.test",
        display_name="Scheduled analyst",
    )
    db_session.add(analyst)
    db_session.flush()
    _grant_fx_run(db_session, analyst.id)

    enqueued = scheduler._enqueue_due_official_runs(db_session, ORG_1, get_settings(), utc_now())

    assert enqueued == [SAMPLE_BANK_ID]
    job = db_session.scalar(select(Job).where(Job.job_type == "official_run"))
    assert job is not None
    assert job.payload["actor_user_id"] == str(analyst.id)
    pipeline.run_official(db_session, job)
    runs = list(db_session.scalars(select(RegulatoryRun).where(RegulatoryRun.module == "fx")))
    assert {run.scenario_code for run in runs} == set(regulatory_fx.FX_RUN_SCENARIO_CODES)
    assert all(run.status == "succeeded" for run in runs)
    assert all(run.created_by == analyst.id for run in runs)
    requested = job_queue.enqueue(
        db_session,
        ORG_1,
        "official_run",
        bank_id=SAMPLE_BANK_ID,
        payload={**job.payload, "actor_user_id": str(owner.id)},
    )
    pipeline.run_official(db_session, requested)
    fx_count = db_session.scalar(
        select(func.count()).select_from(RegulatoryRun).where(RegulatoryRun.module == "fx")
    )
    assert fx_count == len(runs)
    assert requested.payload["actor_user_id"] == str(owner.id)


@pytest.mark.parametrize("authority", ["viewer", "split", "inactive"])
def test_scheduled_fx_without_authorized_principal_denies_closed(
    db_session: Session, authority: str
) -> None:
    materialize_canonical_test_book(db_session)
    if authority == "split":
        _grant_fx_run(db_session, USER_1, SensitivityScope.AGGREGATED)
    elif authority == "inactive":
        _grant_fx_run(db_session, USER_1)
        user = db_session.get(User, USER_1)
        assert user is not None
        user.is_active = False
    db_session.commit()
    records = []
    sink_id = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        enqueued = scheduler._enqueue_due_official_runs(
            db_session, ORG_1, get_settings(), utc_now()
        )
    finally:
        logger.remove(sink_id)

    assert enqueued == []
    assert _count(db_session, "official_run") == 0
    assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0
    assert any(
        record.get("reason") == "no_authorized_scheduled_principal"
        and record.get("bank_id") == SAMPLE_BANK_ID
        and record.get("surface") == "scheduled_official_run"
        for record in records
    )


def test_official_worker_requires_explicit_actor(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _grant_fx_run(db_session, USER_1)
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "official_run",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": "2026-08-31"},
    )
    with pytest.raises(pipeline.PipelineError, match="active actor"):
        pipeline.run_official(db_session, job)
    assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0


def test_scheduled_and_requested_official_runs_coalesce_independently(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _grant_fx_run(db_session, USER_1)
    requester = User(
        id=uuid4(),
        organization_id=ORG_1,
        email="filing.requester@example.test",
        display_name="Filing requester",
        created_at=utc_now() + timedelta(seconds=1),
    )
    db_session.add(requester)
    db_session.flush()
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=requester.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.ANALYST,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.ALL,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "scheduler-test"),
        reason="Authorize requested official execution",
    )
    period_end = db_session.scalar(
        select(func.max(BankReportingPeriod.period_end)).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID
        )
    )
    assert period_end is not None
    ctx = TenantContext(
        organization_id=ORG_1,
        actor_user_id=requester.id,
        authorization_version=requester.authorization_version,
    )
    request = OfficialRunRequest(as_of_date=period_end, reason="Interactive filing")
    requested = live_view.mint_official_run(db_session, ctx, SAMPLE_BANK_ID, request)
    repeated = live_view.mint_official_run(db_session, ctx, SAMPLE_BANK_ID, request)
    assert repeated.job_id == requested.job_id
    now = utc_now().replace(year=period_end.year, month=period_end.month, day=period_end.day)
    for _ in range(2):
        scheduler._enqueue_due_official_runs(db_session, ORG_1, get_settings(), now)
    jobs = list(db_session.scalars(select(Job).where(Job.job_type == "official_run")))
    assert len(jobs) == 2
    interactive = next(job for job in jobs if job.id == requested.job_id)
    scheduled = next(job for job in jobs if job.id != requested.job_id)
    assert interactive.payload == {
        "as_of_date": period_end.isoformat(),
        "reason": request.reason,
        "actor_user_id": str(requester.id),
    }
    assert scheduled.payload["actor_user_id"] == str(USER_1)
    for job in jobs:
        pipeline.run_official(db_session, job)
    for actor_id in (requester.id, USER_1):
        runs = list(
            db_session.scalars(
                select(RegulatoryRun).where(
                    RegulatoryRun.module == "fx", RegulatoryRun.created_by == actor_id
                )
            )
        )
        assert {run.scenario_code for run in runs} == set(regulatory_fx.FX_RUN_SCENARIO_CODES)
        assert all(run.status == "succeeded" for run in runs)
