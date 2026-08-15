"""GET /operator/v1/tenants/{org_id}/activity — one tenant's unified feed."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import operator_views
from app.schemas.operator import TenantActivityRead
from app.services.public_ids import normalize_public_id

router = APIRouter(prefix="/tenants", tags=["operator-activity"])


@router.get("/{org_id}/activity", response_model=TenantActivityRead)
def get_tenant_activity(
    org_id: str,
    db: OperatorDb,
    operator: Operator,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TenantActivityRead:
    """Merged, newest-first feed of ingestion batches, jobs, official runs,
    packages, and audit events — every underlying query explicitly scoped to
    this organization. Requires an active Tenant Inspector session (403
    otherwise); the read is audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = operator_views.get_tenant_activity(db, organization_id, limit)
    record_operator_action(
        db,
        operator,
        action="inspector.read.activity",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result
