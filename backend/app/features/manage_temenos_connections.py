"""Temenos core-banking connection management API.

Onboarding, validation, test, credential rotation, disable/enable, revocation of
a bank's Temenos connection, plus the enabled-domain catalog. Credentials are
write-only: they appear in request bodies only, and no response carries values.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.temenos_connections import (
    TemenosBackfillRequest,
    TemenosConnectionCreate,
    TemenosConnectionListRead,
    TemenosConnectionRead,
    TemenosConnectionUpdate,
    TemenosDomainListRead,
    TemenosPullTriggerRead,
    TemenosPullTriggerRequest,
    TemenosTestPullRead,
)
from app.services import temenos_connections

router = APIRouter(tags=["temenos"])


@router.get(
    "/banks/{bank_id}/temenos/connections",
    response_model=TemenosConnectionListRead,
    operation_id="listTemenosConnections",
)
def list_temenos_connections(
    bank_id: UUID, db: DbSession, ctx: Tenant
) -> TemenosConnectionListRead:
    return temenos_connections.list_connections(db, ctx, bank_id)


@router.post(
    "/banks/{bank_id}/temenos/connections",
    response_model=TemenosConnectionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTemenosConnection",
)
def create_temenos_connection(
    bank_id: UUID,
    payload: TemenosConnectionCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.create_connection(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/validate",
    response_model=TemenosConnectionRead,
    operation_id="validateTemenosConnection",
)
def validate_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.validate_connection(db, ctx, bank_id, connection_id)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/test",
    response_model=TemenosTestPullRead,
    operation_id="testTemenosConnection",
)
def test_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosTestPullRead:
    return temenos_connections.test_connection(db, ctx, bank_id, connection_id)


@router.patch(
    "/banks/{bank_id}/temenos/connections/{connection_id}",
    response_model=TemenosConnectionRead,
    operation_id="updateTemenosConnection",
)
def update_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosConnectionUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.update_connection(db, ctx, bank_id, connection_id, payload)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/disable",
    response_model=TemenosConnectionRead,
    operation_id="disableTemenosConnection",
)
def disable_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.disable_connection(db, ctx, bank_id, connection_id)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/enable",
    response_model=TemenosConnectionRead,
    operation_id="enableTemenosConnection",
)
def enable_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.enable_connection(db, ctx, bank_id, connection_id)


@router.delete(
    "/banks/{bank_id}/temenos/connections/{connection_id}",
    response_model=TemenosConnectionRead,
    operation_id="revokeTemenosConnection",
)
def revoke_temenos_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosConnectionRead:
    return temenos_connections.revoke_connection(db, ctx, bank_id, connection_id)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/pull",
    response_model=TemenosPullTriggerRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="triggerTemenosPull",
)
def trigger_temenos_pull(
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosPullTriggerRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosPullTriggerRead:
    return temenos_connections.trigger_pull(db, ctx, bank_id, connection_id, payload)


@router.post(
    "/banks/{bank_id}/temenos/connections/{connection_id}/backfill",
    response_model=TemenosPullTriggerRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="triggerTemenosBackfill",
)
def trigger_temenos_backfill(
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosBackfillRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> TemenosPullTriggerRead:
    return temenos_connections.trigger_backfill(db, ctx, bank_id, connection_id, payload)


@router.get(
    "/banks/{bank_id}/temenos/domains",
    response_model=TemenosDomainListRead,
    operation_id="listTemenosDomains",
)
def list_temenos_domains(
    bank_id: UUID, db: DbSession, ctx: Tenant, mode: str = "OFS"
) -> TemenosDomainListRead:
    return temenos_connections.list_domains(db, ctx, bank_id, mode)
