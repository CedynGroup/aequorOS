"""GET /operator/v1/tenants — the cross-tenant health board + single-tenant detail."""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.operator.inspection import require_active_inspection
from app.operator.services import operator_views
from app.schemas.operator import (
    TenantEntitlementsRead,
    TenantRead,
    TenantsListRead,
    TenantStorageRead,
    TenantUsersListRead,
)
from app.services.public_ids import normalize_public_id

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


@router.get("/{org_id}", response_model=TenantRead)
def get_tenant(org_id: str, db: OperatorDb, operator: Operator) -> TenantRead:
    """One tenant's health row (the same shape the list returns), so the console
    can refresh a single tenant without refetching the whole board. 404 if the
    org is unknown. Logged as ``tenants.get`` — a scoped cross-tenant read."""
    organization_id = normalize_public_id(org_id)
    result = operator_views.get_tenant(db, organization_id)
    record_operator_action(
        db, operator, action="tenants.get", target_org=organization_id, detail={}
    )
    db.commit()
    return result


@router.get("/{org_id}/users", response_model=TenantUsersListRead)
def get_tenant_users(
    org_id: str, db: OperatorDb, operator: Operator
) -> TenantUsersListRead:
    """The tenant's own user directory, scoped strictly to this org. Requires an
    active Tenant Inspector session (403 otherwise); the read is audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = operator_views.list_tenant_users(db, organization_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.users",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/entitlements", response_model=TenantEntitlementsRead)
def get_tenant_entitlements(
    org_id: str, db: OperatorDb, operator: Operator
) -> TenantEntitlementsRead:
    """The desk dataset entitlements for one tenant plus the grant catalog.
    Requires an active Tenant Inspector session (403 otherwise); audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = operator_views.get_tenant_entitlements(db, organization_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.entitlements",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result


@router.get("/{org_id}/storage", response_model=TenantStorageRead)
def get_tenant_storage(
    org_id: str, db: OperatorDb, operator: Operator
) -> TenantStorageRead:
    """Best-effort object-storage view — never fails on the documented MinIO
    quirks (Cloudflare WAF blocks HEAD; no KES). Requires an active Tenant
    Inspector session (403 otherwise); audited."""
    organization_id = normalize_public_id(org_id)
    session = require_active_inspection(db, operator, organization_id)
    result = operator_views.get_tenant_storage(db, organization_id)
    record_operator_action(
        db,
        operator,
        action="inspector.read.storage",
        target_org=organization_id,
        detail={"session_id": str(session.id)},
    )
    db.commit()
    return result
