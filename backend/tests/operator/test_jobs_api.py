"""The cross-tenant Operations job board and claimant attribution."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import IngestionBatch, Job
from tests.operator.conftest import operator_headers, provision_payload

BASE = "/operator/v1/jobs"


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(BASE).status_code == 401


def test_lists_jobs_with_claimant_and_status_filter(
    operator_client: TestClient, operator_db: Session
) -> None:
    first = Job(
        organization_id="OR-TEST0001",
        bank_id="BK-TEST0001",
        job_type="pipeline_refresh",
        status="running",
        claimed_by="risk-worker:blue-20260815",
    )
    second = Job(
        organization_id="OR-TEST0002",
        bank_id="BK-TEST0002",
        job_type="official_run",
        status="failed",
        claimed_by="risk-worker:green-20260814",
        error="handler failed",
        attempts=2,
    )
    operator_db.add_all([first, second])
    operator_db.commit()

    response = operator_client.get(BASE, headers=operator_headers())
    assert response.status_code == 200, response.text
    jobs = {job["id"]: job for job in response.json()["jobs"]}
    assert jobs[str(first.id)]["claimed_by"] == "risk-worker:blue-20260815"
    assert jobs[str(second.id)]["organization_id"] == "OR-TEST0002"
    assert jobs[str(second.id)]["error"] == "handler failed"

    filtered = operator_client.get(f"{BASE}?status=running", headers=operator_headers())
    assert filtered.status_code == 200, filtered.text
    assert [job["id"] for job in filtered.json()["jobs"]] == [str(first.id)]


# --------------------------------------------------------------------------
# The stranded-work board
# --------------------------------------------------------------------------


def _batch_with_dedup_job(
    db: Session,
    tenant: tuple[str, str],
    *,
    dedup_status: str,
    job_status: str,
    attempts: int,
) -> IngestionBatch:
    """``tenant`` is ``(organization_id, bank_id)`` — the batch's owner."""
    organization_id, bank_id = tenant
    batch = IngestionBatch(
        organization_id=organization_id,
        bank_id=bank_id,
        source_system="DB_DIRECT",
        adapter_version="db_direct_v1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=date(2026, 4, 30),
        records_extracted=167443,
        records_accepted=167443,
        etl_report={"dedup_status": dedup_status},
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    db.add(
        Job(
            organization_id=organization_id,
            bank_id=bank_id,
            job_type="etl_dedup",
            status=job_status,
            entity_type="ingestion_batch",
            entity_id=str(batch.id),
            payload={"batch_id": str(batch.id)},
            attempts=attempts,
            max_attempts=3,
            error="server closed the connection unexpectedly",
            completed_at=utc_now() if job_status == "failed" else None,
        )
    )
    db.commit()
    return batch


def test_stuck_dedup_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(f"{BASE}/stuck-dedup").status_code == 401


def test_stuck_dedup_lists_only_batches_the_queue_will_never_retry(
    operator_client: TestClient, operator_db: Session
) -> None:
    """Stranded is a precise state, not a guess.

    A ``deferred`` batch whose job still has attempts left is working as
    designed; one whose job is terminal with every attempt used is stranded for
    ever, because nothing re-enqueues it. Four batches on the primary sat in the
    second state for weeks looking exactly like the first.
    """
    provisioned = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    ).json()
    assert provisioned["succeeded"] is True, provisioned
    org_id = provisioned["organization_id"]
    bank_id = provisioned["bank_id"]

    tenant = (org_id, bank_id)
    stranded = _batch_with_dedup_job(
        operator_db,
        tenant,
        dedup_status="deferred",
        job_status="failed",
        attempts=3,
    )
    will_retry = _batch_with_dedup_job(
        operator_db,
        tenant,
        dedup_status="deferred",
        job_status="queued",
        attempts=1,
    )
    done = _batch_with_dedup_job(
        operator_db,
        tenant,
        dedup_status="completed",
        job_status="succeeded",
        attempts=0,
    )

    response = operator_client.get(f"{BASE}/stuck-dedup", headers=operator_headers())
    assert response.status_code == 200, response.text
    listed = {row["batch_id"]: row for row in response.json()["batches"]}
    assert str(stranded.id) in listed
    assert str(will_retry.id) not in listed
    assert str(done.id) not in listed

    row = listed[str(stranded.id)]
    assert row["organization_id"] == org_id
    assert row["dedup_status"] == "deferred"
    assert row["job_attempts"] == row["job_max_attempts"] == 3
    # The reason it stranded is on the board, so an operator diagnoses before
    # re-driving: the four on the primary failed for three different reasons.
    assert "server closed the connection" in row["job_error"]
