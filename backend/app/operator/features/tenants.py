"""GET /operator/v1/tenants — the cross-tenant health board."""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.services import operator_views
from app.schemas.operator import TenantsListRead

router = APIRouter(prefix="/tenants", tags=["operator-tenants"])


@router.get("", response_model=TenantsListRead)
def list_tenants(db: OperatorDb, operator: Operator) -> TenantsListRead:
    """Orgs + banks with period spine, freshness, ingestion, SSO and storage
    state. This read IS logged (one row per call): it enumerates every
    tenant, which is exactly the kind of cross-tenant access a bank's
    diligence asks about."""
    result = operator_views.list_tenants(db)
    record_operator_action(
        db,
        operator,
        action="tenants.list",
        detail={"tenant_rows": len(result.tenants)},
    )
    db.commit()
    return result
