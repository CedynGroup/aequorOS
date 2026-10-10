"""Integration keys: issue/list/revoke machine credentials for one institution.

Two purposes, one flow. A ``writer`` key pushes canonical data (Data Engine →
API Push); a ``reader`` key pulls the curated analytics feed
(``docs/bi.md`` §Phase 4) and may optionally be narrowed to branches or regions.
Both are issued through the same atomic path in
``app/identity/service/integration_keys.py``, which creates the service identity, the
credential and the ONE binding that carries its authority together — so there is
no second mechanism to review, and revoking either ends all three.

The raw key is returned exactly once at issuance; listing exposes only the
display prefix, the lifecycle metadata and the DERIVED purpose and slice.
Requests authenticated WITH an integration key cannot list or revoke keys
(scoped account-administration authority requires a human principal), and every
one of these three reads is explicitly organization-filtered in the service
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
    """Issue one credential for one exact institution, for one purpose.

    The authority required is unchanged by the second purpose: one
    organization-wide ACCOUNT/restricted ``administer`` binding
    (``require_account_administration``) plus an exact ``BK-*`` target, which the
    service resolves inside the caller's tenant.
    """

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
