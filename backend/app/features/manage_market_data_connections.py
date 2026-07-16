"""Market data connection management API (market_data_adapter.md §9.3/§10).

Complements ``manage_market_data_uploads``: uploads push template files
through the manual-upload adapter, while these endpoints manage the vendor
connections themselves — onboarding, validation, test pulls, rotation,
disable/enable, revocation — plus the scope catalog and quota views.

Credentials are write-only: they appear in request bodies only, and no
response ever carries credential values.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.market_data_connections import (
    MarketDataConnectionCreate,
    MarketDataConnectionListRead,
    MarketDataConnectionRead,
    MarketDataConnectionUpdate,
    MarketDataQuotaListRead,
    MarketDataScopeListRead,
    TestPullRead,
)
from app.services import market_data_connections

router = APIRouter(tags=["market-data"])


@router.get(
    "/banks/{bank_id}/market-data/connections",
    response_model=MarketDataConnectionListRead,
    operation_id="listMarketDataConnections",
)
def list_market_data_connections(
    bank_id: UUID, db: DbSession, ctx: Tenant
) -> MarketDataConnectionListRead:
    return market_data_connections.list_connections(db, ctx, bank_id)


@router.post(
    "/banks/{bank_id}/market-data/connections",
    response_model=MarketDataConnectionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMarketDataConnection",
)
def create_market_data_connection(
    bank_id: UUID,
    payload: MarketDataConnectionCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.create_connection(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/market-data/connections/{connection_id}/validate",
    response_model=MarketDataConnectionRead,
    operation_id="validateMarketDataConnection",
)
def validate_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.validate_connection(db, ctx, bank_id, connection_id)


@router.post(
    "/banks/{bank_id}/market-data/connections/{connection_id}/test",
    response_model=TestPullRead,
    operation_id="testMarketDataConnection",
)
def test_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> TestPullRead:
    return market_data_connections.test_connection(db, ctx, bank_id, connection_id)


@router.patch(
    "/banks/{bank_id}/market-data/connections/{connection_id}",
    response_model=MarketDataConnectionRead,
    operation_id="updateMarketDataConnection",
)
def update_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    payload: MarketDataConnectionUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.update_connection(db, ctx, bank_id, connection_id, payload)


@router.post(
    "/banks/{bank_id}/market-data/connections/{connection_id}/disable",
    response_model=MarketDataConnectionRead,
    operation_id="disableMarketDataConnection",
)
def disable_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.disable_connection(db, ctx, bank_id, connection_id)


@router.post(
    "/banks/{bank_id}/market-data/connections/{connection_id}/enable",
    response_model=MarketDataConnectionRead,
    operation_id="enableMarketDataConnection",
)
def enable_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.enable_connection(db, ctx, bank_id, connection_id)


@router.delete(
    "/banks/{bank_id}/market-data/connections/{connection_id}",
    response_model=MarketDataConnectionRead,
    operation_id="revokeMarketDataConnection",
)
def revoke_market_data_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataConnectionRead:
    return market_data_connections.revoke_connection(db, ctx, bank_id, connection_id)


@router.get(
    "/banks/{bank_id}/market-data/scopes",
    response_model=MarketDataScopeListRead,
    operation_id="listMarketDataScopes",
)
def list_market_data_scopes(bank_id: UUID, db: DbSession, ctx: Tenant) -> MarketDataScopeListRead:
    return market_data_connections.list_scopes(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/market-data/quota",
    response_model=MarketDataQuotaListRead,
    operation_id="getMarketDataQuota",
)
def get_market_data_quota(bank_id: UUID, db: DbSession, ctx: Tenant) -> MarketDataQuotaListRead:
    return market_data_connections.get_quota(db, ctx, bank_id)
