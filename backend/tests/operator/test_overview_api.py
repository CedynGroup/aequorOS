"""GET /operator/v1/overview — the console-home fleet rollup."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DeskDetermination,
    DeskOperatingEnvironmentAssessment,
    IngestionBatch,
    Job,
    MarketDataConnection,
    OperatorAuditLog,
)
from tests.operator.conftest import operator_headers, provision_payload

OVERVIEW = "/operator/v1/overview"


def _provision(client: TestClient) -> tuple[str, str]:
    body = client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    ).json()
    return body["organization_id"], body["bank_id"]


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(OVERVIEW).status_code == 401


def test_empty_fleet_is_all_zero(operator_client: TestClient) -> None:
    body = operator_client.get(OVERVIEW, headers=operator_headers()).json()
    assert body["tenants"] == {
        "total": 0,
        "banks_live": 0,
        "banks_empty": 0,
        "stale_count": 0,
    }
    assert body["ingestion"] == {"failed_24h": 0}
    assert body["jobs"] == {"failed_24h": 0, "running": 0}
    assert body["connections"] == {"ok": 0, "warn": 0, "crit": 0}
    assert body["desk"] == {
        "pending_determinations": 0,
        "pending_curve_determinations": 0,
        "pending_oe_assessments": 0,
    }
    assert body["needs_attention"] == []


def test_rollup_counts_and_needs_attention(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)

    # A failed ingestion batch and a failed + running job (all within 24h).
    operator_db.add(
        IngestionBatch(
            organization_id=organization_id,
            bank_id=bank_id,
            source_system="EXCEL_CSV",
            adapter_version="test-1",
            extraction_mode="full",
            status="failed",
            as_of_date=date(2026, 7, 31),
        )
    )
    operator_db.add(
        Job(
            organization_id=organization_id,
            bank_id=bank_id,
            job_type="pipeline_refresh",
            status="failed",
        )
    )
    operator_db.add(
        Job(
            organization_id=organization_id,
            bank_id=bank_id,
            job_type="pipeline_refresh",
            status="running",
        )
    )
    # A market-data connection defaults to TESTING → warn bucket.
    operator_db.add(
        MarketDataConnection(
            organization_id=organization_id,
            bank_id=bank_id,
            vendor="refinitiv",
            display_name="LSEG (formerly Refinitiv)",
            vault_path=f"vault://institutions/{bank_id}/vendor_credentials/refinitiv/default",
            credential_ciphertext="SEALED",
            credential_fingerprint="fp",
        )
    )
    # Two pending desk determinations: one rates (has 'rates'), one curve (no 'rates').
    operator_db.add(
        DeskDetermination(
            cob_date=date(2026, 8, 7),
            methodology_code="AEQ-GHS-CURVES",
            methodology_version=1,
            input_snapshot=[],
            input_digest="d" * 64,
            derived_values={"rates": {"base": "0.25"}, "rates_qa_passed": True},
            status="pending_review",
            prepared_by="analyst@aequoros.com",
        )
    )
    operator_db.add(
        DeskDetermination(
            cob_date=date(2026, 8, 7),
            methodology_code="AEQ-GHS-CURVES",
            methodology_version=1,
            input_snapshot=[],
            input_digest="c" * 64,
            derived_values={"curves": {"AEQ.GHS.SOV.FWD": {}}, "forward_grids": {}},
            status="pending_review",
            prepared_by="analyst@aequoros.com",
        )
    )
    # A pending operating-environment assessment.
    operator_db.add(
        DeskOperatingEnvironmentAssessment(
            jurisdiction_code="GH",
            cob_date=date(2026, 8, 7),
            methodology_version="OE-v1",
            input_snapshot={},
            input_digest="e" * 64,
            computed_breakdown={},
            score=Decimal("0.5"),
            status="pending_review",
            proposed_by="analyst@aequoros.com",
        )
    )
    operator_db.commit()

    body = operator_client.get(OVERVIEW, headers=operator_headers()).json()

    assert body["tenants"]["total"] == 1
    # Provisioned tenant has a bank but no reporting periods yet.
    assert body["tenants"]["banks_live"] == 0
    assert body["tenants"]["banks_empty"] == 1
    assert body["ingestion"]["failed_24h"] == 1
    assert body["jobs"] == {"failed_24h": 1, "running": 1}
    assert body["connections"] == {"ok": 0, "warn": 1, "crit": 0}
    assert body["desk"] == {
        "pending_determinations": 1,
        "pending_curve_determinations": 1,
        "pending_oe_assessments": 1,
    }

    # needs_attention: jobs + ingestion are crit; connections(warn) + desk are warn.
    kinds = {(i["kind"], i["severity"]) for i in body["needs_attention"]}
    assert ("jobs", "crit") in kinds
    assert ("ingestion", "crit") in kinds
    assert ("connections", "warn") in kinds
    assert ("desk", "warn") in kinds
    # crit items sort ahead of warn items.
    severities = [i["severity"] for i in body["needs_attention"]]
    assert severities == sorted(severities, key=lambda s: 0 if s == "crit" else 1)


def test_overview_read_is_not_audited(
    operator_client: TestClient, operator_db: Session
) -> None:
    _provision(operator_client)
    operator_client.get(OVERVIEW, headers=operator_headers())
    after = list(
        operator_db.scalars(
            select(OperatorAuditLog).where(OperatorAuditLog.action.like("overview%"))
        )
    )
    assert after == []
