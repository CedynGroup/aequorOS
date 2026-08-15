"""Session-gated deep tenant reads (Tenant Inspector, step 1).

Every route here is a diagnostic look INSIDE one tenant and is gated on an
ACTIVE inspector session the caller opened (``require_active_inspection`` → 403
otherwise) AND audited (``inspector.read.<resource>`` with the session id in the
detail). This is the difference between the fleet-metadata surfaces (open) and
looking at a tenant's actual data (session + audit). Nothing here mints a tenant
token or returns secret material.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import inspector_reads
from app.schemas.operator import (
    TenantConfigRead,
    TenantFindingsRead,
    TenantIngestionBatchDetailRead,
    TenantIngestionListRead,
    TenantMetricsRead,
)
from app.services.public_ids import normalize_public_id

router = APIRouter(prefix="/tenants", tags=["operator-inspector-reads"])


@router.get("/{org_id}/metrics", response_model=TenantMetricsRead)
def get_tenant_metrics(org_id: str, db: OperatorDb, operator: Operator) -> TenantMetricsRead:
    """The bank's computed module outputs: latest LiveMetric per module plus a
    latest RegulatoryRun summary per family. Session-gated + audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_reads.get_tenant_metrics(db, organization_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.metrics",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/findings", response_model=TenantFindingsRead)
def get_tenant_findings(
    org_id: str,
    db: OperatorDb,
    operator: Operator,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TenantFindingsRead:
    """The bank's live findings/alerts (active first, then recently cleared) with
    severity + module. Session-gated + audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_reads.get_tenant_findings(db, organization_id, limit)
    record_operator_action(
        db,
        operator,
        action="inspector.read.findings",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/ingestion", response_model=TenantIngestionListRead)
def get_tenant_ingestion(
    org_id: str,
    db: OperatorDb,
    operator: Operator,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TenantIngestionListRead:
    """Recent ingestion batches (status / source / as-of / row + error counts).
    Session-gated + audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_reads.list_tenant_ingestion(db, organization_id, limit)
    record_operator_action(
        db,
        operator,
        action="inspector.read.ingestion",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/ingestion/{batch_id}", response_model=TenantIngestionBatchDetailRead)
def get_tenant_ingestion_batch(
    org_id: str, batch_id: UUID, db: OperatorDb, operator: Operator
) -> TenantIngestionBatchDetailRead:
    """One ingestion batch with validation/ETL reports and per-row translation
    failures — to diagnose a failed upload. Session-gated + audited; 404 if the
    batch is unknown or belongs to another tenant."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_reads.get_tenant_ingestion_batch(db, organization_id, batch_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.ingestion_batch",
        target_org=organization_id,
        detail={"session_id": str(session.id), "batch_id": str(batch_id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/config", response_model=TenantConfigRead)
def get_tenant_config(org_id: str, db: OperatorDb, operator: Operator) -> TenantConfigRead:
    """Non-secret diagnostic config: bank jurisdiction/currency, active mappings,
    Board threshold register, SSO connection (no secret), and integration-key
    metadata (no raw key). Session-gated + audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = inspector_reads.get_tenant_config(db, organization_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.config",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result
