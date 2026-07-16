"""Live-engine API: automatic trigger, live summary, freshness, alerts, enqueues."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import Job
from app.services import job_queue, pipeline
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.adapters.excel_csv import fixtures
from tests.api.helpers import ORG_1, ORG_2, headers
from tests.api.test_ingestion import FULL_MAPPING, activate_mapping, seed_bank, start_batch
from tests.factories.canonical import (
    FIXTURE_AS_OF,
    seed_canonical_fixture,
    seed_hedge_and_swap_positions,
)

AS_OF = FIXTURE_AS_OF.isoformat()
_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


def _seed_and_refresh(db_client: TestClient, *, hedged: bool = False) -> None:
    """Seed the sample bank + canonical fixture and run one live refresh.

    Uses a direct session on the same engine as the TestClient so the API reads
    see the committed live rows the worker would have produced.
    """
    _ = db_client  # ensures the app engine/DB is initialized
    session = get_sessionmaker()()
    try:
        seed_sample_bank(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        if hedged:
            seed_hedge_and_swap_positions(
                session,
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
            )
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": AS_OF},
        )
        session.commit()
        pipeline.run_refresh(session, job)
    finally:
        session.close()


def test_accepted_ingestion_enqueues_pipeline_refresh(
    db_client: TestClient, tmp_path: Path
) -> None:
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, FULL_MAPPING)
    workbook = fixtures.build_well_formed(tmp_path / "bank.xlsx")
    started = start_batch(db_client, bank_id, workbook)
    assert started["batch"]["status"] == "accepted"

    session = get_sessionmaker()()
    try:
        jobs = list(
            session.scalars(
                select(Job).where(Job.job_type == "pipeline_refresh", Job.bank_id == UUID(bank_id))
            )
        )
    finally:
        session.close()
    assert len(jobs) == 1
    assert jobs[0].coalesce_key.startswith(f"refresh:{bank_id}:")
    assert jobs[0].run_after is not None  # debounced, not immediate


def test_get_live_summary_shape(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/live-summary", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(SAMPLE_BANK_ID)
    assert body["reporting_period_id"] is not None
    modules = {module["module"] for module in body["modules"]}
    assert {"liquidity", "capital", "irr", "fx", "ftp"} <= modules
    assert body["is_stale"] is True  # live view exists but no official run yet


def test_get_freshness_shape(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/freshness", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_stale"] is True
    liquidity = next(module for module in body["modules"] if module["module"] == "liquidity")
    assert liquidity["live_hash"] is not None
    assert liquidity["official_run_hash"] is None


def test_get_alerts_surfaces_fx_breach(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/alerts", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    fx_breaches = [item for item in body["items"] if item["module"] == "fx"]
    assert any(item["rule_id"] == "nop_within_aggregate_limit" for item in fx_breaches)
    assert all(item["severity"] in ("critical", "high") for item in body["items"])


def test_fx_alert_clears_after_hedges(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    before = db_client.get(f"{_BASE}/alerts", headers=headers()).json()
    assert any(item["rule_id"] == "nop_within_aggregate_limit" for item in before["items"])

    # Add the hedge book and refresh again through the pipeline.
    session = get_sessionmaker()()
    try:
        seed_hedge_and_swap_positions(
            session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
        )
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": AS_OF},
        )
        session.commit()
        pipeline.run_refresh(session, job)
    finally:
        session.close()

    after = db_client.get(f"{_BASE}/alerts", headers=headers()).json()
    assert not any(item["rule_id"] == "nop_within_aggregate_limit" for item in after["items"])


def test_refresh_endpoint_enqueues_and_is_pollable(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.post(
        f"{_BASE}/refresh", headers=headers(), json={"as_of_date": AS_OF, "reason": "recompute"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_type"] == "pipeline_refresh"
    assert body["status"] == "queued"

    polled = db_client.get(f"/api/v1/jobs/{body['job_id']}", headers=headers())
    assert polled.status_code == 200
    assert polled.json()["job_type"] == "pipeline_refresh"


def test_mint_official_run_enqueues(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    response = db_client.post(
        f"{_BASE}/official-runs", headers=headers(), json={"as_of_date": AS_OF, "reason": "filing"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_type"] == "official_run"

    session = get_sessionmaker()()
    try:
        official = list(
            session.scalars(
                select(Job).where(Job.job_type == "official_run", Job.bank_id == SAMPLE_BANK_ID)
            )
        )
    finally:
        session.close()
    assert len(official) == 1


def test_tenant_isolation_org2_gets_404(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    org2 = headers(org_id=ORG_2)
    for path in ("live-summary", "freshness", "alerts"):
        response = db_client.get(f"{_BASE}/{path}", headers=org2)
        assert response.status_code == 404, f"{path}: {response.status_code}"
    for path in ("refresh", "official-runs"):
        response = db_client.post(
            f"{_BASE}/{path}", headers=org2, json={"as_of_date": AS_OF, "reason": "x"}
        )
        assert response.status_code == 404, f"{path}: {response.status_code}"
