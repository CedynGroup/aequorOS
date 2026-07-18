"""Database-Direct connection service: credential sealing, live test/discover
over the offline fixture driver, and the sync path through the ingestion spine.

No live database is ever required: the driver is injected as an
:class:`OfflineDumpDriver` backed by a synthetic dump, so the whole pull path
(query build, normalization, bundle staging) runs offline. The load-bearing
concern is that sealed credentials round-trip the vault and never leak.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.adapters.database_direct.drivers.base import ColumnSchema, TableSchema
from app.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.api.deps import TenantContext
from app.core.config import get_settings

# Importing the model registers its table on Base.metadata so db_session's
# create_all provisions it (the model is not wired into models/__init__ yet).
from app.models import Bank, MappingConfigRecord
from app.models.database_connection import DatabaseDirectConnection
from app.schemas.database_connection import (
    DatabaseConnectionCreate,
    DatabaseConnectionSyncRequest,
    DatabaseConnectionUpdate,
)
from app.services import database_connections
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

MASTER_KEY = "db-direct-service-test-master-key"
SECRET = "svc-db-password-that-must-never-leak"
CREDENTIALS = {"username": "AEQUOROS_RO", "password": SECRET}
AS_OF = date(2026, 6, 30)


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="DB Direct Bank",
        short_name="db-direct",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db.add(bank)
    db.flush()
    return bank


def _offline_driver() -> OfflineDumpDriver:
    tables = (
        TableSchema(
            name="GL_ACCOUNTS",
            schema="DBO",
            columns=(
                ColumnSchema(name="ACCT_CODE", data_type="varchar", nullable=False),
                ColumnSchema(name="NAME", data_type="varchar"),
            ),
        ),
    )
    dump = Dump(
        database="COREBANK",
        tables=tables,
        rows={
            "DBO.GL_ACCOUNTS": [
                {"ACCT_CODE": "1000", "NAME": "Cash"},
                {"ACCT_CODE": "2000", "NAME": "Deposits"},
            ]
        },
    )
    return OfflineDumpDriver(dump, backend="sqlserver")


def _create_payload(**overrides: object) -> DatabaseConnectionCreate:
    payload: dict[str, object] = {
        "backend": "sqlserver",
        "display_name": "Core SQL Server",
        "host": "core-db.internal",
        "port": 1433,
        "database": "COREBANK",
        "schemas": ["DBO"],
        "credentials": CREDENTIALS,
        "extraction_spec": {
            "tables": [{"table": "DBO.GL_ACCOUNTS", "record_kind": "gl_account"}],
            "default_mode": "full",
        },
    }
    payload.update(overrides)
    return DatabaseConnectionCreate.model_validate(payload)


def _seed_mapping(db: Session, bank: Bank) -> None:
    db.add(
        MappingConfigRecord(
            organization_id=ORG_1,
            bank_id=bank.id,
            source_system="DB_DIRECT",
            version=1,
            status="active",
            name="DB Direct default",
            config={
                "field_mappings": {
                    "gl_account": {"source_table": "GL_ACCOUNTS", "fields": {"code": "ACCT_CODE"}}
                }
            },
            created_by=USER_1,
        )
    )
    db.flush()


def test_create_seals_credentials_and_activates(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    assert read.status == "ACTIVE"
    assert read.credential_fingerprint
    assert read.validation_error is None
    # The sealed ciphertext exists on the row but the secret never appears in the view.
    row = db_session.get(DatabaseDirectConnection, read.id)
    assert row is not None
    assert row.credential_ciphertext is not None
    assert SECRET not in row.credential_ciphertext
    assert SECRET not in read.model_dump_json()


def test_create_bad_credential_shape_stays_testing(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(
        db_session, ctx, bank.id, _create_payload(credentials={"username": "RO"})
    )
    assert read.status == "TESTING"
    assert read.validation_error


def test_create_rejects_jdbc_without_block(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    with pytest.raises(HTTPException) as excinfo:
        database_connections.create_connection(
            db_session, ctx, bank.id, _create_payload(backend="jdbc", host="", port=None)
        )
    assert excinfo.value.status_code == 400


def test_test_connection_reports_reachable_and_latency(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    result = database_connections.test_connection(
        db_session, ctx, bank.id, read.id, driver=_offline_driver()
    )
    assert result.reachable is True
    assert result.latency_ms is not None
    # Test proves live connectivity via schema introspection (no extraction spec needed);
    # it reports the tables the account can see and pulls no rows.
    assert result.tables_pulled == 1
    assert result.rows_pulled == 0
    assert result.error is None


def test_discover_schema_lists_tables_and_columns(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    discovered = database_connections.discover_schema(
        db_session, ctx, bank.id, read.id, driver=_offline_driver()
    )
    assert len(discovered.tables) == 1
    table = discovered.tables[0]
    assert table.name == "DBO.GL_ACCOUNTS"  # schema-qualified from live introspection
    column_names = {c.name for c in table.columns}
    assert {"ACCT_CODE", "NAME"} <= column_names


def test_sync_now_runs_through_ingestion(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    _seed_mapping(db_session, bank)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    storage = InMemoryStorageClient()
    result = database_connections.sync_now(
        db_session,
        ctx,
        bank.id,
        read.id,
        storage,
        DatabaseConnectionSyncRequest(as_of_date=AS_OF),
        driver=_offline_driver(),
    )
    assert result.batch_id is not None
    assert result.records_extracted == 2
    row = db_session.get(DatabaseDirectConnection, read.id)
    assert row is not None
    assert row.last_sync_status == result.status
    assert row.last_synced_at is not None


def test_rotate_credentials_changes_fingerprint(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    before = read.credential_fingerprint
    updated = database_connections.update_connection(
        db_session,
        ctx,
        bank.id,
        read.id,
        DatabaseConnectionUpdate(credentials={"username": "RO_NEW", "password": "another-secret"}),
    )
    assert updated.credential_fingerprint != before


def test_revoke_wipes_credential(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    bank = _bank(db_session)
    read = database_connections.create_connection(db_session, ctx, bank.id, _create_payload())
    revoked = database_connections.revoke_connection(db_session, ctx, bank.id, read.id)
    assert revoked.status == "REVOKED"
    assert revoked.credential_fingerprint is None
    row = db_session.get(DatabaseDirectConnection, read.id)
    assert row is not None
    assert row.credential_ciphertext is None
