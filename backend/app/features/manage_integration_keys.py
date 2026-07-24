"""Integration keys: admin-gated issue/list/revoke for bank middleware.

The raw key is returned exactly once at issuance; listing exposes only the
display prefix and lifecycle metadata. Requests authenticated WITH an
integration key cannot manage keys (admin role required — service accounts
are analysts).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, TenantContext, require_role
from app.schemas.integration_keys import (
    IntegrationKeyIssued,
    IntegrationKeyIssueRequest,
    IntegrationKeyListRead,
    IntegrationKeyRead,
    IntegrationKeyRevokeRequest,
)
from app.services import integration_keys

router = APIRouter(tags=["integration-keys"])

AdminCtx = Annotated[TenantContext, Depends(require_role("admin"))]


@router.get(
    "/integration-keys",
    response_model=IntegrationKeyListRead,
    operation_id="listIntegrationKeys",
)
def list_integration_keys(db: DbSession, ctx: AdminCtx) -> IntegrationKeyListRead:
    return integration_keys.list_keys(db, ctx)


@router.post(
    "/integration-keys",
    response_model=IntegrationKeyIssued,
    status_code=201,
    operation_id="issueIntegrationKey",
)
def issue_integration_key(
    payload: IntegrationKeyIssueRequest, db: DbSession, ctx: AdminCtx
) -> IntegrationKeyIssued:
    return integration_keys.issue_key(db, ctx, payload.label)


@router.post(
    "/integration-keys/{key_id}/revoke",
    response_model=IntegrationKeyRead,
    operation_id="revokeIntegrationKey",
)
def revoke_integration_key(
    key_id: UUID, payload: IntegrationKeyRevokeRequest, db: DbSession, ctx: AdminCtx
) -> IntegrationKeyRead:
    return integration_keys.revoke_key(db, ctx, key_id, payload.reason)
