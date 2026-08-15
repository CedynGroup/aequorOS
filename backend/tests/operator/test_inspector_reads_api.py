"""Session-gated deep tenant reads (Tenant Inspector, step 1).

Every deep per-tenant read requires an ACTIVE inspector session the CALLER
opened, and writes an ``inspector.read.*`` row to ``operator_audit_log``. These
tests pin the gate (403 without / expired / ended / wrong-org / wrong-operator
session; break-glass grants), the audit trail, the payload shapes, and — the
sharpest check — that ``/config`` never leaks secret material.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import (
    BankReportingPeriod,
    IngestionBatch,
    IntegrationKey,
    LiveFinding,
    LiveMetric,
    OperatorAuditLog,
    OperatorInspectorSession,
    RegulatoryRun,
    SsoConnection,
    TranslationFailure,
    User,
)
from tests.operator.conftest import operator_headers, provision_payload, start_inspection
from tests.operator.test_operator_auth import PASSWORD, bearer, login, make_operator

BASE = "/operator/v1/tenants"

# (suffix, audit-action) for the FULL gated deep-read surface: the four
# pre-existing per-tenant reads that this wave moved behind the gate, plus the
# five new diagnostic reads.
GATED: list[tuple[str, str]] = [
    ("/users", "inspector.read.users"),
    ("/activity", "inspector.read.activity"),
    ("/entitlements", "inspector.read.entitlements"),
    ("/storage", "inspector.read.storage"),
    ("/metrics", "inspector.read.metrics"),
    ("/findings", "inspector.read.findings"),
    ("/ingestion", "inspector.read.ingestion"),
    ("/config", "inspector.read.config"),
]


def _provision(client: TestClient, **overrides: object) -> tuple[str, str]:
    body = client.post(
        BASE, json=provision_payload(**overrides), headers=operator_headers()
    ).json()
    assert body["succeeded"] is True, body
    return body["organization_id"], body["bank_id"]


def _seed_period(db: Session, organization_id: str, bank_id: str) -> UUID:
    period = BankReportingPeriod(
        organization_id=organization_id,
        bank_id=bank_id,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        label="2026-01",
        status="open",
    )
    db.add(period)
    db.commit()
    db.refresh(period)
    return period.id


# -- the gate --------------------------------------------------------------------
@pytest.mark.parametrize("suffix", [suffix for suffix, _ in GATED])
def test_deep_read_requires_active_session(operator_client: TestClient, suffix: str) -> None:
    organization_id, _bank = _provision(operator_client)
    response = operator_client.get(
        f"{BASE}/{organization_id}{suffix}", headers=operator_headers()
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["code"] == "inspection_required"


@pytest.mark.parametrize(("suffix", "action"), GATED)
def test_deep_read_ok_with_session_and_audits_once(
    operator_client: TestClient, operator_db: Session, suffix: str, action: str
) -> None:
    organization_id, _bank = _provision(operator_client)
    session_id = start_inspection(operator_client, organization_id)
    response = operator_client.get(
        f"{BASE}/{organization_id}{suffix}", headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    rows = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action == action)
        )
    )
    assert len(rows) == 1
    assert rows[0].target_org == organization_id
    assert rows[0].detail["session_id"] == session_id


def test_fleet_metadata_endpoints_stay_open(operator_client: TestClient) -> None:
    organization_id, _bank = _provision(operator_client)
    # No session — these two are the tenant directory, not a look inside a tenant.
    assert operator_client.get(BASE, headers=operator_headers()).status_code == 200
    assert (
        operator_client.get(f"{BASE}/{organization_id}", headers=operator_headers()).status_code
        == 200
    )


def test_expired_session_is_forbidden(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    session_id = start_inspection(operator_client, organization_id)
    row = operator_db.get(OperatorInspectorSession, UUID(session_id))
    assert row is not None
    row.expires_at = utc_now() - timedelta(minutes=1)
    operator_db.commit()
    response = operator_client.get(
        f"{BASE}/{organization_id}/metrics", headers=operator_headers()
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["code"] == "inspection_required"


def test_ended_session_is_forbidden(operator_client: TestClient) -> None:
    organization_id, _bank = _provision(operator_client)
    session_id = start_inspection(operator_client, organization_id)
    ended = operator_client.post(
        f"/operator/v1/inspector/sessions/{session_id}/end", headers=operator_headers()
    )
    assert ended.status_code == 200
    response = operator_client.get(
        f"{BASE}/{organization_id}/findings", headers=operator_headers()
    )
    assert response.status_code == 403


def test_session_does_not_unlock_a_different_org(operator_client: TestClient) -> None:
    org_a, _bank_a = _provision(operator_client)
    org_b, _bank_b = _provision(
        operator_client,
        organization_name="Second Holdings",
        bank_name="Second Bank",
        admin_email="admin@second.example",
    )
    start_inspection(operator_client, org_a)
    # A session for A grants A but NOT B.
    assert (
        operator_client.get(f"{BASE}/{org_a}/metrics", headers=operator_headers()).status_code
        == 200
    )
    assert (
        operator_client.get(f"{BASE}/{org_b}/metrics", headers=operator_headers()).status_code
        == 403
    )


def test_session_is_scoped_to_the_operator_who_opened_it(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)  # opened by dev@aequoros.com
    make_operator(operator_db, role="operator_admin")
    other = bearer(login(operator_client, "ama@aequoros.com", PASSWORD))
    # A different operator has no session of their OWN for this org → 403.
    assert (
        operator_client.get(f"{BASE}/{organization_id}/metrics", headers=other).status_code
        == 403
    )


def test_break_glass_session_grants_read(operator_client: TestClient) -> None:
    organization_id, _bank = _provision(operator_client)
    # The dev token is super_admin, so it may open the admin-gated break_glass mode.
    start_inspection(operator_client, organization_id, mode="break_glass")
    response = operator_client.get(
        f"{BASE}/{organization_id}/config", headers=operator_headers()
    )
    assert response.status_code == 200


# -- payloads --------------------------------------------------------------------
def test_metrics_returns_latest_live_metric_and_run(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    period_id = _seed_period(operator_db, organization_id, bank_id)
    operator_db.add(
        LiveMetric(
            organization_id=organization_id,
            bank_id=bank_id,
            reporting_period_id=period_id,
            module="liquidity",
            metrics={"lcr": 1.25},
            status="green",
            computed_from_input_hash="hash-abc",
            computed_at=utc_now(),
        )
    )
    operator_db.add(
        RegulatoryRun(
            organization_id=organization_id,
            bank_id=bank_id,
            reporting_period_id=period_id,
            module="capital",
            scenario_code="baseline",
            status="succeeded",
            engine_version="v1",
            input_schema_version="bank-facts-v2",
            output_schema_version="v1",
            input_hash="run-hash",
            inputs={},
            metrics={"car": 0.15},
            created_by=uuid4(),
        )
    )
    operator_db.commit()

    start_inspection(operator_client, organization_id)
    body = operator_client.get(
        f"{BASE}/{organization_id}/metrics", headers=operator_headers()
    ).json()
    assert body["organization_id"] == organization_id
    assert len(body["live_metrics"]) == 1
    metric = body["live_metrics"][0]
    assert metric["module"] == "liquidity"
    assert metric["status"] == "green"
    assert metric["metrics"] == {"lcr": 1.25}
    assert len(body["regulatory_runs"]) == 1
    run = body["regulatory_runs"][0]
    assert run["module"] == "capital"
    assert run["status"] == "succeeded"
    assert run["scenario_code"] == "baseline"


def test_findings_returns_active_alert_with_severity_and_module(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    period_id = _seed_period(operator_db, organization_id, bank_id)
    operator_db.add(
        LiveFinding(
            organization_id=organization_id,
            bank_id=bank_id,
            reporting_period_id=period_id,
            module="liquidity",
            rule_id="LCR_MIN",
            severity="high",
            status="open",
            message="LCR below internal floor",
            metric="lcr",
        )
    )
    operator_db.commit()

    start_inspection(operator_client, organization_id)
    body = operator_client.get(
        f"{BASE}/{organization_id}/findings", headers=operator_headers()
    ).json()
    assert body["open_count"] == 1
    finding = body["findings"][0]
    assert finding["severity"] == "high"
    assert finding["module"] == "liquidity"
    assert finding["status"] == "open"
    assert finding["rule_id"] == "LCR_MIN"


def test_ingestion_list_and_detail_expose_failure_diagnostics(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        adapter_version="excel_csv_v1.0",
        extraction_mode="full",
        status="failed",
        as_of_date=date(2026, 1, 31),
        records_extracted=10,
        records_error=3,
        error_code="translation_incomplete",
        error_message="3 rows failed to translate",
        validation_report={"errors": 3},
    )
    operator_db.add(batch)
    operator_db.commit()
    operator_db.refresh(batch)
    operator_db.add(
        TranslationFailure(
            organization_id=organization_id,
            bank_id=bank_id,
            ingestion_batch_id=batch.id,
            entity_type="position",
            source_locator="positions.xlsx#Sheet1!R14",
            raw_record={"amount": "not-a-number"},
            error_code="bad_number",
            error_message="amount is not numeric",
        )
    )
    operator_db.commit()

    start_inspection(operator_client, organization_id)
    listing = operator_client.get(
        f"{BASE}/{organization_id}/ingestion", headers=operator_headers()
    ).json()
    assert listing["organization_id"] == organization_id
    assert len(listing["batches"]) == 1
    row = listing["batches"][0]
    assert row["status"] == "failed"
    assert row["records_error"] == 3
    assert row["error_code"] == "translation_incomplete"

    batch_id = row["batch_id"]
    detail = operator_client.get(
        f"{BASE}/{organization_id}/ingestion/{batch_id}", headers=operator_headers()
    ).json()
    assert detail["validation_report"] == {"errors": 3}
    assert len(detail["translation_failures"]) == 1
    failure = detail["translation_failures"][0]
    assert failure["source_locator"].endswith("R14")
    assert failure["raw_record"] == {"amount": "not-a-number"}
    assert failure["error_code"] == "bad_number"


def test_ingestion_detail_unknown_batch_is_404(operator_client: TestClient) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.get(
        f"{BASE}/{organization_id}/ingestion/{uuid4()}", headers=operator_headers()
    )
    assert response.status_code == 404


def test_ingestion_detail_is_scoped_to_the_tenant(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_a, bank_a = _provision(operator_client)
    org_b, _bank_b = _provision(
        operator_client,
        organization_name="Second Holdings",
        bank_name="Second Bank",
        admin_email="admin@second.example",
    )
    batch = IngestionBatch(
        organization_id=org_a,
        bank_id=bank_a,
        source_system="EXCEL_CSV",
        adapter_version="excel_csv_v1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=date(2026, 1, 31),
    )
    operator_db.add(batch)
    operator_db.commit()
    operator_db.refresh(batch)

    start_inspection(operator_client, org_b)
    # The batch belongs to A; asking under B's org path (and B's session) → 404.
    response = operator_client.get(
        f"{BASE}/{org_b}/ingestion/{batch.id}", headers=operator_headers()
    )
    assert response.status_code == 404


def test_config_never_returns_secret_material(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)

    # Turn the disabled SSO stub into a configured connection WITH a sealed secret.
    sso = operator_db.scalar(
        select(SsoConnection).where(SsoConnection.organization_id == organization_id)
    )
    assert sso is not None
    sso.issuer = "https://accounts.google.com"
    sso.client_id = "client-abc.apps.googleusercontent.com"
    sso.client_secret_ciphertext = "SEALED-SSO-SECRET-CIPHERTEXT"
    sso.client_secret_fingerprint = "SSO-FINGERPRINT-XYZ"
    sso.allowed_email_domains = ["testbank.example"]
    sso.enabled = True

    # A service user + integration key (only the SHA-256 hash is ever stored).
    service_user = User(
        organization_id=organization_id,
        email="svc@testbank.example",
        display_name="middleware service account",
        role="analyst",
        auth_provider="service",
        is_active=True,
    )
    operator_db.add(service_user)
    operator_db.commit()
    operator_db.refresh(service_user)
    operator_db.add(
        IntegrationKey(
            organization_id=organization_id,
            service_user_id=service_user.id,
            label="core-middleware",
            key_prefix="aeq_live_ABCD",
            key_hash="SECRET-KEY-HASH-0123456789ABCDEF",
        )
    )
    operator_db.commit()

    start_inspection(operator_client, organization_id)
    response = operator_client.get(
        f"{BASE}/{organization_id}/config", headers=operator_headers()
    )
    assert response.status_code == 200
    text = response.text

    # Secret VALUES never appear.
    assert "SEALED-SSO-SECRET-CIPHERTEXT" not in text
    assert "SSO-FINGERPRINT-XYZ" not in text
    assert "SECRET-KEY-HASH-0123456789ABCDEF" not in text
    # Secret FIELD NAMES never appear.
    assert "client_secret_ciphertext" not in text
    assert "client_secret_fingerprint" not in text
    assert "key_hash" not in text

    body = response.json()
    # Non-secret diagnostics ARE present.
    assert body["sso"]["issuer"] == "https://accounts.google.com"
    assert body["sso"]["client_id"] == "client-abc.apps.googleusercontent.com"
    assert body["sso"]["secret_configured"] is True
    assert body["sso"]["allowed_email_domains"] == ["testbank.example"]
    key = body["integration_keys"][0]
    assert key["key_prefix"] == "aeq_live_ABCD"
    assert key["status"] == "active"
    assert body["banks"][0]["jurisdiction_code"] == "GH"
    assert body["banks"][0]["currency"] == "GHS"
