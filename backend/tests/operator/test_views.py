"""Cross-tenant read views: tenants board, activity feed, data-engine wall."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    IngestionBatch,
    Job,
    MarketDataConnection,
    OperatorAuditLog,
)
from tests.operator.conftest import operator_headers, provision_payload


def _provision(operator_client: TestClient) -> tuple[str, str]:
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = response.json()
    assert body["succeeded"] is True, body
    return body["organization_id"], body["bank_id"]


def test_tenants_list_shows_the_provisioned_tenant(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)

    response = operator_client.get("/operator/v1/tenants", headers=operator_headers())
    assert response.status_code == 200
    tenants = response.json()["tenants"]
    assert len(tenants) == 1
    row = tenants[0]
    assert row["organization_id"] == organization_id
    assert row["organization_name"] == "Test Bancorp Holdings"
    assert row["bank_id"] == bank_id
    assert row["jurisdiction_code"] == "GH"
    assert row["currency"] == "GHS"
    assert row["license_type"] == "universal_bank"
    assert row["period_count"] == 0
    assert row["latest_period_end"] is None
    assert row["freshness"] is None  # no periods yet — nothing to be fresh about
    assert row["last_ingestion"] is None
    assert row["sso_configured"] is True
    assert row["sso_enabled"] is False
    assert row["storage_provider"] == "minio"

    # This cross-tenant enumeration is itself audited, once per call.
    audit_rows = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "tenants.list")
        )
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].detail == {"tenant_rows": 1}


def test_activity_feed_merges_sources_newest_first(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    operator_db.add(
        IngestionBatch(
            organization_id=organization_id,
            bank_id=bank_id,
            source_system="EXCEL_CSV",
            adapter_version="test-1",
            extraction_mode="full",
            status="accepted",
            as_of_date=date(2026, 7, 31),
            records_accepted=42,
        )
    )
    operator_db.add(
        Job(
            organization_id=organization_id,
            bank_id=bank_id,
            job_type="pipeline_refresh",
            status="succeeded",
        )
    )
    operator_db.add(
        AuditEvent(
            organization_id=organization_id,
            event_type="ingestion.completed",
            entity_type="bank",
            entity_id=bank_id,
        )
    )
    operator_db.commit()

    response = operator_client.get(
        f"/operator/v1/tenants/{organization_id}/activity", headers=operator_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == organization_id
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"ingestion_batch", "job", "audit_event"}
    timestamps = [item["ts"] for item in body["items"]]
    assert timestamps == sorted(timestamps, reverse=True)
    batch_item = next(item for item in body["items"] if item["kind"] == "ingestion_batch")
    assert "42 accepted" in batch_item["summary"]
    assert batch_item["status"] == "accepted"


def test_activity_respects_limit(operator_client: TestClient, operator_db: Session) -> None:
    organization_id, _bank_id = _provision(operator_client)
    for index in range(5):
        operator_db.add(
            AuditEvent(organization_id=organization_id, event_type=f"event.{index}")
        )
    operator_db.commit()

    response = operator_client.get(
        f"/operator/v1/tenants/{organization_id}/activity?limit=3",
        headers=operator_headers(),
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3


def test_activity_unknown_org_is_404(operator_client: TestClient) -> None:
    response = operator_client.get(
        "/operator/v1/tenants/OR-00000000/activity", headers=operator_headers()
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_data_engines_exposes_metadata_and_never_credentials(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    operator_db.add(
        MarketDataConnection(
            organization_id=organization_id,
            bank_id=bank_id,
            vendor="refinitiv",
            display_name="LSEG (formerly Refinitiv)",
            vault_path=f"vault://institutions/{bank_id}/vendor_credentials/refinitiv/default",
            credential_ciphertext="SEALED-CREDENTIAL-BLOB",
            credential_fingerprint="abcd1234",
        )
    )
    operator_db.commit()

    response = operator_client.get("/operator/v1/data-engines", headers=operator_headers())
    assert response.status_code == 200
    connections = response.json()["connections"]
    assert len(connections) == 1
    row = connections[0]
    assert row["organization_id"] == organization_id
    assert row["bank_id"] == bank_id
    assert row["engine"] == "market_data"
    assert row["system"] == "refinitiv"
    assert row["status"] == "TESTING"
    assert row["last_activity_at"] is None

    # The leak canary: no credential material, locator, or fingerprint —
    # in values OR in field names.
    assert "SEALED-CREDENTIAL-BLOB" not in response.text
    assert "abcd1234" not in response.text
    assert "vault://" not in response.text
    forbidden_fields = {"credential_ciphertext", "credential_fingerprint", "vault_path"}
    assert forbidden_fields.isdisjoint(row.keys())


def test_data_engines_empty_when_no_connections(operator_client: TestClient) -> None:
    _provision(operator_client)
    response = operator_client.get("/operator/v1/data-engines", headers=operator_headers())
    assert response.status_code == 200
    assert response.json() == {"connections": []}
