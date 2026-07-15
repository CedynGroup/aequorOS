"""Data activation endpoint: derive facts, recompute all modules, tenant scoping."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

ALL_MODULES = ["liquidity", "capital", "irr", "fx", "ftp", "forecast"]


def _seed_bank_and_canonical(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()


def _activate(db_client: TestClient, *, run_calculations: bool = True) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/data-activations",
        headers=headers(),
        json={
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Activate the uploaded fixture book.",
            "run_calculations": run_calculations,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_activation_derives_facts_and_runs_all_six_modules(db_client: TestClient) -> None:
    _seed_bank_and_canonical(db_client)
    body = _activate(db_client)

    assert body["period_label"] == "2026-06"
    assert body["period_created"] is True
    assert body["facts_created"] > 50
    derived = {group["group"] for group in body["groups"] if group["status"] == "derived"}
    assert "balance_sheet" in derived
    assert "irr_position" in derived

    runs = {run["module"]: run for run in body["runs"]}
    assert list(runs) == ALL_MODULES
    assert all(run["status"] == "succeeded" for run in runs.values()), runs
    assert runs["liquidity"]["headline"].startswith("LCR ")
    assert runs["capital"]["headline"].startswith("CAR ")
    assert runs["irr"]["headline"].startswith("worst ΔEVE/Tier1 ")
    assert runs["fx"]["headline"].startswith("NOP/Tier1 ")
    assert runs["ftp"]["headline"].startswith("portfolio NIM ")
    assert runs["forecast"]["headline"].startswith("avg ROE ")
    # 4 liquidity + 4 capital + 7 IRR + 4 FX + 3 FTP scenarios, 1 forecast run.
    assert runs["liquidity"]["scenarios_succeeded"] == 4
    assert runs["irr"]["scenarios_succeeded"] == 7
    assert runs["forecast"]["scenarios_succeeded"] == 1

    # The new period is visible to the dashboards and each module answers 200.
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    period = next(p for p in periods if p["label"] == "2026-06")
    for path in (
        f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/dashboard",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/dashboard",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/dashboard",
    ):
        response = db_client.get(
            path, headers=headers(), params={"reporting_period_id": period["id"]}
        )
        assert response.status_code == 200, f"{path}: {response.text}"

    # The activation is listed from its audit trail.
    listing = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/data-activations", headers=headers())
    assert listing.status_code == 200
    activations = listing.json()["activations"]
    assert len(activations) == 1
    assert activations[0]["period_label"] == "2026-06"
    assert activations[0]["modules_succeeded"] == 6


def test_reactivation_rebuilds_facts_and_appends_new_runs(db_client: TestClient) -> None:
    _seed_bank_and_canonical(db_client)
    first = _activate(db_client)
    second = _activate(db_client)

    assert second["period_created"] is False
    assert second["facts_deleted"] == first["facts_created"]
    assert second["facts_created"] == first["facts_created"]

    # Run history is immutable: the second activation appended new runs.
    runs = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "liquidity", "reporting_period_id": second["reporting_period_id"]},
    ).json()
    assert runs["total"] == 8  # 4 scenarios x 2 activations


def test_activation_without_calculations_only_derives(db_client: TestClient) -> None:
    _seed_bank_and_canonical(db_client)
    body = _activate(db_client, run_calculations=False)
    assert body["runs"] == []
    assert body["facts_created"] > 50

    runs = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"reporting_period_id": body["reporting_period_id"]},
    ).json()
    assert runs["total"] == 0


def test_activation_conflicts_without_canonical_data(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/data-activations",
        headers=headers(),
        json={
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Nothing was ingested.",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "no_canonical_data"


def test_activation_is_tenant_scoped(db_client: TestClient) -> None:
    _seed_bank_and_canonical(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/data-activations",
        headers=headers(ORG_2),
        json={
            "as_of_date": FIXTURE_AS_OF.isoformat(),
            "reason": "Cross-tenant attempt.",
        },
    )
    assert response.status_code == 404, response.text
    listing = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/data-activations", headers=headers(ORG_2)
    )
    assert listing.status_code == 404
