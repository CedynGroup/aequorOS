"""Session-gated deep tenant reads for the Tenant Inspector.

These views power the diagnostic surfaces the console shows INSIDE an active
inspector session (metrics, findings, ingestion, config). Like every operator
read they run on the BYPASSRLS session, so EVERY query is explicitly scoped by
``organization_id`` — the cross-tenant read discipline the operator model rests
on. The session gate itself lives in ``app.operator.inspection``; this module is
pure read assembly and returns NO secret material (SSO exposes only whether a
secret is set; integration keys expose only the display prefix + lifecycle).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    IngestionBatch,
    IntegrationKey,
    LiveFinding,
    LiveMetric,
    MappingConfigRecord,
    Organization,
    ParamCapitalThreshold,
    ParamLiquidityThreshold,
    RegulatoryRun,
    SsoConnection,
    TranslationFailure,
)
from app.schemas.operator import (
    TenantCapitalThresholdRead,
    TenantConfigBankRead,
    TenantConfigRead,
    TenantFindingRead,
    TenantFindingsRead,
    TenantIngestionBatchDetailRead,
    TenantIngestionBatchRead,
    TenantIngestionListRead,
    TenantIntegrationKeyRead,
    TenantLiquidityThresholdRead,
    TenantLiveMetricRead,
    TenantMappingConfigRead,
    TenantMetricsRead,
    TenantRegulatoryRunRead,
    TenantSsoConfigRead,
    TenantThresholdRegisterRead,
    TenantTranslationFailureRead,
)

#: Live-finding severity ordering for the active-alert sort (highest first).
_SEVERITY_RANK: dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _require_organization(db: Session, organization_id: str) -> Organization:
    organization = db.scalar(select(Organization).where(Organization.id == organization_id))
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )
    return organization


# -- metrics ---------------------------------------------------------------------
def get_tenant_metrics(db: Session, organization_id: str) -> TenantMetricsRead:
    """Latest LiveMetric per (bank, module) + latest RegulatoryRun per (bank,
    module/family). LiveMetric is unique on (org, bank, period, module), so the
    'latest' is the newest ``computed_at`` per (bank, module); runs likewise by
    ``created_at``."""
    _require_organization(db, organization_id)

    metric_rows = list(
        db.scalars(
            select(LiveMetric)
            .where(LiveMetric.organization_id == organization_id)
            .order_by(LiveMetric.computed_at.desc())
        )
    )
    latest_metrics: dict[tuple[str, str], LiveMetric] = {}
    for row in metric_rows:
        latest_metrics.setdefault((row.bank_id, row.module), row)
    live_metrics = [
        TenantLiveMetricRead(
            bank_id=row.bank_id,
            module=row.module,
            status=row.status,
            metrics=row.metrics,
            reporting_period_id=str(row.reporting_period_id),
            computed_from_input_hash=row.computed_from_input_hash,
            computed_at=row.computed_at,
        )
        for row in sorted(latest_metrics.values(), key=lambda r: (r.bank_id, r.module))
    ]

    run_rows = list(
        db.scalars(
            select(RegulatoryRun)
            .where(RegulatoryRun.organization_id == organization_id)
            .order_by(RegulatoryRun.created_at.desc())
        )
    )
    latest_runs: dict[tuple[str, str], RegulatoryRun] = {}
    for row in run_rows:
        latest_runs.setdefault((row.bank_id, row.module), row)
    regulatory_runs = [
        TenantRegulatoryRunRead(
            run_id=str(row.id),
            bank_id=row.bank_id,
            module=row.module,
            scenario_code=row.scenario_code,
            status=row.status,
            input_hash=row.input_hash,
            reporting_period_id=str(row.reporting_period_id),
            started_at=row.started_at,
            completed_at=row.completed_at,
            error_code=row.error_code,
        )
        for row in sorted(latest_runs.values(), key=lambda r: (r.bank_id, r.module))
    ]

    return TenantMetricsRead(
        organization_id=organization_id,
        live_metrics=live_metrics,
        regulatory_runs=regulatory_runs,
    )


# -- findings --------------------------------------------------------------------
def get_tenant_findings(db: Session, organization_id: str, limit: int) -> TenantFindingsRead:
    """Active alerts (open / needs_review), severity-ranked, followed by the most
    recently superseded findings — the whole list capped by ``limit``."""
    _require_organization(db, organization_id)

    active = list(
        db.scalars(
            select(LiveFinding).where(
                LiveFinding.organization_id == organization_id,
                LiveFinding.status.in_(("open", "needs_review")),
            )
        )
    )
    active.sort(
        key=lambda f: (_SEVERITY_RANK.get(f.severity, -1), f.created_at),
        reverse=True,
    )
    recent_superseded = list(
        db.scalars(
            select(LiveFinding)
            .where(
                LiveFinding.organization_id == organization_id,
                LiveFinding.status == "superseded",
            )
            .order_by(LiveFinding.updated_at.desc())
            .limit(limit)
        )
    )
    findings = [
        TenantFindingRead(
            bank_id=f.bank_id,
            module=f.module,
            rule_id=f.rule_id,
            severity=f.severity,
            status=f.status,
            message=f.message,
            metric=f.metric,
            reporting_period_id=str(f.reporting_period_id),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in (active + recent_superseded)[:limit]
    ]
    return TenantFindingsRead(
        organization_id=organization_id,
        findings=findings,
        open_count=sum(1 for f in active if f.status == "open"),
    )


# -- ingestion -------------------------------------------------------------------
def _ingestion_batch_read(batch: IngestionBatch) -> TenantIngestionBatchRead:
    return TenantIngestionBatchRead(
        batch_id=str(batch.id),
        bank_id=batch.bank_id,
        source_system=batch.source_system,
        adapter_version=batch.adapter_version,
        extraction_mode=batch.extraction_mode,
        status=batch.status,
        as_of_date=batch.as_of_date,
        records_extracted=batch.records_extracted,
        records_translated=batch.records_translated,
        records_accepted=batch.records_accepted,
        records_warning=batch.records_warning,
        records_error=batch.records_error,
        records_blocked=batch.records_blocked,
        error_code=batch.error_code,
        error_message=batch.error_message,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at,
    )


def list_tenant_ingestion(
    db: Session, organization_id: str, limit: int
) -> TenantIngestionListRead:
    """Recent ingestion batches, newest-first (status / source / as-of / row +
    error counts) — the upload health feed for one tenant."""
    _require_organization(db, organization_id)
    rows = list(
        db.scalars(
            select(IngestionBatch)
            .where(IngestionBatch.organization_id == organization_id)
            .order_by(IngestionBatch.created_at.desc())
            .limit(limit)
        )
    )
    return TenantIngestionListRead(
        organization_id=organization_id,
        batches=[_ingestion_batch_read(batch) for batch in rows],
    )


def get_tenant_ingestion_batch(
    db: Session, organization_id: str, batch_id: UUID
) -> TenantIngestionBatchDetailRead:
    """One batch with its validation/ETL reports and per-row translation failures
    — the detail needed to diagnose a failed upload. 404 if the batch is unknown
    or belongs to another tenant."""
    _require_organization(db, organization_id)
    batch = db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.organization_id == organization_id,
            IngestionBatch.id == batch_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion batch not found."
        )
    failures = list(
        db.scalars(
            select(TranslationFailure)
            .where(
                TranslationFailure.organization_id == organization_id,
                TranslationFailure.ingestion_batch_id == batch_id,
            )
            .order_by(TranslationFailure.created_at)
        )
    )
    return TenantIngestionBatchDetailRead(
        batch=_ingestion_batch_read(batch),
        validation_report=batch.validation_report,
        etl_report=batch.etl_report,
        translation_failures=[
            TenantTranslationFailureRead(
                entity_type=failure.entity_type,
                source_locator=failure.source_locator,
                error_code=failure.error_code,
                error_message=failure.error_message,
                raw_record=failure.raw_record,
                created_at=failure.created_at,
            )
            for failure in failures
        ],
    )


# -- config ----------------------------------------------------------------------
def get_tenant_config(db: Session, organization_id: str) -> TenantConfigRead:
    """Non-secret diagnostic config: bank identity (jurisdiction/currency),
    active mapping configs, the current Board threshold register, SSO connection
    (issuer/client id/domains — never the secret), and integration-key metadata
    (prefix/status/lifecycle — never the hash or raw key)."""
    _require_organization(db, organization_id)

    banks = list(
        db.scalars(
            select(Bank)
            .where(Bank.organization_id == organization_id)
            .order_by(Bank.created_at)
        )
    )
    bank_reads = [
        TenantConfigBankRead(
            bank_id=bank.id,
            name=bank.name,
            jurisdiction_code=bank.jurisdiction_code,
            currency=bank.currency,
            license_type=bank.license_type,
            institution_type=bank.institution_type,
        )
        for bank in banks
    ]

    mappings = list(
        db.scalars(
            select(MappingConfigRecord)
            .where(
                MappingConfigRecord.organization_id == organization_id,
                MappingConfigRecord.status == "active",
            )
            .order_by(MappingConfigRecord.bank_id, MappingConfigRecord.source_system)
        )
    )
    active_mappings = [
        TenantMappingConfigRead(
            id=str(mapping.id),
            bank_id=mapping.bank_id,
            source_system=mapping.source_system,
            source_ref=mapping.source_ref,
            version=mapping.version,
            name=mapping.name,
            status=mapping.status,
            config=mapping.config,
        )
        for mapping in mappings
    ]

    liquidity_thresholds = list(
        db.scalars(
            select(ParamLiquidityThreshold)
            .where(
                ParamLiquidityThreshold.organization_id == organization_id,
                ParamLiquidityThreshold.effective_to.is_(None),
            )
            .order_by(ParamLiquidityThreshold.threshold_code)
        )
    )
    capital_thresholds = list(
        db.scalars(
            select(ParamCapitalThreshold)
            .where(
                ParamCapitalThreshold.organization_id == organization_id,
                ParamCapitalThreshold.effective_to.is_(None),
            )
            .order_by(ParamCapitalThreshold.threshold_code)
        )
    )
    threshold_register = TenantThresholdRegisterRead(
        liquidity=[
            TenantLiquidityThresholdRead(
                id=str(threshold.id),
                institution_class=threshold.institution_class,
                threshold_code=threshold.threshold_code,
                threshold_pct=float(threshold.threshold_pct),
                effective_from=threshold.effective_from,
                effective_to=threshold.effective_to,
                approved_by=threshold.approved_by,
            )
            for threshold in liquidity_thresholds
        ],
        capital=[
            TenantCapitalThresholdRead(
                id=str(threshold.id),
                threshold_code=threshold.threshold_code,
                value_pct=float(threshold.value_pct),
                effective_from=threshold.effective_from,
                effective_to=threshold.effective_to,
                approved_by=threshold.approved_by,
            )
            for threshold in capital_thresholds
        ],
    )

    sso_row = db.scalar(
        select(SsoConnection).where(SsoConnection.organization_id == organization_id)
    )
    sso = (
        TenantSsoConfigRead(
            issuer=sso_row.issuer,
            client_id=sso_row.client_id,
            allowed_email_domains=list(sso_row.allowed_email_domains or []),
            enabled=sso_row.enabled,
            jit_enabled=sso_row.jit_enabled,
            # Only WHETHER a secret exists — never the ciphertext or fingerprint.
            secret_configured=sso_row.client_secret_ciphertext is not None,
        )
        if sso_row is not None
        else None
    )

    key_rows = list(
        db.scalars(
            select(IntegrationKey)
            .where(IntegrationKey.organization_id == organization_id)
            .order_by(IntegrationKey.created_at.desc())
        )
    )
    integration_keys = [
        TenantIntegrationKeyRead(
            label=key.label,
            key_prefix=key.key_prefix,
            status="revoked" if key.revoked_at is not None else "active",
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )
        for key in key_rows
    ]

    return TenantConfigRead(
        organization_id=organization_id,
        banks=bank_reads,
        active_mappings=active_mappings,
        threshold_register=threshold_register,
        sso=sso,
        integration_keys=integration_keys,
    )
