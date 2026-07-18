"""Database-Direct core-database connection management API.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do. The live test / discover /
sync endpoints run against an offline fixture driver patched over the service's
``driver_for`` seam, so no live database is required.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.database_direct.config import ExtractionSpec
from app.adapters.database_direct.drivers.base import ColumnSchema, TableSchema
from app.adapters.database_direct.extraction import StagedBundle, StagedTable
from app.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.api.v1.database_connections import router as database_connections_router
from app.core.config import get_settings

# Importing the model registers its table on Base.metadata so db_client's
# create_all provisions it (the model is not wired into models/__init__ yet).
from app.models.database_connection import DatabaseDirectConnection  # noqa: F401
from app.services import database_connections as database_connections_service
from app.services.database_connections import _reconcile_as_of
from tests.api.helpers import ORG_2, USER_2, headers

MASTER_KEY = "db-direct-api-test-master-key"
SECRET = "svc-db-password-that-must-never-leak"
CREDENTIALS = {"username": "AEQUOROS_RO", "password": SECRET}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def dd_client(db_client: TestClient) -> TestClient:
    """The shared db_client with the (not-yet-wired) router mounted at /api/v1."""
    app = db_client.app
    assert isinstance(app, FastAPI)
    already = any(getattr(r, "name", "") == "list_database_connections" for r in app.routes)
    if not already:
        app.include_router(database_connections_router, prefix="/api/v1")
    return db_client


@pytest.fixture(autouse=True)
def _offline_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the service driver seam so test/discover/sync run offline."""
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
        rows={"DBO.GL_ACCOUNTS": [{"ACCT_CODE": "1000", "NAME": "Cash"}]},
    )
    monkeypatch.setattr(
        database_connections_service, "driver_for", lambda _backend: OfflineDumpDriver(dump)
    )


def _seed_bank(client: TestClient) -> str:
    response = client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["bank_id"]


def _base(bank_id: str) -> str:
    return f"/api/v1/banks/{bank_id}/database-direct/connections"


def _create(
    client: TestClient,
    bank_id: str,
    *,
    display_name: str = "Core SQL Server",
    credentials: dict[str, Any] | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "backend": "sqlserver",
        "display_name": display_name,
        "host": "core-db.internal",
        "port": 1433,
        "database": "COREBANK",
        "schemas": ["DBO"],
        "credentials": credentials if credentials is not None else CREDENTIALS,
        "extraction_spec": {
            "tables": [{"table": "DBO.GL_ACCOUNTS", "record_kind": "gl_account"}],
            "default_mode": "full",
        },
    }
    return client.post(_base(bank_id), headers=headers(), json=payload)


