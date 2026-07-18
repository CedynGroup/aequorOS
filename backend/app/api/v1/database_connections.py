"""Database-Direct core-database connection management API.

Onboarding, credential rotation, disable/enable/revoke, a live connection test,
live schema discovery for mapping, and an on-demand sync that runs a read-only
pull through the ingestion spine. Credentials are write-only: they appear in
request bodies only, and no response carries values — only status, fingerprint,
and expiry. Mirrors the Temenos and market-data connection routers.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.database_connection import (
    DatabaseConnectionCreate,
    DatabaseConnectionDiscoverResult,
    DatabaseConnectionListRead,
    DatabaseConnectionRead,
    DatabaseConnectionSyncRequest,
    DatabaseConnectionSyncResult,
    DatabaseConnectionTestResult,
    DatabaseConnectionUpdate,
)
from app.services import database_connections
from app.storage.client import StorageClient
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client

router = APIRouter(tags=["database-direct"])


def get_database_direct_storage() -> StorageClient:
    try:
        return get_storage_client()
    except (StorageRetiredError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage is unavailable: {exc}",
        ) from exc


DatabaseDirectStorage = Annotated[StorageClient, Depends(get_database_direct_storage)]

_BASE = "/banks/{bank_id}/database-direct/connections"


@router.get(
    _BASE,
    response_model=DatabaseConnectionListRead,
    operation_id="listDatabaseDirectConnections",
)
def list_database_connections(
    bank_id: UUID, db: DbSession, ctx: Tenant
) -> DatabaseConnectionListRead:
    return database_connections.list_connections(db, ctx, bank_id)


@router.post(
    _BASE,
    response_model=DatabaseConnectionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createDatabaseDirectConnection",
)
def create_database_connection(
    bank_id: UUID,
    payload: DatabaseConnectionCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionRead:
    return database_connections.create_connection(db, ctx, bank_id, payload)


@router.get(
    _BASE + "/{connection_id}",
    response_model=DatabaseConnectionRead,
    operation_id="getDatabaseDirectConnection",
)
def get_database_connection(
    bank_id: UUID, connection_id: UUID, db: DbSession, ctx: Tenant
) -> DatabaseConnectionRead:
    return database_connections.get_connection(db, ctx, bank_id, connection_id)


@router.patch(
    _BASE + "/{connection_id}",
    response_model=DatabaseConnectionRead,
    operation_id="updateDatabaseDirectConnection",
)
def update_database_connection(
    bank_id: UUID,
    connection_id: UUID,
    payload: DatabaseConnectionUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionRead:
    return database_connections.update_connection(db, ctx, bank_id, connection_id, payload)


@router.post(
    _BASE + "/{connection_id}/disable",
    response_model=DatabaseConnectionRead,
    operation_id="disableDatabaseDirectConnection",
)
def disable_database_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionRead:
    return database_connections.disable_connection(db, ctx, bank_id, connection_id)


@router.post(
    _BASE + "/{connection_id}/enable",
    response_model=DatabaseConnectionRead,
    operation_id="enableDatabaseDirectConnection",
)
def enable_database_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionRead:
    return database_connections.enable_connection(db, ctx, bank_id, connection_id)


@router.delete(
    _BASE + "/{connection_id}",
    response_model=DatabaseConnectionRead,
    operation_id="revokeDatabaseDirectConnection",
)
def revoke_database_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionRead:
    return database_connections.revoke_connection(db, ctx, bank_id, connection_id)


@router.post(
    _BASE + "/{connection_id}/test",
    response_model=DatabaseConnectionTestResult,
    operation_id="testDatabaseDirectConnection",
)
def test_database_connection(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionTestResult:
    return database_connections.test_connection(db, ctx, bank_id, connection_id)


@router.get(
    _BASE + "/{connection_id}/schema",
    response_model=DatabaseConnectionDiscoverResult,
    operation_id="discoverDatabaseDirectSchema",
)
def discover_database_schema(
    bank_id: UUID,
    connection_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> DatabaseConnectionDiscoverResult:
    return database_connections.discover_schema(db, ctx, bank_id, connection_id)


@router.post(
    _BASE + "/{connection_id}/sync",
    response_model=DatabaseConnectionSyncResult,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="syncDatabaseDirectConnection",
)
def sync_database_connection(  # noqa: PLR0913 - FastAPI path params + deps + body
    bank_id: UUID,
    connection_id: UUID,
    payload: DatabaseConnectionSyncRequest,
    db: DbSession,
    ctx: MutationTenant,
    storage: DatabaseDirectStorage,
) -> DatabaseConnectionSyncResult:
    return database_connections.sync_now(db, ctx, bank_id, connection_id, storage, payload)
