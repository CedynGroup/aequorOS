"""The cross-tenant Operations job board and claimant attribution."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Job
from tests.operator.conftest import operator_headers

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