def test_create_activates_on_valid_credentials(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    response = _create(dd_client, bank_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["backend"] == "sqlserver"
    assert body["credential_fingerprint"]


def test_credentials_never_appear_in_any_response(
    dd_client: TestClient, vault_key: None
) -> None:
    bank_id = _seed_bank(dd_client)
    created = _create(dd_client, bank_id)
    assert SECRET not in created.text
    assert "password" not in created.json()
    listed = dd_client.get(_base(bank_id), headers=headers())
    assert SECRET not in listed.text


def test_bad_credential_shape_stays_testing(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    response = _create(dd_client, bank_id, credentials={"username": "RO"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]


def test_duplicate_display_name_conflicts(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    assert _create(dd_client, bank_id).status_code == 201
    assert _create(dd_client, bank_id).status_code == 409


def test_test_endpoint_reports_reachable(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    response = dd_client.post(f"{_base(bank_id)}/{conn_id}/test", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reachable"] is True
    assert body["rows_pulled"] == 0  # test proves connectivity via introspection; pulls no rows


def test_schema_discovery_lists_columns(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    response = dd_client.get(f"{_base(bank_id)}/{conn_id}/schema", headers=headers())
    assert response.status_code == 200, response.text
    tables = response.json()["tables"]
    assert tables[0]["name"] == "DBO.GL_ACCOUNTS"
    assert {c["name"] for c in tables[0]["columns"]} >= {"ACCT_CODE", "NAME"}


def test_schema_discovery_reports_row_count_and_samples(
    dd_client: TestClient, vault_key: None
) -> None:
    # Discovery now runs a bounded COUNT(*) + sample pull so the operator maps
    # against real values (the offline dump has one GL_ACCOUNTS row).
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    response = dd_client.get(f"{_base(bank_id)}/{conn_id}/schema", headers=headers())
    assert response.status_code == 200, response.text
    table = response.json()["tables"][0]
    assert table["row_count"] == 1
    by_name = {c["name"]: c for c in table["columns"]}
    assert by_name["ACCT_CODE"]["sample_values"] == ["1000"]
    assert by_name["NAME"]["sample_values"] == ["Cash"]


def test_sync_requires_active_mapping(dd_client: TestClient, vault_key: None) -> None:
    # With no DB_DIRECT mapping config the ingestion spine rejects the sync (422),
    # proving the sync genuinely routes through start_ingestion.
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    response = dd_client.post(
        f"{_base(bank_id)}/{conn_id}/sync",
        headers=headers(),
        json={"as_of_date": "2026-06-30"},
    )
    assert response.status_code == 422, response.text


def test_sync_ingests_when_mapping_present(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    mapping = dd_client.post(
        f"/api/v1/banks/{bank_id}/mapping-configs",
        headers=headers(),
        json={
            "source_system": "DB_DIRECT",
            "name": "DB Direct default",
            "config": {
                "field_mappings": {
                    "gl_account": {"source_table": "GL_ACCOUNTS", "fields": {"code": "ACCT_CODE"}}
                }
            },
            "activate": True,
            "reason": "test mapping",
        },
    )
    assert mapping.status_code in (200, 201), mapping.text
    response = dd_client.post(
        f"{_base(bank_id)}/{conn_id}/sync",
        headers=headers(),
        json={"as_of_date": "2026-06-30"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["batch_id"]
    assert body["records_extracted"] == 1


def test_disable_enable_revoke_lifecycle(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    conn_id = _create(dd_client, bank_id).json()["id"]
    assert (
        dd_client.post(f"{_base(bank_id)}/{conn_id}/disable", headers=headers()).json()["status"]
        == "DISABLED"
    )
    assert (
        dd_client.post(f"{_base(bank_id)}/{conn_id}/enable", headers=headers()).json()["status"]
        == "ACTIVE"
    )
    revoked = dd_client.delete(f"{_base(bank_id)}/{conn_id}", headers=headers())
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["credential_fingerprint"] is None


def test_tenant_isolation(dd_client: TestClient, vault_key: None) -> None:
    bank_id = _seed_bank(dd_client)
    _create(dd_client, bank_id)
    other = dd_client.get(_base(bank_id), headers=headers(org_id=ORG_2, user_id=USER_2))
    assert other.status_code == 404


class TestAsOfReconciliation:
    """The sync adopts the snapshot's own reporting date over a wrong request."""

    def _bundle(self, as_of_value: str) -> StagedBundle:
        return StagedBundle(
            backend="oracle",
            as_of_date="2026-06-30",
            source_database="CORE",
            extraction_mode="full",
            tables=(
                StagedTable(
                    name="CORE.IFTB_DEPOSIT",
                    record_kind="position",
                    dataset_kind=None,
                    columns=("SOURCE_REFERENCE", "AS_OF_DATE"),
                    rows=[{"SOURCE_REFERENCE": "D1", "AS_OF_DATE": as_of_value}],
                    extraction_mode="full",
                ),
            ),
            warnings=[],
            incremental_cursors={},
        )

    def test_adopts_source_date_on_mismatch(self) -> None:
        spec = ExtractionSpec(as_of_column="AS_OF_DATE")
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-04-30T00:00:00"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 4, 30)
        assert note is not None and "2026-04-30" in note

    def test_no_change_when_dates_match(self) -> None:
        spec = ExtractionSpec(as_of_column="AS_OF_DATE")
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-06-30"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 6, 30)
        assert note is None

    def test_no_reconciliation_without_as_of_column(self) -> None:
        spec = ExtractionSpec()  # as_of_column unset -> requested date stands
        effective, note = _reconcile_as_of(
            spec, self._bundle("2026-04-30"), requested=date(2026, 6, 30)
        )
        assert effective == date(2026, 6, 30)
        assert note is None
