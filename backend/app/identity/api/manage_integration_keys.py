"""Integration keys: issue/list/revoke machine credentials for one institution.

Data push keys are issued through the atomic service path, which creates the
identity, credential and machine binding together. Analytics reader issuance is
retired until BI ships with governed delegation.

The raw key is returned exactly once at issuance; listing exposes only the
display prefix, the lifecycle metadata and the derived supported purpose.
Requests authenticated WITH an integration key cannot list or revoke keys
(scoped account-administration authority requires a human principal), and every
one of these three endpoints is explicitly organization-filtered in the service
because ``integration_keys`` is deliberately not RLS-forced.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import (
    DbSession,
    TenantContext,
    require_account_administration,
)
from app.identity.schemas.integration_keys import (
    IntegrationKeyIssued,
    IntegrationKeyIssueRequest,
    IntegrationKeyListRead,
    IntegrationKeyRead,
    IntegrationKeyRevokeRequest,
)
from app.identity.service import integration_keys

router = APIRouter(tags=["integration-keys"])

AdminCtx = Annotated[TenantContext, Depends(require_account_administration)]


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
    """Issue a data push key for an exact bank under Account administration."""

    return integration_keys.issue_key(
        db,
        ctx,
        payload.bank_id,
        payload.label,
        purpose=payload.purpose,
        data_scope=payload.data_scope_kind,
        data_scope_values=payload.data_scope_values,
    )


@router.post(
    "/integration-keys/{key_id}/revoke",
    response_model=IntegrationKeyRead,
    operation_id="revokeIntegrationKey",
)
def revoke_integration_key(
    key_id: UUID, payload: IntegrationKeyRevokeRequest, db: DbSession, ctx: AdminCtx
) -> IntegrationKeyRead:
    return integration_keys.revoke_key(db, ctx, key_id, payload.reason)
