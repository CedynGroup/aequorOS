"""Examiner mode v1 (Phase 2 item 7).

The examiner role reads everything and mutates nothing: it clears the
read-only tenant guard (it ranks above viewer in the ladder) while every
mutation gate (analyst and above) excludes it. Run reproduction recomputes
the canonical snapshot hash live; the documentation package bundles the
period's evidence with provenance.
"""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.security import ROLES, has_role
from app.db.session import get_sessionmaker
from app.models import RegulatoryRun
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers


def _seed_with_liquidity_run(db_client: TestClient) -> tuple[str, str]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    period_id = periods[0]["id"]
    run = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": "baseline",
        },
    )
    assert run.status_code == 201, run.text
    return period_id, run.json()["id"]


def test_examiner_ladder_reads_but_never_mutates() -> None:
    assert ROLES == ("admin", "approver", "analyst", "examiner", "viewer")
    assert has_role(["examiner"], "viewer") is True
    assert has_role(["examiner"], "examiner") is True
    assert has_role(["examiner"], "analyst") is False
    assert has_role(["viewer"], "examiner") is False


def test_examiner_surfaces_and_run_reproduction(db_client: TestClient) -> None:
    period_id, run_id = _seed_with_liquidity_run(db_client)
    examiner = headers(roles=("examiner",))

    runs = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/examiner/runs",
        headers=examiner,
        params={"reporting_period_id": period_id},
    )
    assert runs.status_code == 200, runs.text
    listed = runs.json()["runs"]
    assert any(entry["run_id"] == run_id for entry in listed)

    # Reproduction: the stored snapshot re-hashes to the stored input_hash.
    reproduction = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/examiner/runs/{run_id}/reproduction",
        headers=examiner,
    )
    assert reproduction.status_code == 200, reproduction.text
    body = reproduction.json()
    assert body["reproducible"] is True
    assert body["stored_input_hash"] == body["recomputed_input_hash"]
    assert body["fact_count"] > 0

    # A tampered snapshot is caught: the recomputed hash diverges.
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run_row = session.get(RegulatoryRun, UUID(run_id))
        assert run_row is not None
        tampered = dict(run_row.inputs)
        tampered["as_of_date"] = "2031-12-31"
        session.execute(
            update(RegulatoryRun)
            .where(RegulatoryRun.id == UUID(run_id))
            .values(inputs=tampered)
        )
        session.commit()
    finally:
        session.close()
    tampered_check = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/examiner/runs/{run_id}/reproduction",
        headers=examiner,
    ).json()
    assert tampered_check["reproducible"] is False

    # The examiner cannot mutate: analyst-gated endpoints refuse the role.
    blocked = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=examiner,
        json={
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": "combined",
        },
    )
    assert blocked.status_code == 403


def test_documentation_package_bundles_period_evidence(db_client: TestClient) -> None:
    period_id, _run_id = _seed_with_liquidity_run(db_client)
    package = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/examiner/documentation-package",
        headers=headers(roles=("examiner",)),
        params={"reporting_period_id": period_id},
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["bank_id"] == SAMPLE_BANK_ID
    assert any(entry["module"] == "liquidity" for entry in body["latest_runs"])
    assert body["audit_event_count"] > 0
    assert any(path.endswith("/liquidity-thresholds") for path in body["register_endpoints"])
    assert body["cfp_approved_version"] is None

    foreign = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/examiner/documentation-package",
        headers=headers(ORG_2, roles=("examiner",)),
        params={"reporting_period_id": period_id},
    )
    assert foreign.status_code == 404
