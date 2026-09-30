"""Scheduler: due official runs enqueued + self-perpetuating tick, inert by default."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
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
from app.models import (
    BankReportingPeriod,
    BiMartBuild,
    CurrentFinancialFact,
    Job,
    LiveMetric,
    RegulatoryRun,
    User,
)
from app.models.bi import MART_BUILD_SCOPES
from app.schemas.live import OfficialRunRequest
from app.services import authorization, job_queue, live_view, pipeline, regulatory_fx, scheduler
from app.services.bi import enqueue as bi_enqueue
from app.services.bi.versions import BUILDER_VERSION
from tests.api.helpers import ORG_1, ORG_2, USER_1
from tests.factories.canonical import FIXTURE_AS_OF
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def _grant_official_run(
    session: Session,
    user_id: UUID,
    sensitivity: SensitivityScope = SensitivityScope.CONFIDENTIAL,
) -> None:
    for module in (ModuleScope.FX, ModuleScope.FTP):
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                module,
                sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "scheduler-test"),
            reason="Authorize scheduled module execution",
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
    _grant_official_run(db_session, USER_1)
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
    _grant_official_run(db_session, analyst.id)

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
    with pytest.raises(HTTPException) as denied:
        pipeline.run_official(db_session, requested)
    assert denied.value.status_code == 403
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
        _grant_official_run(db_session, USER_1, SensitivityScope.AGGREGATED)
    elif authority == "inactive":
        _grant_official_run(db_session, USER_1)
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
    _grant_official_run(db_session, USER_1)
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
    _grant_official_run(db_session, USER_1)
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


# ---------------------------------------------------------------------------
# BI: recovery sweep + daily retention, both behind BI_SCHEDULER_ENABLED
# ---------------------------------------------------------------------------


def _enable_bi_scheduling(monkeypatch: pytest.MonkeyPatch, *, enqueue: bool = True) -> None:
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1" if enqueue else "0")
    get_settings.cache_clear()


def _seed_live_generation(db_session: Session, *, as_of: date = FIXTURE_AS_OF) -> None:
    """The live plane's input generation — the date every BI trigger keys on."""
    materialize_canonical_test_book(db_session)
    db_session.add(
        CurrentFinancialFact(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_as_of_date=as_of,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            amount=Decimal("1"),
            currency="GHS",
        )
    )
    db_session.commit()


def _record_build(
    db_session: Session, scope: str, *, status: str = "succeeded", as_of: date = FIXTURE_AS_OF
) -> None:
    started = utc_now()
    db_session.add(
        BiMartBuild(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            as_of_date=as_of,
            scope=scope,
            fingerprint="f" * 64,
            status=status,
            builder_version=1,
            started_at=started,
            finished_at=None if status == "running" else started,
            row_counts={},
        )
    )
    db_session.commit()


def _claimed_tick(db_session: Session) -> Job:
    job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None
    return tick


