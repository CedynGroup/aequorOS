"""Cross-tenant operator read views (docs/internal/developer.md §3).

Every query in this module runs on the operator's BYPASSRLS session, so
EVERY query carries an explicit ``organization_id`` scope (or iterates
organizations explicitly). That discipline is the whole security model of
cross-tenant reads — never add a query here without it.

Nothing in this module returns credential material: connection views expose
lifecycle/status/expiry metadata only.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    AuditEvent,
    Bank,
    BankReportingPeriod,
    DatabaseDirectConnection,
    IngestionBatch,
    Job,
    MarketDataConnection,
    Organization,
    RegulatoryPackage,
    RegulatoryRun,
    SsoConnection,
    TemenosConnection,
    TenantStorage,
)
from app.schemas.operator import (
    ActivityItemRead,
    DataEngineConnectionRead,
    DataEnginesRead,
    TenantActivityRead,
    TenantFreshnessSummaryRead,
    TenantIngestionSummaryRead,
    TenantRead,
    TenantsListRead,
)
from app.services import freshness as freshness_service

logger = logging.getLogger(__name__)


# -- tenants -----------------------------------------------------------------
def list_tenants(db: Session) -> TenantsListRead:
    organizations = list(db.scalars(select(Organization).order_by(Organization.created_at)))
    tenants: list[TenantRead] = []
    for organization in organizations:
        banks = list(
            db.scalars(
                select(Bank)
                .where(Bank.organization_id == organization.id)
                .order_by(Bank.created_at)
            )
        )
        storage_row = db.scalar(
            select(TenantStorage).where(TenantStorage.organization_id == organization.id)
        )
        sso_row = db.scalar(
            select(SsoConnection).where(SsoConnection.organization_id == organization.id)
        )
        if not banks:
            tenants.append(
                _tenant_row(organization, None, None, None, None, None, sso_row, storage_row)
            )
            continue
        for bank in banks:
            period_count = db.scalar(
                select(func.count())
                .select_from(BankReportingPeriod)
                .where(
                    BankReportingPeriod.organization_id == organization.id,
                    BankReportingPeriod.bank_id == bank.id,
                )
            )
            latest_period_end = db.scalar(
                select(func.max(BankReportingPeriod.period_end)).where(
                    BankReportingPeriod.organization_id == organization.id,
                    BankReportingPeriod.bank_id == bank.id,
                )
            )
            freshness = _freshness_summary(db, organization.id, bank.id, period_count or 0)
            last_ingestion = _last_ingestion(db, organization.id, bank.id)
            tenants.append(
                _tenant_row(
                    organization,
                    bank,
                    period_count or 0,
                    latest_period_end,
                    freshness,
                    last_ingestion,
                    sso_row,
                    storage_row,
                )
            )
    return TenantsListRead(tenants=tenants)


def _tenant_row(  # noqa: PLR0913 - a row is the join of these seven sources
    organization: Organization,
    bank: Bank | None,
    period_count: int | None,
    latest_period_end,  # noqa: ANN001 - date | None from SQL max()
    freshness: TenantFreshnessSummaryRead | None,
    last_ingestion: TenantIngestionSummaryRead | None,
    sso_row: SsoConnection | None,
    storage_row: TenantStorage | None,
) -> TenantRead:
    return TenantRead(
        organization_id=organization.id,
        organization_name=organization.name,
        organization_created_at=organization.created_at,
        bank_id=bank.id if bank else None,
        bank_name=bank.name if bank else None,
        jurisdiction_code=bank.jurisdiction_code if bank else None,
        currency=bank.currency if bank else None,
        license_type=bank.license_type if bank else None,
        bank_created_at=bank.created_at if bank else None,
        period_count=period_count or 0,
        latest_period_end=latest_period_end,
        freshness=freshness,
        last_ingestion=last_ingestion,
        sso_configured=sso_row is not None,
        sso_enabled=bool(sso_row.enabled) if sso_row else False,
        storage_provider=storage_row.provider if storage_row else None,
    )


def _freshness_summary(
    db: Session, organization_id: str, bank_id: str, period_count: int
) -> TenantFreshnessSummaryRead | None:
    """Reuse the tenant freshness service — it is cross-tenant-callable
    because it scopes strictly by the ctx we hand it. A tenant with no
    periods has nothing to be fresh about; a per-tenant failure must never
    take down the whole board."""
    if period_count == 0:
        return None
    try:
        ctx = TenantContext(organization_id=organization_id)
        report = freshness_service.get_bank_freshness(db, ctx, bank_id)
    except Exception:  # noqa: BLE001 - one tenant's failure must not break the list
        logger.exception(
            "freshness summary failed for org %s bank %s", organization_id, bank_id
        )
        return None
    computed = [m.computed_at for m in report.modules if m.computed_at is not None]
    return TenantFreshnessSummaryRead(
        is_stale=report.is_stale,
        stale_modules=[m.module for m in report.modules if m.is_stale],
        modules_reported=len(report.modules),
        latest_computed_at=max(computed) if computed else None,
    )


def _last_ingestion(
    db: Session, organization_id: str, bank_id: str
) -> TenantIngestionSummaryRead | None:
    batch = db.scalar(
        select(IngestionBatch)
        .where(
            IngestionBatch.organization_id == organization_id,
            IngestionBatch.bank_id == bank_id,
        )
        .order_by(IngestionBatch.created_at.desc())
        .limit(1)
    )
    if batch is None:
        return None
    return TenantIngestionSummaryRead(
        batch_id=str(batch.id),
        status=batch.status,
        source_system=batch.source_system,
        as_of_date=batch.as_of_date,
        completed_at=batch.completed_at,
    )


# -- activity feed --------------------------------------------------------------
def get_tenant_activity(db: Session, organization_id: str, limit: int) -> TenantActivityRead:
    organization = db.scalar(
        select(Organization).where(Organization.id == organization_id)
    )
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )

    items: list[ActivityItemRead] = []

    for batch in db.scalars(
        select(IngestionBatch)
        .where(IngestionBatch.organization_id == organization_id)
        .order_by(IngestionBatch.created_at.desc())
        .limit(limit)
    ):
        items.append(
            ActivityItemRead(
                ts=batch.completed_at or batch.created_at,
                kind="ingestion_batch",
                summary=(
                    f"{batch.source_system} ingestion for {batch.bank_id} "
                    f"as of {batch.as_of_date.isoformat()} — "
                    f"{batch.records_accepted} accepted / {batch.records_error} errors"
                ),
                status=batch.status,
            )
        )

    for job in db.scalars(
        select(Job)
        .where(Job.organization_id == organization_id)
        .order_by(Job.queued_at.desc())
        .limit(limit)
    ):
        items.append(
            ActivityItemRead(
                ts=job.completed_at or job.started_at or job.queued_at,
                kind="job",
                summary=(
                    f"{job.job_type} job"
                    + (f" for {job.bank_id}" if job.bank_id else "")
                    + (f" — {job.error}" if job.error else "")
                ),
                status=job.status,
            )
        )

    for run in db.scalars(
        select(RegulatoryRun)
        .where(RegulatoryRun.organization_id == organization_id)
        .order_by(RegulatoryRun.created_at.desc())
        .limit(limit)
    ):
        items.append(
            ActivityItemRead(
                ts=run.completed_at or run.created_at,
                kind="official_run",
                summary=(
                    f"official {run.module} run ({run.scenario_code}) for {run.bank_id}"
                ),
                status=run.status,
            )
        )

    for package in db.scalars(
        select(RegulatoryPackage)
        .where(RegulatoryPackage.organization_id == organization_id)
        .order_by(RegulatoryPackage.generated_at.desc())
        .limit(limit)
    ):
        items.append(
            ActivityItemRead(
                ts=package.generated_at,
                kind="package",
                summary=(
                    f"{package.return_code} v{package.version} "
                    f"({package.reporting_date.isoformat()}, {package.basis}) "
                    f"for {package.bank_id}"
                ),
                status=package.status,
            )
        )

    for event in db.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    ):
        items.append(
            ActivityItemRead(
                ts=event.created_at,
                kind="audit_event",
                summary=f"{event.event_type}"
                + (f" ({event.entity_type} {event.entity_id})" if event.entity_type else ""),
                status="recorded",
            )
        )

    items.sort(key=_activity_sort_key, reverse=True)
    return TenantActivityRead(organization_id=organization_id, items=items[:limit])


def _activity_sort_key(item: ActivityItemRead) -> tuple[bool, datetime]:
    # SQLite returns naive datetimes, Postgres tz-aware; normalize the sort so
    # a mixed feed never raises on naive/aware comparison.
    ts = item.ts
    return (True, ts.replace(tzinfo=None) if ts.tzinfo is not None else ts)


# -- data engines ----------------------------------------------------------------
def list_data_engine_connections(db: Session) -> DataEnginesRead:
    connections: list[DataEngineConnectionRead] = []
    organization_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)))
    for organization_id in organization_ids:
        for row in db.scalars(
            select(MarketDataConnection)
            .where(MarketDataConnection.organization_id == organization_id)
            .order_by(MarketDataConnection.created_at)
        ):
            connections.append(
                DataEngineConnectionRead(
                    organization_id=row.organization_id,
                    bank_id=row.bank_id,
                    engine="market_data",
                    system=row.vendor,
                    display_name=row.display_name,
                    status=row.status,
                    last_activity_at=row.last_pull_at,
                    last_activity_status=row.last_pull_status,
                    credential_expires_at=row.credential_expires_at,
                    created_at=row.created_at,
                )
            )
        for row in db.scalars(
            select(DatabaseDirectConnection)
            .where(DatabaseDirectConnection.organization_id == organization_id)
            .order_by(DatabaseDirectConnection.created_at)
        ):
            connections.append(
                DataEngineConnectionRead(
                    organization_id=row.organization_id,
                    bank_id=row.bank_id,
                    engine="database_direct",
                    system=row.backend,
                    display_name=row.display_name,
                    status=row.status,
                    last_activity_at=row.last_synced_at,
                    last_activity_status=row.last_sync_status,
                    credential_expires_at=row.credential_expires_at,
                    created_at=row.created_at,
                )
            )
        for row in db.scalars(
            select(TemenosConnection)
            .where(TemenosConnection.organization_id == organization_id)
            .order_by(TemenosConnection.created_at)
        ):
            connections.append(
                DataEngineConnectionRead(
                    organization_id=row.organization_id,
                    bank_id=row.bank_id,
                    engine="t24",
                    system=row.core_system,
                    display_name=row.display_name,
                    status=row.status,
                    last_activity_at=row.last_pull_at,
                    last_activity_status=row.last_pull_status,
                    credential_expires_at=row.credential_expires_at,
                    created_at=row.created_at,
                )
            )
    return DataEnginesRead(connections=connections)
