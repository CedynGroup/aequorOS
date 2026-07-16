"""The live cross-module view plus the on-demand refresh / official-run buttons.

``get_live_summary`` is a single cheap read of the upserted ``live_metrics``
rows for a bank's latest period, tagged with a freshness ``is_stale`` flag.
``refresh_bank_data`` and ``mint_official_run`` enqueue the corresponding jobs
immediately and return the job id to poll via ``GET /jobs/{id}``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import Bank, BankReportingPeriod, LiveMetric
from app.schemas.live import (
    JobEnqueuedRead,
    LiveModuleView,
    LiveSummaryRead,
    OfficialRunRequest,
    RefreshRequest,
)
from app.services import freshness, job_queue
from app.services.audit import record_event

_MODULE_ORDER = {
    module: index
    for index, module in enumerate(("liquidity", "capital", "irr", "fx", "ftp", "forecast"))
}


def get_live_summary(db: Session, ctx: TenantContext, bank_id: UUID) -> LiveSummaryRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _latest_period(db, ctx, bank)
    if period is None:
        return LiveSummaryRead(
            bank_id=bank.id,
            reporting_period_id=None,
            period_label=None,
            modules=[],
            is_stale=False,
            computed_at=None,
        )

    rows = list(
        db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
                LiveMetric.reporting_period_id == period.id,
            )
        )
    )
    rows.sort(key=lambda row: _MODULE_ORDER.get(row.module, 99))
    modules = [
        LiveModuleView(
            module=row.module,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            metrics=row.metrics,
            computed_at=row.computed_at,
            computed_from_input_hash=row.computed_from_input_hash,
        )
        for row in rows
    ]
    computed_at = max((row.computed_at for row in rows), default=None)
    is_stale = freshness.get_bank_freshness(db, ctx, bank.id, period.id).is_stale
    return LiveSummaryRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        period_label=period.label,
        modules=modules,
        is_stale=is_stale,
        computed_at=computed_at,
    )


def refresh_bank_data(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RefreshRequest
) -> JobEnqueuedRead:
    """Enqueue an immediate live refresh (the "Recompute now" button)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    job = job_queue.enqueue(
        db,
        ctx.organization_id,
        "pipeline_refresh",
        bank_id=bank.id,
        payload=_job_payload(ctx, payload.as_of_date.isoformat(), payload.reason),
        run_after=utc_now(),
        coalesce_key=f"refresh:{bank.id}:{payload.as_of_date.isoformat()}",
    )
    record_event(
        db,
        ctx,
        event_type="bank_data.refresh_requested",
        entity_type="bank",
        entity_id=bank.id,
        details={"as_of_date": payload.as_of_date.isoformat(), "reason": payload.reason},
    )
    db.commit()
    return JobEnqueuedRead(job_id=job.id, job_type=job.job_type, status=job.status)


def mint_official_run(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: OfficialRunRequest
) -> JobEnqueuedRead:
    """Enqueue an immediate immutable official run (the "Mint for filing" button)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    job = job_queue.enqueue(
        db,
        ctx.organization_id,
        "official_run",
        bank_id=bank.id,
        payload=_job_payload(ctx, payload.as_of_date.isoformat(), payload.reason),
        run_after=utc_now(),
        coalesce_key=f"official:{bank.id}:{payload.as_of_date.isoformat()}",
    )
    record_event(
        db,
        ctx,
        event_type="official_run.requested",
        entity_type="bank",
        entity_id=bank.id,
        details={"as_of_date": payload.as_of_date.isoformat(), "reason": payload.reason},
    )
    db.commit()
    return JobEnqueuedRead(job_id=job.id, job_type=job.job_type, status=job.status)


def _job_payload(ctx: TenantContext, as_of_date: str, reason: str) -> dict[str, str]:
    payload: dict[str, str] = {"as_of_date": as_of_date, "reason": reason}
    if ctx.actor_user_id is not None:
        payload["actor_user_id"] = str(ctx.actor_user_id)
    return payload


def _latest_period(db: Session, ctx: TenantContext, bank: Bank) -> BankReportingPeriod | None:
    return db.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