def test_scheduler_tick_carries_the_bi_flag(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BI_SCHEDULER_ENABLED alone must keep the tick chain alive (the
    any_scheduling_enabled ships-together invariant, D-008) and run both BI
    members: the recovery sweep and the daily retention pass."""
    _enable_bi_scheduling(monkeypatch)
    assert scheduler.any_scheduling_enabled(get_settings())
    _seed_live_generation(db_session)
    tick = _claimed_tick(db_session)

    scheduler.run_tick(db_session, tick)

    (rebuild,) = list(db_session.scalars(select(Job).where(Job.job_type == "bi_mart_refresh")))
    assert rebuild.bank_id == SAMPLE_BANK_ID
    assert rebuild.coalesce_key == f"bi:{SAMPLE_BANK_ID}:{FIXTURE_AS_OF.isoformat()}"
    assert rebuild.payload["as_of_date"] == FIXTURE_AS_OF.isoformat()
    assert rebuild.payload["reason"] == "scheduled mart recovery"
    assert rebuild.payload["builder_version"] == BUILDER_VERSION
    (retention,) = list(db_session.scalars(select(Job).where(Job.job_type == "bi_retention")))
    assert retention.payload == {"builder_version": BUILDER_VERSION}
    assert retention.coalesce_key == f"bi-retention:{utc_now().date().isoformat()}"
    assert retention.bank_id is None
    assert tick.progress["bi_rebuilds_enqueued"] == 1
    assert tick.progress["bi_retention_enqueued"] is True
    assert _count(db_session, "scheduled_tick", status="queued") == 1
    get_settings.cache_clear()


def test_bi_members_are_absent_from_the_tick_when_the_flag_is_off(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another flag keeps the chain alive; the BI sweep still does not run."""
    monkeypatch.setenv("LIVE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    _seed_live_generation(db_session)
    tick = _claimed_tick(db_session)

    scheduler.run_tick(db_session, tick)

    assert _count(db_session, "bi_mart_refresh") == 0
    assert _count(db_session, "bi_retention") == 0
    assert tick.progress["bi_rebuilds_enqueued"] == 0
    assert tick.progress["bi_retention_enqueued"] is False
    get_settings.cache_clear()


def test_bi_rebuild_sweep_skips_a_live_date_built_for_every_scope(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete build is left alone. ``succeeded`` is the ONLY status that
    counts as built (A5-05): nothing writes ``skipped`` to a build row."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope)

    enqueued = scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())

    assert enqueued == []
    assert _count(db_session, "bi_mart_refresh") == 0
    get_settings.cache_clear()


def test_an_unchanged_fingerprint_leaves_succeeded_rows_and_terminates_the_sweep(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REAL termination rule after a fingerprint skip (A5-05).

    ``refresh_bank_as_of`` returns before it touches ``bi_mart_builds`` when
    the fingerprint has not moved, so the rows of the build that did the work
    stay ``succeeded`` — and that is what makes the sweep terminate. The word
    ``skipped`` is a job ``progress`` fact only; no writer ever stores it as a
    build status, so it must not appear in the sweep's predicate.

    The live assertion here is the predicate itself: widen it to admit
    ``failed`` and this test fails. The ``skipped`` probe at the end can no
    longer bite on the predicate — since D-044 the CHECK constraint makes such
    a row unstorable, so the question is moot by construction — and it is kept
    as the proof of exactly that."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope)

    # A build row carrying the dead status is NOT built: were "skipped" still
    # in the predicate this slice would read as complete on a status no code
    # path can produce.
    statuses = set(
        db_session.scalars(select(BiMartBuild.status).where(BiMartBuild.bank_id == SAMPLE_BANK_ID))
    )
    assert statuses == {"succeeded"}
    assert scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now()) == []

    # A legal status other than "succeeded" leaves the slice unbuilt, so the
    # sweep offers it again.
    db_session.execute(
        update(BiMartBuild)
        .where(BiMartBuild.bank_id == SAMPLE_BANK_ID, BiMartBuild.scope == MART_BUILD_SCOPES[0])
        .values(status="failed")
    )
    db_session.commit()
    due = scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())
    assert len(due) == 1, "only 'succeeded' may satisfy the sweep"

    # "skipped" is not merely absent from the predicate — since D-044 the CHECK
    # constraint refuses the value outright, which is the stronger guarantee:
    # no writer can produce a status the sweep would misread. Probed last, so
    # the aborted statement cannot disturb the assertions above.
    with pytest.raises(IntegrityError):
        db_session.execute(
            update(BiMartBuild)
            .where(
                BiMartBuild.bank_id == SAMPLE_BANK_ID,
                BiMartBuild.scope == MART_BUILD_SCOPES[0],
            )
            .values(status="skipped")
        )
    db_session.rollback()
    get_settings.cache_clear()


@pytest.mark.parametrize("status", ["failed", "running"])
def test_bi_rebuild_sweep_recovers_an_incomplete_scope(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES[:-1]:
        _record_build(db_session, scope)
    _record_build(db_session, MART_BUILD_SCOPES[-1], status=status)

    enqueued = scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())

    assert len(enqueued) == 1
    assert enqueued[0].payload["as_of_date"] == FIXTURE_AS_OF.isoformat()
    get_settings.cache_clear()


def test_bi_rebuild_sweep_reads_the_live_date_not_an_older_build(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full build for LAST month's date does not satisfy this month's."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope, as_of=date(2026, 5, 31))

    enqueued = scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())

    assert len(enqueued) == 1
    assert enqueued[0].coalesce_key == f"bi:{SAMPLE_BANK_ID}:{FIXTURE_AS_OF.isoformat()}"
    get_settings.cache_clear()


def test_bi_rebuild_sweep_ignores_a_bank_with_no_live_generation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_bi_scheduling(monkeypatch)
    materialize_canonical_test_book(db_session)
    db_session.commit()

    assert scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now()) == []
    get_settings.cache_clear()


