"""Enterprise-wide stress test API (docs/stress.md §3.3–3.4, Phase 2).

One approved macro scenario drives every engine into a single immutable
RegulatoryRun (module enterprise_stress) carrying the base+stress 3-year
projection and the Appendix II Tables 1–6. Covers persistence + provenance,
reproducibility (stable input_hash), the approved-scenario governance gate, and
tenant isolation — against the deterministic canonical seeded book.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import User
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.api.test_ingestion import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
LATEST_URL = "/api/v1/banks/{bank_id}/enterprise-stress/latest"
SCENARIO_URL = "/api/v1/macro-scenarios"


def _period_id(client: TestClient, bank_id: str) -> str:
    response = client.get(f"/api/v1/banks/{bank_id}/reporting-periods", headers=headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    return next(p["id"] for p in periods if p["period_end"] == "2026-03-31")


def _seed_checker(client: TestClient) -> UUID:
    _ = client
    checker_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=checker_id,
                organization_id=ORG_1,
                email=f"checker-{checker_id.hex[:8]}@aequoros.example",
                display_name="Scenario Checker",
                role="approver",
            )
        )
        session.commit()
    finally:
        session.close()
    return checker_id


def _path(variable: str, year: int, base: str, stress: str) -> dict:
    return {"variable": variable, "year_index": year, "base_value": base, "stress_value": stress}


def _severe_paths() -> list[dict]:
    levels = {
        "gdp_growth": ("0.05", "0.00"),
        "interest_rate": ("0.20", "0.25"),
        "inflation": ("0.15", "0.21"),
        "unemployment": ("0.06", "0.09"),
        "fx_usd_ghs": ("12.5", "15.0"),
        "gse_index": ("5000", "3500"),
        "gog_yield": ("0.22", "0.26"),
    }
    paths: list[dict] = []
    for variable, (base, stress) in levels.items():
        for year in (1, 2, 3):
            paths.append(_path(variable, year, base, stress))
    return paths


def _scenario_payload(code: str) -> dict:
    return {
        "code": code,
        "name": "2027 severe downturn",
        "scenario_type": "adverse",
        "severity": "severe",
        "horizon_years": 3,
        "narrative": "GDP contraction, cedi depreciation, rate spike.",
        "source": "BoG MPC + internal desk",
        "paths": _severe_paths(),
        "reason": "Author the annual adverse scenario.",
    }


def _create_scenario(client: TestClient, code: str = "adverse_2027") -> str:
    response = client.post(
        SCENARIO_URL,
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json=_scenario_payload(code),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _approve_scenario(client: TestClient, scenario_id: str, checker: UUID) -> None:
    submit = client.post(
        f"{SCENARIO_URL}/{scenario_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"reason": "Ready for approval."},
    )
    assert submit.status_code == 200, submit.text
    approve = client.post(
        f"{SCENARIO_URL}/{scenario_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "Reviewed and approved."},
    )
    assert approve.status_code == 200, approve.text


def test_enterprise_stress_persists_run_projection_and_appendix(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client)
    _approve_scenario(db_client, scenario_id, checker)

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Annual ICAAP stress test.",
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert len(run["input_hash"]) == 64
    assert run["engine_version"] == "enterprise-stress-v1.0.0"
    assert run["scenario_code"] == "adverse_2027"

    # The outcome couples solvency and liquidity, both baseline vs stressed.
    outcome = run["outcome"]
    assert "capital" in outcome and "liquidity" in outcome and "coupling" in outcome
    assert outcome["capital"]["stressed_car_end_pct"] is not None

    # The 3-year projection carries base + stress legs.
    projection = run["projection"]
    assert len(projection["base"]) == 3
    assert len(projection["stress"]) == 3

    # Appendix II carries all six tables and honours the RWA tie.
    appendix = run["appendix_ii"]
    assert appendix["unit"] == "GHS'000"
    t1 = appendix["table1_summary"]
    assert len(t1["post_adverse"]) == 3
    assert t1["management_actions"] is None  # pre-management-action output
    t5_by_label = {row["label"]: row["total_pillar1_rwa"] for row in appendix["table5_rwa"]["rows"]}
    for snapshot in t1["post_adverse"]:
        assert t5_by_label[snapshot["label"]] == snapshot["total_rwa"]
    assert len(appendix["table6_risk_drivers"]["rows"]) == 21  # 7 vars × 3 years

    # Reproducibility: a rerun over the same book + scenario anchors the same hash.
    again = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Rerun for determinism check.",
        },
    )
    assert again.status_code == 201, again.text
    assert again.json()["input_hash"] == run["input_hash"]

    latest = db_client.get(
        LATEST_URL.format(bank_id=bank_id)
        + f"?reporting_period_id={period_id}&scenario_id={scenario_id}",
        headers=headers(),
    )
    assert latest.status_code == 200
    assert latest.json()["run_id"] == again.json()["run_id"]

    # Tenant isolation: another org cannot see the run.
    foreign = db_client.get(
        LATEST_URL.format(bank_id=bank_id)
        + f"?reporting_period_id={period_id}&scenario_id={scenario_id}",
        headers=headers(ORG_2),
    )
    assert foreign.status_code == 404


def test_enterprise_stress_requires_an_approved_scenario(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    scenario_id = _create_scenario(db_client, code="draft_2027")  # left as a draft

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Attempt to run an unapproved scenario.",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "scenario_not_approved"


def test_enterprise_stress_run_registry_lists_and_reopens(db_client: TestClient) -> None:
    """The run registry (docs/stress.md §4.5): full history + re-open-by-id."""
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client)
    _approve_scenario(db_client, scenario_id, checker)

    first = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "First quarterly run.",
        },
    )
    assert first.status_code == 201, first.text
    second = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Re-run.",
        },
    )
    assert second.status_code == 201, second.text

    # List: both runs, newest first, as lightweight summaries.
    listing = db_client.get(RUNS_URL.format(bank_id=bank_id), headers=headers())
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 2
    run_ids = {row["run_id"] for row in rows}
    assert run_ids == {first.json()["run_id"], second.json()["run_id"]}
    assert rows[0]["created_at"] >= rows[1]["created_at"]  # newest first
    assert rows[0]["scenario_code"] == "adverse_2027"
    assert rows[0]["car_erosion_pp"] is not None
    assert "stress_stays_above_all_minima" in rows[0]

    # Period filter narrows to the same period.
    filtered = db_client.get(
        RUNS_URL.format(bank_id=bank_id) + f"?reporting_period_id={period_id}",
        headers=headers(),
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 2

    # Re-open one run by id → the full read with projection + appendix.
    reopen = db_client.get(
        RUNS_URL.format(bank_id=bank_id) + f"/{first.json()['run_id']}",
        headers=headers(),
    )
    assert reopen.status_code == 200, reopen.text
    body = reopen.json()
    assert body["run_id"] == first.json()["run_id"]
    assert body["scenario_id"] == scenario_id
    assert len(body["projection"]["stress"]) == 3
    assert body["appendix_ii"]["unit"] == "GHS'000"

    # Unknown run id → 404.
    missing = db_client.get(
        RUNS_URL.format(bank_id=bank_id) + "/00000000-0000-0000-0000-000000000000",
        headers=headers(),
    )
    assert missing.status_code == 404

    # Tenant isolation: another org sees neither the list nor the run.
    foreign_list = db_client.get(RUNS_URL.format(bank_id=bank_id), headers=headers(ORG_2))
    assert foreign_list.status_code in (200, 404)
    assert foreign_list.json() == [] if foreign_list.status_code == 200 else True
    foreign_get = db_client.get(
        RUNS_URL.format(bank_id=bank_id) + f"/{first.json()['run_id']}",
        headers=headers(ORG_2),
    )
    assert foreign_get.status_code == 404
