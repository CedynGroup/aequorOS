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
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    AuditEvent,
    Bank,
    BankReportingPeriod,
    DatabaseDirectConnection,
    DeskDetermination,
    DeskOperatingEnvironmentAssessment,
    IngestionBatch,
    Job,
    MarketDataConnection,
    OperatorAuditLog,
    Organization,
    RegulatoryPackage,
    RegulatoryRun,
    SsoConnection,
    TemenosConnection,
    TenantStorage,
    User,
)
from app.schemas.market_desk import DeskEntitlementRead
from app.schemas.operator import (
    ActivityItemRead,
    DataEngineConnectionRead,
    DataEnginesRead,
    FleetOverviewRead,
    NeedsAttentionItemRead,
    OperatorAuditLogListRead,
    OperatorAuditLogRead,
    OverviewConnectionsRead,
    OverviewDeskRead,
    OverviewIngestionRead,
    OverviewJobsRead,
    OverviewTenantsRead,
    OperatorJobRead,
    OperatorJobsRead,
    TenantActivityRead,
    TenantEntitlementsRead,
    TenantFreshnessSummaryRead,
    TenantIngestionSummaryRead,
    TenantRead,
    TenantsListRead,
    TenantStorageRead,
    TenantUserRead,
    TenantUsersListRead,
)
from app.services import freshness as freshness_service
from app.services.market_desk import entitlements as entitlements_service

# Connection status → fleet health bucket (all three connection tables share the
# same status vocabulary, app/domain/ingestion/constants.py). Terminal/off states
# (DISABLED, REVOKED, REPLACED_PENDING_DELETION) are intentional and not a health
# problem, so they fall through to no bucket.
_CONNECTION_OK: frozenset[str] = frozenset({"ACTIVE"})
_CONNECTION_WARN: frozenset[str] = frozenset({"TESTING", "EXPIRING_SOON"})
_CONNECTION_CRIT: frozenset[str] = frozenset({"EXPIRED", "INVALID"})

logger = logging.getLogger(__name__)


# -- tenants -----------------------------------------------------------------
def list_tenants(db: Session) -> TenantsListRead:
    organizations = list(db.scalars(select(Organization).order_by(Organization.created_at)))
    tenants: list[TenantRead] = []
    for organization in organizations:
        tenants.extend(_tenant_rows_for_org(db, organization))
    return TenantsListRead(tenants=tenants)


def _tenant_rows_for_org(db: Session, organization: Organization) -> list[TenantRead]:
    """One TenantRead per bank (or a single bank-less row) for ONE org.

    Every query is explicitly scoped to ``organization.id`` — the cross-tenant
    read discipline the whole operator security model rests on."""
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
        return [_tenant_row(organization, None, None, None, None, None, sso_row, storage_row)]
    rows: list[TenantRead] = []
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
        rows.append(
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
    return rows


def _require_organization(db: Session, organization_id: str) -> Organization:
    organization = db.scalar(select(Organization).where(Organization.id == organization_id))
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )
    return organization


def get_tenant(db: Session, organization_id: str) -> TenantRead:
    """One tenant's health row (the same shape ``list_tenants`` returns), so the
    console can refresh a single tenant without refetching the whole board. An
    org with multiple banks returns its primary (earliest-created) bank row."""
    organization = _require_organization(db, organization_id)
    return _tenant_rows_for_org(db, organization)[0]