def test_bi_rebuild_sweep_is_inert_without_the_enqueue_switch(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep goes through the one seam: with BI_MART_ENQUEUE_ENABLED off
    it can never queue a job the handler would only skip. Retention is a
    scheduler concern and still runs."""
    _enable_bi_scheduling(monkeypatch, enqueue=False)
    _seed_live_generation(db_session)
    tick = _claimed_tick(db_session)

    scheduler.run_tick(db_session, tick)

    assert _count(db_session, "bi_mart_refresh") == 0
    assert tick.progress["bi_rebuilds_enqueued"] == 0
    assert _count(db_session, "bi_retention") == 1
    get_settings.cache_clear()


def test_bi_retention_is_once_per_day_platform_wide(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whichever org's tick first crosses the day carries the job; a later
    tick from ANY org finds the day's key taken, and a completed job keeps it
    (existence check, not enqueue coalescing). The next day gets a new one."""
    _enable_bi_scheduling(monkeypatch)
    today = utc_now()
    first = scheduler._enqueue_bi_retention(db_session, ORG_1, today)
    assert first is not None
    db_session.commit()
    assert scheduler._enqueue_bi_retention(db_session, ORG_1, today) is None
    assert scheduler._enqueue_bi_retention(db_session, ORG_2, today) is None
    job_queue.complete(db_session, first)
    assert scheduler._enqueue_bi_retention(db_session, ORG_2, today) is None
    assert _count(db_session, "bi_retention") == 1

    tomorrow = today + timedelta(days=1)
    second = scheduler._enqueue_bi_retention(db_session, ORG_2, tomorrow)
    assert second is not None
    assert second.coalesce_key == f"bi-retention:{tomorrow.date().isoformat()}"
    assert _count(db_session, "bi_retention") == 2
    get_settings.cache_clear()


# -- A5-06: the sweep is bounded, so a broken bank stops churning the bi lane ----


def _terminal_refresh_jobs(db_session: Session, count: int, *, status: str = "failed") -> None:
    """``count`` finished ``bi_mart_refresh`` jobs on the live slice's key.

    The builder reuses one ``bi_mart_builds`` row per (bank, as-of, scope) and
    resets it on every attempt, so the queue — not the build table — is where
    the attempt history survives. Rows are aged apart so ``queued_at DESC`` is
    deterministic on both SQLite and Postgres.
    """
    key = bi_enqueue.coalesce_key_for(SAMPLE_BANK_ID, FIXTURE_AS_OF)
    base = utc_now() - timedelta(hours=count + 1)
    for index in range(count):
        db_session.add(
            Job(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                job_type="bi_mart_refresh",
                status=status,
                coalesce_key=key,
                payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
                attempts=3,
                max_attempts=3,
                queued_at=base + timedelta(hours=index),
                completed_at=base + timedelta(hours=index),
            )
        )
    db_session.commit()


def test_one_failed_build_is_still_re_enqueued_by_the_sweep(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single failure is what the safety net exists for."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope, status="failed")
    _terminal_refresh_jobs(db_session, 1)

    enqueued = scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())

    assert len(enqueued) == 1
    assert enqueued[0].payload["reason"] == "scheduled mart recovery"
    get_settings.cache_clear()


def test_a_deterministically_failing_slice_stops_being_re_enqueued(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5-06: at MAX_RECOVERY_FAILURES terminal failures the sweep gives up,
    so a broken bank no longer burns a max_attempts chain every hour. Recovery
    is the operator backfill, which uses its own key and is unaffected."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope, status="failed")
    _terminal_refresh_jobs(db_session, bi_enqueue.MAX_RECOVERY_FAILURES)

    assert bi_enqueue.banks_due_for_rebuild(db_session, organization_id=ORG_1) == []
    assert scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now()) == []
    # The bound holds on every later tick, not just this one.
    assert scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now()) == []
    assert _count(db_session, "bi_mart_refresh") == bi_enqueue.MAX_RECOVERY_FAILURES
    get_settings.cache_clear()


def test_one_short_of_the_bound_is_still_re_enqueued(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope, status="failed")
    _terminal_refresh_jobs(db_session, bi_enqueue.MAX_RECOVERY_FAILURES - 1)

    assert len(scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())) == 1
    get_settings.cache_clear()


def test_a_later_success_resets_the_failure_count(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consecutive means consecutive: a succeeded job among the newest rows
    makes the slice eligible again, so a bank that failed three times last
    month is not locked out for ever."""
    _enable_bi_scheduling(monkeypatch)
    _seed_live_generation(db_session)
    for scope in MART_BUILD_SCOPES:
        _record_build(db_session, scope, status="failed")
    _terminal_refresh_jobs(db_session, bi_enqueue.MAX_RECOVERY_FAILURES)
    assert scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now()) == []

    # One newer succeeded job on the same key (an authoritative hook's build
    # that worked), then the slice broke again.
    db_session.add(
        Job(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            job_type="bi_mart_refresh",
            status="succeeded",
            coalesce_key=bi_enqueue.coalesce_key_for(SAMPLE_BANK_ID, FIXTURE_AS_OF),
            payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
            queued_at=utc_now(),
            completed_at=utc_now(),
        )
    )
    db_session.commit()

    assert len(scheduler._enqueue_due_bi_rebuilds(db_session, ORG_1, utc_now())) == 1
    get_settings.cache_clear()


def test_the_recovery_bound_mirrors_the_queues_own_max_attempts() -> None:
    """The number is not free-floating: three terminal failures is the queue's
    own "stop treating this as transient" contract applied one level up."""
    assert bi_enqueue.MAX_RECOVERY_FAILURES == 3
