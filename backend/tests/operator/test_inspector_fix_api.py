"""Session-gated deep tenant FIXES (Tenant Inspector, the write side).

Every fix requires an ACTIVE inspection session the caller opened (403
``inspection_required`` otherwise), performs its write on the operator's
cross-tenant BYPASSRLS session — never a tenant impersonation token — and writes
exactly one ``inspector.fix.*`` row to ``operator_audit_log``. These tests pin
the gate (consent AND break-glass grant), the audit trail with before/after, job
enqueuing, org-scoping, and the two scoped config changes (mapping toggle +
effective-dated threshold supersession).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import (
    BankReportingPeriod,
    IngestionBatch,
    Job,
    MappingConfigRecord,
    OperatorAuditLog,
    ParamCapitalThreshold,
    ParamLiquidityThreshold,
)
from tests.operator.conftest import operator_headers, provision_payload, start_inspection

BASE = "/operator/v1/tenants"


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


def _seed_mapping(db: Session, organization_id: str, bank_id: str, *, status: str = "active") -> UUID:
    mapping = MappingConfigRecord(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        source_ref="",
        version=1,
        status=status,
        name="excel positions v1",
        config={"field_mappings": {}},
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping.id


def _seed_liquidity_threshold(db: Session, organization_id: str) -> UUID:
    row = ParamLiquidityThreshold(
        organization_id=organization_id,
        jurisdiction_code="GH",
        institution_class="bank",
        threshold_code="LCR_MIN",
        threshold_pct=Decimal("100.000000"),
        effective_from=date(2026, 1, 1),
        effective_to=None,
        approved_by="board@testbank.example",
        approval_timestamp=utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _seed_capital_threshold(db: Session, organization_id: str) -> UUID:
    row = ParamCapitalThreshold(
        organization_id=organization_id,
        jurisdiction_code="GH",
        threshold_code="CAR_MIN",
        value_pct=Decimal("13.000000"),
        effective_from=date(2026, 1, 1),
        effective_to=None,
        approved_by="board@testbank.example",
        approval_timestamp=utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _audit_rows(db: Session, action: str) -> list[OperatorAuditLog]:
    return list(db.scalars(select(OperatorAuditLog).where(OperatorAuditLog.action == action)))


# -- the gate --------------------------------------------------------------------
# (path suffix, JSON body) for each fix; bodies are valid so the ONLY failure is
# the missing session.
_FIXES: list[tuple[str, dict[str, object]]] = [
    ("/fix/recompute", {"note": "recompute please"}),
    ("/fix/official-run", {"note": "mint filing"}),
    ("/fix/rerun-ingestion", {"batch_id": str(uuid4()), "note": "reprocess"}),
    (
        "/fix/config",
        {"kind": "mapping_active", "target_id": str(uuid4()), "value": False, "note": "toggle"},
    ),
]


@pytest.mark.parametrize(("suffix", "body"), _FIXES)
def test_fix_requires_active_session(
    operator_client: TestClient, suffix: str, body: dict[str, object]
) -> None:
    organization_id, _bank = _provision(operator_client)
    response = operator_client.post(
        f"{BASE}/{organization_id}{suffix}", json=body, headers=operator_headers()
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["code"] == "inspection_required"


@pytest.mark.parametrize("suffix", [suffix for suffix, _ in _FIXES])
def test_fix_note_is_required(operator_client: TestClient, suffix: str) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    # Empty note is rejected at the schema (min_length=1) → 422, before any work.
    body: dict[str, object] = {"note": ""}
    if suffix == "/fix/rerun-ingestion":
        body["batch_id"] = str(uuid4())
    if suffix == "/fix/config":
        body.update({"kind": "mapping_active", "target_id": str(uuid4()), "value": False})
    response = operator_client.post(
        f"{BASE}/{organization_id}{suffix}", json=body, headers=operator_headers()
    )
    assert response.status_code == 422, response.text


# -- recompute / official run ----------------------------------------------------
def test_recompute_enqueues_job_and_audits_once(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    _seed_period(operator_db, organization_id, bank_id)
    session_id = start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/recompute",
        json={"note": "LCR looks stale"},
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_type"] == "pipeline_refresh"
    assert body["status"] == "queued"

    job = operator_db.scalar(
        select(Job).where(
            Job.organization_id == organization_id, Job.job_type == "pipeline_refresh"
        )
    )
    assert job is not None
    assert job.bank_id == bank_id
    assert str(job.id) == body["job_id"]
    assert job.payload["reason"] == "LCR looks stale"

    rows = _audit_rows(operator_db, "inspector.fix.recompute")
    assert len(rows) == 1
    assert rows[0].target_org == organization_id
    assert rows[0].detail["session_id"] == session_id
    assert rows[0].detail["note"] == "LCR looks stale"
    assert rows[0].detail["job_id"] == body["job_id"]


def test_recompute_without_period_is_409(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/recompute",
        json={"note": "no period yet"},
        headers=operator_headers(),
    )
    assert response.status_code == 409, response.text
    # A failed action writes NO audit row.
    assert _audit_rows(operator_db, "inspector.fix.recompute") == []


def test_official_run_enqueues_job_and_audits_once(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    _seed_period(operator_db, organization_id, bank_id)
    start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/official-run",
        json={"note": "quarter-end filing", "as_of_date": "2026-01-31"},
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_type"] == "official_run"

    job = operator_db.scalar(
        select(Job).where(
            Job.organization_id == organization_id, Job.job_type == "official_run"
        )
    )
    assert job is not None
    assert job.bank_id == bank_id
    assert job.payload["as_of_date"] == "2026-01-31"

    rows = _audit_rows(operator_db, "inspector.fix.official_run")
    assert len(rows) == 1
    assert rows[0].detail["as_of_date"] == "2026-01-31"


@pytest.mark.parametrize("mode", ["consent", "break_glass"])
def test_both_session_modes_permit_a_fix(
    operator_client: TestClient, operator_db: Session, mode: str
) -> None:
    organization_id, bank_id = _provision(operator_client)
    _seed_period(operator_db, organization_id, bank_id)
    start_inspection(operator_client, organization_id, mode=mode)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/recompute",
        json={"note": f"{mode} remediation"},
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text


# -- re-run ingestion ------------------------------------------------------------
def test_rerun_ingestion_enqueues_refresh_and_audits(
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
    )
    operator_db.add(batch)
    operator_db.commit()
    operator_db.refresh(batch)

    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/rerun-ingestion",
        json={"batch_id": str(batch.id), "note": "re-process the failed upload"},
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_type"] == "pipeline_refresh"
    assert body["batch_id"] == str(batch.id)
    assert "re-upload" in body["detail"]

    job = operator_db.scalar(
        select(Job).where(
            Job.organization_id == organization_id, Job.job_type == "pipeline_refresh"
        )
    )
    assert job is not None
    assert job.payload["rerun_of_batch_id"] == str(batch.id)

    rows = _audit_rows(operator_db, "inspector.fix.rerun_ingestion")
    assert len(rows) == 1
    assert rows[0].detail["batch_id"] == str(batch.id)


def test_rerun_ingestion_foreign_batch_is_404(
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
    response = operator_client.post(
        f"{BASE}/{org_b}/fix/rerun-ingestion",
        json={"batch_id": str(batch.id), "note": "wrong org"},
        headers=operator_headers(),
    )
    assert response.status_code == 404, response.text
    assert _audit_rows(operator_db, "inspector.fix.rerun_ingestion") == []


# -- scoped config: mapping active toggle ----------------------------------------
def test_config_mapping_active_toggles_and_audits_before_after(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    mapping_id = _seed_mapping(operator_db, organization_id, bank_id, status="active")
    start_inspection(operator_client, organization_id)

    # Deactivate.
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "mapping_active",
            "target_id": str(mapping_id),
            "value": False,
            "note": "bad mapping, disabling",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "mapping_active"
    assert body["before"]["status"] == "active"
    assert body["after"]["status"] == "retired"

    operator_db.expire_all()
    mapping = operator_db.get(MappingConfigRecord, mapping_id)
    assert mapping is not None
    assert mapping.status == "retired"

    rows = _audit_rows(operator_db, "inspector.fix.config")
    assert len(rows) == 1
    assert rows[0].detail["before"]["status"] == "active"
    assert rows[0].detail["after"]["status"] == "retired"

    # Reactivate (no sibling conflict) — reversible.
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "mapping_active",
            "target_id": str(mapping_id),
            "value": True,
            "note": "re-enabling",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["after"]["status"] == "active"


def test_config_mapping_active_rejects_non_boolean(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, bank_id = _provision(operator_client)
    mapping_id = _seed_mapping(operator_db, organization_id, bank_id)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "mapping_active",
            "target_id": str(mapping_id),
            "value": 12.5,
            "note": "wrong type",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 422, response.text


def test_config_foreign_mapping_is_404(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_a, bank_a = _provision(operator_client)
    org_b, _bank_b = _provision(
        operator_client,
        organization_name="Second Holdings",
        bank_name="Second Bank",
        admin_email="admin@second.example",
    )
    mapping_id = _seed_mapping(operator_db, org_a, bank_a)
    start_inspection(operator_client, org_b)
    response = operator_client.post(
        f"{BASE}/{org_b}/fix/config",
        json={
            "kind": "mapping_active",
            "target_id": str(mapping_id),
            "value": False,
            "note": "cross-org",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 404, response.text


# -- scoped config: threshold supersession ---------------------------------------
def test_config_threshold_value_creates_new_effective_row_and_audits(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    threshold_id = _seed_liquidity_threshold(operator_db, organization_id)
    start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "threshold_value",
            "target_id": str(threshold_id),
            "value": 110.0,
            "note": "raising the internal LCR floor",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "threshold_value"
    assert body["before"]["value_pct"] == 100.0
    assert body["after"]["value_pct"] == 110.0
    new_row_id = UUID(body["target_id"])
    assert new_row_id != threshold_id

    operator_db.expire_all()
    # The prior generation is now closed…
    prior = operator_db.get(ParamLiquidityThreshold, threshold_id)
    assert prior is not None
    assert prior.effective_to is not None
    # …and a NEW open-ended generation carries the new value + operator approval.
    current = operator_db.scalar(
        select(ParamLiquidityThreshold).where(
            ParamLiquidityThreshold.organization_id == organization_id,
            ParamLiquidityThreshold.threshold_code == "LCR_MIN",
            ParamLiquidityThreshold.effective_to.is_(None),
        )
    )
    assert current is not None
    assert current.id == new_row_id
    assert current.threshold_pct == Decimal("110")
    assert current.approved_by == "dev@aequoros.com"

    rows = _audit_rows(operator_db, "inspector.fix.config")
    assert len(rows) == 1
    assert rows[0].detail["before"]["value_pct"] == 100.0
    assert rows[0].detail["after"]["value_pct"] == 110.0


def test_config_threshold_value_supports_capital_register(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    threshold_id = _seed_capital_threshold(operator_db, organization_id)
    start_inspection(operator_client, organization_id)

    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "threshold_value",
            "target_id": str(threshold_id),
            "value": 14.5,
            "note": "capital floor bump",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["after"]["register"] == "capital"

    operator_db.expire_all()
    current = operator_db.scalar(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.organization_id == organization_id,
            ParamCapitalThreshold.effective_to.is_(None),
        )
    )
    assert current is not None
    assert current.value_pct == Decimal("14.5")


def test_config_threshold_value_rejects_boolean(
    operator_client: TestClient, operator_db: Session
) -> None:
    organization_id, _bank = _provision(operator_client)
    threshold_id = _seed_liquidity_threshold(operator_db, organization_id)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "threshold_value",
            "target_id": str(threshold_id),
            "value": True,
            "note": "wrong type",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 422, response.text


def test_config_threshold_unknown_target_is_404(operator_client: TestClient) -> None:
    organization_id, _bank = _provision(operator_client)
    start_inspection(operator_client, organization_id)
    response = operator_client.post(
        f"{BASE}/{organization_id}/fix/config",
        json={
            "kind": "threshold_value",
            "target_id": str(uuid4()),
            "value": 99.0,
            "note": "no such row",
        },
        headers=operator_headers(),
    )
    assert response.status_code == 404, response.text


# -- org scoping -----------------------------------------------------------------
def test_session_does_not_unlock_a_different_org(
    operator_client: TestClient, operator_db: Session
) -> None:
    org_a, bank_a = _provision(operator_client)
    org_b, bank_b = _provision(
        operator_client,
        organization_name="Second Holdings",
        bank_name="Second Bank",
        admin_email="admin@second.example",
    )
    _seed_period(operator_db, org_b, bank_b)
    start_inspection(operator_client, org_a)
    # A session for A must NOT authorize a fix on B.
    response = operator_client.post(
        f"{BASE}/{org_b}/fix/recompute",
        json={"note": "wrong org"},
        headers=operator_headers(),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["code"] == "inspection_required"