def list_tenant_users(db: Session, organization_id: str) -> TenantUsersListRead:
    """The tenant's own directory, scoped strictly to this org."""
    _require_organization(db, organization_id)
    rows = list(
        db.scalars(
            select(User)
            .where(User.organization_id == organization_id)
            .order_by(User.created_at)
        )
    )
    return TenantUsersListRead(
        users=[
            TenantUserRead(
                email=row.email,
                full_name=row.display_name,
                role=row.role,
                auth_provider=row.auth_provider,
                is_active=row.is_active,
                last_login_at=row.last_login_at,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


def get_tenant_entitlements(db: Session, organization_id: str) -> TenantEntitlementsRead:
    """The desk entitlement query filtered to one org, plus the dataset/tier
    catalog (so the console can render the grant surface without a second call)."""
    _require_organization(db, organization_id)
    rows = entitlements_service.list_entitlements(db, organization_id=organization_id)
    return TenantEntitlementsRead(
        entitlements=[
            DeskEntitlementRead(
                id=row.id,
                organization_id=row.organization_id,
                dataset_code=row.dataset_code,
                tier=row.tier,
                status=row.status,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                granted_by=row.granted_by,
                revoked_by=row.revoked_by,
                revoked_at=row.revoked_at,
                notes=row.notes,
                created_at=row.created_at,
            )
            for row in rows
        ],
        catalog=entitlements_service.catalog(),
    )


def get_tenant_storage(db: Session, organization_id: str) -> TenantStorageRead:
    """Best-effort object-storage view. Never fails on the documented MinIO
    quirks (Cloudflare WAF blocks HEAD; no KES): when a metric cannot be read it
    stays ``None`` and ``note`` explains why."""
    _require_organization(db, organization_id)
    row = db.scalar(
        select(TenantStorage).where(TenantStorage.organization_id == organization_id)
    )
    if row is None:
        return TenantStorageRead(
            note="No storage registry row for this tenant (provisioning saga writes it)."
        )
    bucket = row.bucket_names[0] if row.bucket_names else None
    if row.provider == "minio":
        note = (
            "Object metrics unavailable: the MinIO deployment sits behind a "
            "Cloudflare WAF that blocks HEAD, and exposes no KMS encryption state."
        )
        kms_key_state = None
    else:
        note = None
        kms_key_state = "configured" if row.kms_key_arn else "none"
    return TenantStorageRead(
        provider=row.provider,
        bucket=bucket,
        object_count=None,
        bytes=None,
        kms_key_state=kms_key_state,
        note=note,
    )


# -- operator audit log (the operator's OWN cross-tenant action log) -------------
def _audit_log_read(row: OperatorAuditLog) -> OperatorAuditLogRead:
    return OperatorAuditLogRead(
        id=str(row.id),
        operator_email=row.operator_email,
        auth_mode=row.auth_mode,  # type: ignore[arg-type]  # DB-constrained to the literal set
        action=row.action,
        target_org=row.target_org,
        detail=row.detail,
        created_at=row.created_at,
    )


def read_operator_audit_log(  # noqa: PLR0913 - one filter per query parameter
    db: Session,
    *,
    operator_email: str | None = None,
    target_org: str | None = None,
    action: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> OperatorAuditLogListRead:
    filters = []
    if operator_email is not None:
        filters.append(OperatorAuditLog.operator_email == operator_email)
    if target_org is not None:
        filters.append(OperatorAuditLog.target_org == target_org)
    if action is not None:
        # Prefix match: "desk." matches "desk.determination.publish".
        filters.append(OperatorAuditLog.action.like(f"{action}%"))
    if from_ts is not None:
        filters.append(OperatorAuditLog.created_at >= from_ts)
    if to_ts is not None:
        filters.append(OperatorAuditLog.created_at <= to_ts)
    total = db.scalar(select(func.count()).select_from(OperatorAuditLog).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(OperatorAuditLog)
            .where(*filters)
            .order_by(OperatorAuditLog.created_at.desc(), OperatorAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return OperatorAuditLogListRead(items=[_audit_log_read(row) for row in rows], total=total)


# -- fleet overview --------------------------------------------------------------
def fleet_overview(db: Session) -> FleetOverviewRead:
    """Cross-tenant rollup for the console home. Tenant + freshness + connection
    facts reuse the tested org-scoped read views; ingestion/job/desk counts are
    deliberate fleet aggregates (single COUNT queries, not N+1)."""
    tenant_rows = list_tenants(db).tenants
    total_orgs = db.scalar(select(func.count()).select_from(Organization)) or 0
    banks_live = sum(1 for t in tenant_rows if t.bank_id is not None and t.period_count > 0)
    banks_empty = sum(1 for t in tenant_rows if t.bank_id is not None and t.period_count == 0)
    stale_count = sum(
        1 for t in tenant_rows if t.freshness is not None and t.freshness.is_stale
    )
    tenants = OverviewTenantsRead(
        total=total_orgs,
        banks_live=banks_live,
        banks_empty=banks_empty,
        stale_count=stale_count,
    )

    cutoff = utc_now() - timedelta(hours=24)
    ingestion = OverviewIngestionRead(
        failed_24h=db.scalar(
            select(func.count())
            .select_from(IngestionBatch)
            .where(
                IngestionBatch.status == "failed",
                func.coalesce(IngestionBatch.completed_at, IngestionBatch.created_at) >= cutoff,
            )
        )
        or 0
    )
    jobs = OverviewJobsRead(
        failed_24h=db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == "failed",
                func.coalesce(Job.completed_at, Job.queued_at) >= cutoff,
            )
        )
        or 0,
        running=db.scalar(
            select(func.count()).select_from(Job).where(Job.status == "running")
        )
        or 0,
    )

    connection_rows = list_data_engine_connections(db).connections
    conn_ok = sum(1 for c in connection_rows if c.status in _CONNECTION_OK)
    conn_warn = sum(1 for c in connection_rows if c.status in _CONNECTION_WARN)
    conn_crit = sum(1 for c in connection_rows if c.status in _CONNECTION_CRIT)
    connections = OverviewConnectionsRead(ok=conn_ok, warn=conn_warn, crit=conn_crit)

    pending = list(
        db.scalars(
            select(DeskDetermination).where(DeskDetermination.status == "pending_review")
        )
    )
    # Curve determinations carry no ``rates`` block in derived_values (only
    # ``curves``/``forward_grids``); rates determinations always do (they run the
    # §5 rates pipeline). That is the split the two governance queues use.
    pending_curves = sum(1 for d in pending if "rates" not in (d.derived_values or {}))
    pending_rates = len(pending) - pending_curves
    pending_oe = (
        db.scalar(
            select(func.count())
            .select_from(DeskOperatingEnvironmentAssessment)
            .where(DeskOperatingEnvironmentAssessment.status == "pending_review")
        )
        or 0
    )
    desk = OverviewDeskRead(
        pending_determinations=pending_rates,
        pending_curve_determinations=pending_curves,
        pending_oe_assessments=pending_oe,
    )

    return FleetOverviewRead(
        tenants=tenants,
        ingestion=ingestion,
        jobs=jobs,
        connections=connections,
        desk=desk,
        needs_attention=_needs_attention(
            tenants=tenants,
            ingestion=ingestion,
            jobs=jobs,
            connections=connections,
            desk=desk,
        ),
    )


def _needs_attention(
    *,
    tenants: OverviewTenantsRead,
    ingestion: OverviewIngestionRead,
    jobs: OverviewJobsRead,
    connections: OverviewConnectionsRead,
    desk: OverviewDeskRead,
) -> list[NeedsAttentionItemRead]:
    """Bounded, rollup-level attention list (crit first, then warn) — counts
    only, never a per-tenant enumeration that would leak the board here."""
    crit: list[NeedsAttentionItemRead] = []
    warn: list[NeedsAttentionItemRead] = []
    if connections.crit:
        crit.append(
            NeedsAttentionItemRead(
                kind="connections",
                summary=f"{connections.crit} data connection(s) expired or invalid",
                severity="crit",
                href="/data-engines",
            )
        )
    if jobs.failed_24h:
        crit.append(
            NeedsAttentionItemRead(
                kind="jobs",
                summary=f"{jobs.failed_24h} job(s) failed in the last 24h",
                severity="crit",
            )
        )
    if ingestion.failed_24h:
        crit.append(
            NeedsAttentionItemRead(
                kind="ingestion",
                summary=f"{ingestion.failed_24h} ingestion batch(es) failed in the last 24h",
                severity="crit",
            )
        )
    if tenants.stale_count:
        warn.append(
            NeedsAttentionItemRead(
                kind="freshness",
                summary=f"{tenants.stale_count} tenant(s) with stale modules",
                severity="warn",
                href="/tenants",
            )
        )
    if connections.warn:
        warn.append(
            NeedsAttentionItemRead(
                kind="connections",
                summary=f"{connections.warn} data connection(s) testing or expiring soon",
                severity="warn",
                href="/data-engines",
            )
        )
    pending_desk = (
        desk.pending_determinations
        + desk.pending_curve_determinations
        + desk.pending_oe_assessments
    )
    if pending_desk:
        warn.append(
            NeedsAttentionItemRead(
                kind="desk",
                summary=f"{pending_desk} desk determination(s)/assessment(s) awaiting review",
                severity="warn",
                href="/desk",
            )
        )
    return crit + warn


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
        institution_type=bank.institution_type if bank else None,
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


# -- jobs ------------------------------------------------------------------------
def list_jobs(
    db: Session, *, limit: int, status_filter: str | None = None
) -> OperatorJobsRead:
    """Newest-first cross-tenant job board with durable claimant identity."""
    statement = select(Job).order_by(Job.queued_at.desc()).limit(limit)
    if status_filter is not None:
        statement = statement.where(Job.status == status_filter)
    rows = list(db.scalars(statement))
    return OperatorJobsRead(
        jobs=[
            OperatorJobRead(
                id=str(job.id),
                organization_id=job.organization_id,
                bank_id=job.bank_id,
                job_type=job.job_type,
                status=job.status,
                claimed_by=job.claimed_by,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                queued_at=job.queued_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                run_after=job.run_after,
                error=job.error,
            )
            for job in rows
        ]
    )


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
