"""Database-Direct core-database connection management API on the ACTUAL primary.

Credential handling is the load-bearing concern: credentials go in through
request bodies, round-trip the encrypted vault, and must NEVER appear in any
response — only status, fingerprint, and expiry do. The live test / discover /
sync endpoints run against an offline fixture driver patched over the service's
``driver_for`` seam, so no live database is ever reached. Invariants: activation
on valid credentials, TESTING on a bad shape, name conflicts (409), lifecycle
states, the sync routes through the ingestion spine (422 without a mapping,
202 + a batch with one), tenant isolation. The real bank already holds
connections; the vault key is set by the test (never read from .env). Opt-in via
REAL_DATA_DATABASE_URL, rolled back (tests/real_data.py).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.database_direct.config import ExtractionSpec
from app.adapters.database_direct.drivers.base import ColumnSchema, TableSchema
from app.adapters.database_direct.extraction import StagedBundle, StagedTable
from app.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.api.v1.database_connections import get_database_direct_storage
from app.core.config import get_settings
from app.services import database_connections as database_connections_service
from app.services.database_connections import _reconcile_as_of
from tests.factories.outbound import stub_public_dns
from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data
from tests.storage.inmemory import InMemoryStorageClient

MASTER_KEY = "db-direct-api-test-master-key"
SECRET = "svc-db-password-that-must-never-leak"
CREDENTIALS = {"username": "AEQUOROS_RO", "password": SECRET}
BASE = f"/api/v1/banks/{REAL_BANK_ID}/database-direct/connections"


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def dd_client(real_client: TestClient, storage_engine: InMemoryStorageClient) -> TestClient:
    """``real_client`` with the db-direct storage dependency pointed at the same
    in-memory engine the ingestion path uses.

    The db-direct router resolves storage through its own
    ``get_database_direct_storage`` dependency (the app.storage factory), which
    ``real_client`` does not override — so point it at the in-memory engine, or a
    sync would hit the real storage client.
    """
    app = real_client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[get_database_direct_storage] = lambda: storage_engine
    return real_client


@pytest.fixture(autouse=True)
def _resolvable_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """The egress guard resolves the host before every live connect; stub DNS
    so the suite stays offline and deterministic."""
    stub_public_dns(monkeypatch, "core-db.internal")


@pytest.fixture(autouse=True)
def _offline_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the service driver seam so test/discover/sync run offline — no
    core database is ever contacted."""
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


def _unique_name(stem: str = "Core SQL Server") -> str:
    """The real bank already holds connections; never collide with their names."""
    return f"{stem} [{uuid4().hex[:8]}]"


