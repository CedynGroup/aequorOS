"""Self-perpetuating scheduler for immutable official runs.

A ``scheduled_tick`` job runs per organization: when official runs are enabled it
enqueues an ``official_run`` for every bank whose latest period has no official
run since today's cutoff hour, then enqueues the next tick at the following hour
boundary. It is inert (no enqueue, no reschedule) while ``OFFICIAL_RUN_ENABLED``
is off, so no environment auto-mints heavy runs and tests stay deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Bank, BankReportingPeriod, Job, RegulatoryRun, User
from app.services import job_queue

SCHEDULED_TICK = "scheduled_tick"
OFFICIAL_RUN = "official_run"
_LIQUIDITY_MODULE = "liquidity"
_BASELINE_SCENARIO = "baseline"


def run_tick(session: Session, job: Job) -> None:
    """Worker handler: enqueue due official runs for the org, then reschedule."""
    settings = get_settings()
    if not settings.worker.official_run_enabled:
        job.progress = {"status": "inert", "reason": "official_run_disabled"}
        return

    org_id = job.organization_id
    now = utc_now()
    actor_id = session.scalar(
        select(User.id)
        .where(User.organization_id == org_id, User.is_active.is_(True))
        .order_by(User.created_at)
        .limit(1)
    )

    enqueued: list[str] = []
    banks = list(session.scalars(select(Bank).where(Bank.organization_id == org_id)))
    for bank in banks:
        period = _latest_period(session, org_id, bank.id)
        if period is None:
            continue
        if _official_run_done_today(
            session, org_id, bank.id, period.id, settings.worker.official_run_hour, now
        ):
            continue
        payload: dict[str, str] = {"as_of_date": period.period_end.isoformat()}
        if actor_id is not None:
            payload["actor_user_id"] = str(actor_id)
        job_queue.enqueue(
            session,
            org_id,
            OFFICIAL_RUN,
            bank_id=bank.id,
            payload=payload,
            coalesce_key=f"official:{bank.id}:{now.date().isoformat()}",
        )
        enqueued.append(str(bank.id))

    job_queue.enqueue(
        session,
        org_id,
        SCHEDULED_TICK,
        run_after=_next_hour_boundary(now),
        coalesce_key=f"tick:{org_id}",
    )
    session.commit()
    job.progress = {"official_runs_enqueued": enqueued}


def seed_tick(db: Session, organization_id: UUID) -> Job:
    """Seed the first (or a replacement) tick for one org; coalesces to one."""
    return job_queue.enqueue(
        db,
        organization_id,
        SCHEDULED_TICK,
        run_after=utc_now(),
        coalesce_key=f"tick:{organization_id}",
    )


def seed_ticks(db: Session) -> int:
    """Seed a tick for every org that owns a bank. Returns the count seeded.

    Reads across organizations, so on Postgres the worker connection must be
    able to see all tenants (a BYPASSRLS worker role); on SQLite/dev it is a
    plain scan. Idempotent via the per-org coalesce key.
    """
    org_ids = list(db.scalars(select(Bank.organization_id).distinct()))
    for org_id in org_ids:
        seed_tick(db, org_id)
    db.commit()
    return len(org_ids)


def _latest_period(session: Session, org_id: UUID, bank_id: UUID) -> BankReportingPeriod | None:
    return session.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == org_id,
            BankReportingPeriod.bank_id == bank_id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


def _official_run_done_today(  # noqa: PLR0913 - the "due" predicate needs the full key
    session: Session,
    org_id: UUID,
    bank_id: UUID,
    period_id: UUID,
    official_run_hour: int,
    now: datetime,
) -> bool:
    boundary = now.replace(hour=official_run_hour, minute=0, second=0, microsecond=0)
    run_id = session.scalar(
        select(RegulatoryRun.id)
        .where(
            RegulatoryRun.organization_id == org_id,
            RegulatoryRun.bank_id == bank_id,
            RegulatoryRun.reporting_period_id == period_id,
            RegulatoryRun.module == _LIQUIDITY_MODULE,
            RegulatoryRun.scenario_code == _BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
            RegulatoryRun.created_at >= boundary,
        )
        .limit(1)
    )
    return run_id is not None


def _next_hour_boundary(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
