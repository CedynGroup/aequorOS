"""The live cross-module view plus the on-demand refresh / official-run buttons.

``get_live_summary`` is a cheap read of the bank/module ``live_metrics`` rows.
The optional source fact period is provenance for the current canonical
materialisation, never an identity or a user-facing reporting prerequisite.
``refresh_bank_data`` and ``mint_official_run`` enqueue the corresponding jobs
immediately and return the job id to poll via ``GET /jobs/{id}``.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    Bank,
    BankReportingPeriod,
    CurrentFinancialFact,
    IngestionBatch,
    LiveMetric,
    LiveMetricSnapshot,
)
from app.schemas.live import (
    JobEnqueuedRead,
    LiveModuleView,
    LiveSnapshotListRead,
    LiveSnapshotRead,
    LiveSummaryRead,
    OfficialRunRequest,
    RefreshRequest,
)
from app.services import job_queue
from app.services.audit import record_event

_MODULE_ORDER = {
    module: index
    for index, module in enumerate(
        ("liquidity", "capital", "irr", "fx", "ftp", "rating", "forecast")
    )
}


def get_live_summary(db: Session, ctx: TenantContext, bank_id: str) -> LiveSummaryRead:
    """The always-live treasury cockpit.

    The live view is compute-from-latest: on read it recomputes any module
    whose inputs have changed (new data ingested, or a methodology/parameter
    version drift) via the shared cheap-tier ``pipeline.recompute_live``, then
    serves the fresh rows. It never depends on a background job having run, and
    it is never labelled "stale" — drift versus the last *official filing* is a
    governance concept (``freshness.get_bank_freshness``), not a liveness one,
    and lives on the reporting surface, not here.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _latest_period(db, ctx, bank)
    if period is not None:
        _ensure_live_current(db, ctx, bank, period)

    rows = list(
        db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
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
            source_as_of_date=row.source_as_of_date,
            source_fact_period_id=row.source_fact_period_id,
            engine_version=row.engine_version,
            calculation_generation=row.calculation_generation,
            pipeline_state=row.pipeline_state,  # type: ignore[arg-type]
            pipeline_error=row.pipeline_error,
        )
        for row in rows
    ]
    computed_at = max((row.computed_at for row in rows), default=None)
    return LiveSummaryRead(
        bank_id=bank.id,
        reporting_period_id=period.id if period is not None else None,
        period_label=period.label if period is not None else None,
        source_as_of_date=max((row.source_as_of_date for row in rows), default=None),
        modules=modules,
        is_stale=False,
        computed_at=computed_at,
    )


def _ensure_live_current(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> None:
    """Keep the live cache current WITHOUT blocking the read.

    A read never recomputes inline — the module engines are seconds each, far too
    heavy for a request. Instead a CHEAP check (pure timestamp comparison, no
    engine work) detects when data has been ingested since the cache was computed
    and enqueues a coalesced background refresh; the in-process worker recomputes
    and updates the cache within seconds, so the next poll reflects it. Fact
    re-derivation and the engines stay entirely off the request path, and no
    manual activation is ever required — a data change auto-refreshes.
    """
    cached = {
        row.module: row
        for row in db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
            )
        )
    }
    if not _refresh_needed(db, ctx, bank, cached):
        return
    job_queue.enqueue(
        db,
        ctx.organization_id,
        "pipeline_refresh",
        bank_id=bank.id,
        payload={"as_of_date": period.period_end.isoformat(), "reason": "live-read auto-refresh"},
        run_after=utc_now(),
        coalesce_key=f"refresh:{bank.id}:{period.period_end.isoformat()}",
    )
    db.commit()


def _refresh_needed(
    db: Session, ctx: TenantContext, bank: Bank, cached: dict[str, LiveMetric]
) -> bool:
    """Cheap staleness check: is the cache behind the latest ingested data?

    Compares the newest ingestion batch against the oldest cached module — a pure
    timestamp comparison, no engine work, so it is safe on every read.
    """
    has_current_facts = db.scalar(
        select(CurrentFinancialFact.id)
        .where(
            CurrentFinancialFact.organization_id == ctx.organization_id,
            CurrentFinancialFact.bank_id == bank.id,
        )
        .limit(1)
    )
    if has_current_facts is None or not cached:
        return True
    if any(
        row.pipeline_state == "failed"
        or row.metrics.get("availability") == "unavailable"
        for row in cached.values()
    ):
        return True
    oldest_compute = min((row.computed_at for row in cached.values()), default=None)
    latest_ingest = db.scalar(
        select(func.max(IngestionBatch.created_at)).where(
            IngestionBatch.organization_id == ctx.organization_id,
            IngestionBatch.bank_id == bank.id,
        )
    )
    return (
        latest_ingest is not None
        and oldest_compute is not None
        and latest_ingest > oldest_compute
    )


def refresh_bank_data(
    db: Session, ctx: TenantContext, bank_id: str, payload: RefreshRequest
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
    db: Session, ctx: TenantContext, bank_id: str, payload: OfficialRunRequest
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


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def list_live_snapshots(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    module: str,
    days: int = 45,
) -> LiveSnapshotListRead:
    """The plane-2 daily ladder, oldest first, capped at ``days`` rows.

    Past days are end-of-day closes; today's row (when present) is the live
    edge. The series only has rows for days a refresh actually ran — gaps
    are honest, not zero-filled.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows = list(
        db.scalars(
            select(LiveMetricSnapshot)
            .where(
                LiveMetricSnapshot.organization_id == ctx.organization_id,
                LiveMetricSnapshot.bank_id == bank.id,
                LiveMetricSnapshot.module == module,
            )
            .order_by(LiveMetricSnapshot.snapshot_date.desc())
            .limit(days)
        )
    )
    rows.reverse()
    return LiveSnapshotListRead(
        bank_id=bank.id,
        module=module,
        days=days,
        snapshots=[
            LiveSnapshotRead(
                snapshot_date=row.snapshot_date,
                module=row.module,
                metrics={
                    key: value
                    for key, value in row.metrics.items()
                    if isinstance(value, (str, int, float, bool))
                },
                status=row.status,
                computed_at=row.computed_at,
            )
            for row in rows
        ],
    )