def _create(
    client: TestClient,
    *,
    display_name: str | None = None,
    credentials: dict[str, Any] | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "backend": "sqlserver",
        "display_name": display_name or _unique_name(),
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
    return client.post(BASE, headers=real_headers(), json=payload)


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_create_activates_on_valid_credentials(dd_client: TestClient) -> None:
    before = dd_client.get(BASE, headers=real_headers()).json()
    response = _create(dd_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["backend"] == "sqlserver"
    assert body["credential_fingerprint"]
    after = dd_client.get(BASE, headers=real_headers()).json()
    assert after["total"] == before["total"] + 1
    assert body["id"] in {item["id"] for item in after["connections"]}


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_credentials_never_appear_in_any_response(dd_client: TestClient) -> None:
    created = _create(dd_client)
    assert created.status_code == 201, created.text
    assert SECRET not in created.text
    assert "password" not in created.json()
    listed = dd_client.get(BASE, headers=real_headers())
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert "credential_ciphertext" not in listed.text


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_bad_credential_shape_stays_testing(dd_client: TestClient) -> None:
    response = _create(dd_client, credentials={"username": "RO"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "TESTING"
    assert body["validation_error"]


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_duplicate_display_name_conflicts(dd_client: TestClient) -> None:
    name = _unique_name()
    assert _create(dd_client, display_name=name).status_code == 201
    assert _create(dd_client, display_name=name).status_code == 409


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_test_endpoint_reports_reachable(dd_client: TestClient) -> None:
    conn_id = _create(dd_client).json()["id"]
    response = dd_client.post(f"{BASE}/{conn_id}/test", headers=real_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reachable"] is True
    assert body["rows_pulled"] == 0  # test proves connectivity via introspection; pulls no rows


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_schema_discovery_lists_columns(dd_client: TestClient) -> None:
    conn_id = _create(dd_client).json()["id"]
    response = dd_client.get(f"{BASE}/{conn_id}/schema", headers=real_headers())
    assert response.status_code == 200, response.text
    tables = response.json()["tables"]
    assert tables[0]["name"] == "DBO.GL_ACCOUNTS"
    assert {c["name"] for c in tables[0]["columns"]} >= {"ACCT_CODE", "NAME"}


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_schema_discovery_reports_row_count_and_samples(dd_client: TestClient) -> None:
    # Discovery runs a bounded COUNT(*) + sample pull so the operator maps
    # against real values (the offline dump has one GL_ACCOUNTS row).
    conn_id = _create(dd_client).json()["id"]
    response = dd_client.get(f"{BASE}/{conn_id}/schema", headers=real_headers())
    assert response.status_code == 200, response.text
    table = response.json()["tables"][0]
    assert table["row_count"] == 1
    by_name = {c["name"]: c for c in table["columns"]}
    assert by_name["ACCT_CODE"]["sample_values"] == ["1000"]
    assert by_name["NAME"]["sample_values"] == ["Cash"]


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_sync_requires_active_mapping(dd_client: TestClient) -> None:
    # A NEW connection is its own data source (mapping scoped by source_ref =
    # connection id); with neither a connection-scoped nor a bank-wide DB_DIRECT
    # mapping active, the ingestion spine rejects the sync (422) — proving the
    # sync genuinely routes through start_ingestion.
    conn_id = _create(dd_client).json()["id"]
    response = dd_client.post(
        f"{BASE}/{conn_id}/sync",
        headers=real_headers(),
        json={"as_of_date": "2026-06-30"},
    )
    assert response.status_code == 422, response.text


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_sync_ingests_when_mapping_present(dd_client: TestClient) -> None:
    conn_id = _create(dd_client).json()["id"]
    mapping = dd_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/mapping-configs",
        headers=real_headers(),
        json={
            "source_system": "DB_DIRECT",
            # Scoped to THIS connection so the real bank's other DB_DIRECT
            # sources (and their active mappings) stay untouched.
            "source_ref": conn_id,
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
        f"{BASE}/{conn_id}/sync",
        headers=real_headers(),
        json={"as_of_date": "2026-06-30"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["batch_id"]
    assert body["records_extracted"] == 1

    batch = dd_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/ingestion-batches/{body['batch_id']}",
        headers=real_headers(),
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["source_system"] == "DB_DIRECT"


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_disable_enable_revoke_lifecycle(dd_client: TestClient) -> None:
    conn_id = _create(dd_client).json()["id"]
    assert (
        dd_client.post(f"{BASE}/{conn_id}/disable", headers=real_headers()).json()["status"]
        == "DISABLED"
    )
    assert (
        dd_client.post(f"{BASE}/{conn_id}/enable", headers=real_headers()).json()["status"]
        == "ACTIVE"
    )
    revoked = dd_client.delete(f"{BASE}/{conn_id}", headers=real_headers())
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["credential_fingerprint"] is None


@requires_real_data
@pytest.mark.usefixtures("vault_key")
def test_tenant_isolation(dd_client: TestClient) -> None:
    created = _create(dd_client)
    assert created.status_code == 201, created.text
    other = dd_client.get(BASE, headers=other_headers())
    assert other.status_code == 404
    assert (
        dd_client.post(f"{BASE}/{created.json()['id']}/test", headers=other_headers()).status_code
        == 404
    )


class TestAsOfReconciliation:
    """The sync adopts the snapshot's own reporting date over a wrong request
    (pure — no database)."""

    def _bundle(self, as_of_value: str) -> StagedBundle:
        return StagedBundle(
            backend="oracle",
            as_of_date="2026-06-30",
            source_database="CORE",
            extraction_mode="full",
            tables=[
                StagedTable(
                    name="CORE.IFTB_DEPOSIT",
                    record_kind="position",
                    dataset_kind=None,
                    columns=("SOURCE_REFERENCE", "AS_OF_DATE"),
                    rows=[{"SOURCE_REFERENCE": "D1", "AS_OF_DATE": as_of_value}],
                    extraction_mode="full",
                ),
            ],
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
