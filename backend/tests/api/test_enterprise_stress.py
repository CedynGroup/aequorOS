"""Enterprise-wide stress test API (docs/stress.md §3.3–3.4, Phase 2).

One approved macro scenario drives every engine into a single immutable
RegulatoryRun (module enterprise_stress) carrying the base+stress 3-year
projection and the Appendix II Tables 1–6. Covers persistence + provenance,
reproducibility (stable input_hash), the approved-scenario governance gate, and
tenant isolation — against the deterministic canonical seeded book.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, RegulatoryRun, User
from app.services import authorization, default_macro_scenarios
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.api.test_fx_authorization import _grant
from tests.api.test_ingestion import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
LATEST_URL = "/api/v1/banks/{bank_id}/enterprise-stress/latest"
SCENARIO_URL = "/api/v1/macro-scenarios"


@pytest.fixture(autouse=True)
def _enterprise_fx_run_authority(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ANALYST,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.FX,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "enterprise-test"),
            reason="Authorize the enterprise calculation fixture FX leg",
            commit=False,
        )
        user = session.get(User, USER_1)
        assert user is not None
        user.authorization_version = 1
        session.commit()


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


def test_enterprise_stress_runs_a_system_default_without_approval(
    db_client: TestClient,
    monkeypatch,
) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    scenario = default_macro_scenarios.DEFAULT_BY_CODE["system_irr_parallel_up_200"]

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": str(scenario.id),
            "reporting_period_id": period_id,
            "reason": "Run the platform IRRBB parallel-up default.",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scenario_id"] == str(scenario.id)
    assert body["scenario_code"] == scenario.code
    assert body["outcome"]["irr"]["delta_eve"] != "0.0000"

    # Immutable runs reopen from their own scenario snapshot even after a
    # catalogue version retires the original system identifier.
    monkeypatch.delitem(default_macro_scenarios.DEFAULT_BY_ID, scenario.id)
    reopened = db_client.get(
        f"{RUNS_URL.format(bank_id=bank_id)}/{body['run_id']}",
        headers=headers(),
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["scenario_id"] == str(scenario.id)
    assert reopened.json()["scenario_code"] == scenario.code

    latest = db_client.get(
        LATEST_URL.format(bank_id=bank_id),
        params={"reporting_period_id": period_id, "scenario_id": str(scenario.id)},
        headers=headers(),
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["run_id"] == body["run_id"]
    assert latest.json()["scenario_id"] == str(scenario.id)


def test_latest_run_distinguishes_system_and_tenant_scenarios_with_same_code(
    db_client: TestClient,
) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    system = default_macro_scenarios.DEFAULT_BY_CODE["system_adverse_bog_style"]
    tenant_id = _create_scenario(db_client, code=system.code)
    _approve_scenario(db_client, tenant_id, _seed_checker(db_client))
    run_ids = {}
    for scenario_id in (str(system.id), tenant_id, tenant_id):
        response = db_client.post(
            RUNS_URL.format(bank_id=bank_id),
            headers=headers(),
            json={
                "scenario_id": scenario_id,
                "reporting_period_id": period_id,
                "reason": "Exercise scenario identity with a shared code.",
            },
        )
        assert response.status_code == 201, response.text
        run_ids[scenario_id] = response.json()["run_id"]

    for scenario_id, run_id in run_ids.items():
        params = {"reporting_period_id": period_id, "scenario_id": scenario_id}
        latest = db_client.get(
            LATEST_URL.format(bank_id=bank_id), params=params, headers=headers()
        )
        assert latest.status_code == 200, latest.text
        assert latest.json()["run_id"] == run_id
        assert latest.json()["scenario_id"] == scenario_id
        foreign = db_client.get(
            LATEST_URL.format(bank_id=bank_id), params=params, headers=headers(ORG_2)
        )
        assert foreign.status_code == 404


@pytest.mark.parametrize("rotation", ["steepener", "flattener"])
def test_cloned_rotation_with_flat_policy_rate_runs(
    db_client: TestClient, rotation: str
) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    system = default_macro_scenarios.DEFAULT_BY_CODE[f"system_irr_{rotation}"]
    clone = db_client.post(
        f"{SCENARIO_URL}/{system.id}/clone",
        headers=headers(),
        json={"reason": "Customize the long end independently."},
    )
    assert clone.status_code == 201, clone.text
    scenario_id = clone.json()["id"]
    paths = [
        {
            "variable": point["variable"],
            "year_index": point["year_index"],
            "base_value": point["base_value"],
            "stress_value": (
                point["base_value"]
                if point["variable"] == "policy_rate"
                else point["stress_value"]
            ),
        }
        for point in clone.json()["paths"]
    ]
    edited = db_client.patch(
        f"{SCENARIO_URL}/{scenario_id}",
        headers=headers(),
        json={"paths": paths, "reason": "Keep policy rates flat."},
    )
    assert edited.status_code == 200, edited.text
    _approve_scenario(db_client, scenario_id, _seed_checker(db_client))
    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Run the approved long-end rotation.",
        },
    )
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["outcome"]["irr"]["delta_eve"]) != 0


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


def test_enterprise_stress_rejects_a_scenario_short_of_the_run_horizon(
    db_client: TestClient,
) -> None:
    """QA audit 2026-08-20 P0-3: a 3-year run must not consume a scenario whose macro
    paths stop short of the horizon — that would mint an 'official' 3-year stress with
    unstressed tail years. The run is refused before any immutable run is written."""
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)

    # A horizon-3 scenario whose paths only reach year 2 (creatable — the create
    # guard only rejects paths BEYOND the horizon, not short coverage).
    short_paths = [
        _path(variable, year, base, stress)
        for variable, (base, stress) in {
            "gdp_growth": ("0.05", "0.00"),
            "inflation": ("0.15", "0.21"),
        }.items()
        for year in (1, 2)
    ]
    payload = _scenario_payload("short_2027")
    payload["paths"] = short_paths
    create = db_client.post(
        SCENARIO_URL, headers=headers(user_id=USER_1, roles=("analyst",)), json=payload
    )
    assert create.status_code == 201, create.text
    scenario_id = create.json()["id"]
    _approve_scenario(db_client, scenario_id, checker)

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "horizon_years": 3,
            "reason": "Attempt a 3-year run on a 2-year scenario.",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "scenario_horizon_too_short"


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


def test_the_car_target_defaults_to_the_governed_floor_not_a_literal(
    db_client: TestClient,
) -> None:
    """D-15: one run, one capital floor.

    ``car_target_pct`` defaulted to the literal ``13`` on the request schema, so
    an enterprise-stress run carried TWO capital floors: the governed,
    effective-dated ``car_min`` the engines check every projected ratio against,
    and this API default, from which Appendix II Table 1's "capital required",
    Table 5's Pillar-1 requirement and the management-action RWA-relief valuation
    were computed. They are the same regulatory quantity, and the literal could
    not track it — BoG has moved the minimum with the CRD ¶75 conservation
    buffer, and an SDI's Act 930 s.29 floor is 10%, not 13%.
    """
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client, code="car_target_default")
    _approve_scenario(db_client, scenario_id, checker)
    body = {
        "scenario_id": scenario_id,
        "reporting_period_id": period_id,
        "reason": "CAR target resolves from the control plane.",
    }

    omitted = db_client.post(RUNS_URL.format(bank_id=bank_id), headers=headers(), json=body)
    assert omitted.status_code == 201, omitted.text
    run = omitted.json()
    # The governed bank floor: CRD ¶71's 10% plus the ¶75 conservation buffer,
    # seeded in the regulatory-parameter control plane as ``car_min`` = 13.
    target = Decimal(run["appendix_ii"]["table1_summary"]["car_target_pct"])
    assert target == Decimal("13")
    assert Decimal(run["appendix_ii"]["table5_rwa"]["car_target_pct"]) == target

    # Stating the same floor explicitly is the same run, hash included — the two
    # paths resolve to ONE number rather than two that happen to agree.
    explicit = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={**body, "car_target_pct": "13"},
    )
    assert explicit.status_code == 201, explicit.text
    assert explicit.json()["input_hash"] == run["input_hash"]


def test_a_car_target_below_the_governed_floor_is_refused(db_client: TestClient) -> None:
    """An internal target may sit ABOVE the regulatory minimum, never below it.

    Appendix II's "capital required" line computed against a target weaker than
    the binding minimum understates what the institution must hold.
    """
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client, code="car_target_too_low")
    _approve_scenario(db_client, scenario_id, checker)

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "A target below the regulatory floor.",
            "car_target_pct": "9",
        },
    )
    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == "car_target_below_regulatory_minimum"
    assert details["details"]["governed_car_min_pct"] == "13"


def _assert_enterprise_fx_projection(
    metrics: dict[str, Any], original_metrics: dict[str, Any], *, visible: bool
) -> None:
    original_rows = original_metrics["appendix_ii"]["table5_rwa"]["rows"]
    original_drivers = original_metrics["appendix_ii"]["table6_risk_drivers"]["rows"]
    assert metrics["outcome"]["capital"] == original_metrics["outcome"]["capital"]
    assert metrics["outcome"]["liquidity"] == original_metrics["outcome"]["liquidity"]
    assert metrics["projection"] == original_metrics["projection"]
    if visible:
        assert metrics["outcome"] == original_metrics["outcome"]
        assert metrics["appendix_ii"] == original_metrics["appendix_ii"]
    else:
        assert "fx" not in metrics["outcome"]
        assert metrics["appendix_ii"]["table6_risk_drivers"]["rows"] == [
            row for row in original_drivers if row["variable"] != "fx_usd_ghs"
        ]
        expected_rows = deepcopy(original_rows)
        for row in expected_rows:
            if row["pillar2"].pop("country_and_fx") is not None:
                del row["pillar2"]["total"]
                del row["total_capital_requirement"]
        assert metrics["appendix_ii"]["table5_rwa"]["rows"] == expected_rows


@pytest.mark.parametrize("include_fx", [True, False])
@pytest.mark.parametrize("fx_sensitivity", [None, "aggregated", "confidential"])
def test_enterprise_readers_project_fx_without_hiding_non_fx(
    db_client: TestClient,
    fx_sensitivity: str | None,
    include_fx: bool,
) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)
    checker = _seed_checker(db_client)
    scenario_id = _create_scenario(db_client)
    _approve_scenario(db_client, scenario_id, checker)
    created = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Verify enterprise result visibility",
            "include_fx": include_fx,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    with get_sessionmaker()() as session:
        stored = session.get(RegulatoryRun, UUID(run_id))
        assert stored is not None
        original_metrics = deepcopy(stored.metrics)
        original_inputs = deepcopy(stored.inputs)
        assert bool(original_metrics["outcome"].get("fx")) == include_fx
        original_rows = original_metrics["appendix_ii"]["table5_rwa"]["rows"]
        assert any(row["pillar2"]["country_and_fx"] is None for row in original_rows)
        assert (
            any(row["pillar2"]["country_and_fx"] is not None for row in original_rows) == include_fx
        )
        original_drivers = original_metrics["appendix_ii"]["table6_risk_drivers"]["rows"]
        assert any(row["variable"] == "fx_usd_ghs" for row in original_drivers)
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    _, version = _grant(module_scope=ModuleScope.CAPITAL, sensitivity_scope=SensitivityScope.ALL)
    if fx_sensitivity:
        _, version = _grant(sensitivity_scope=SensitivityScope(fx_sensitivity))
    reader_headers = headers(roles=("viewer",), authorization_version=version)
    base = f"/api/v1/banks/{bank_id}"
    registry = db_client.get(
        f"{base}/regulatory-runs",
        params={"module": "enterprise_stress", "limit": 1},
        headers=reader_headers,
    )
    assert registry.status_code == 200, registry.text
    assert registry.json()["total"] == 1
    summary_metrics = registry.json()["runs"][0]["metrics"]
    generic = db_client.get(f"{base}/regulatory-runs/{run_id}", headers=reader_headers)
    latest = db_client.get(
        LATEST_URL.format(bank_id=bank_id),
        params={"reporting_period_id": period_id, "scenario_id": scenario_id},
        headers=reader_headers,
    )
    detail = db_client.get(
        f"{RUNS_URL.format(bank_id=bank_id)}/{run_id}",
        headers=reader_headers,
    )
    for response in (generic, latest, detail):
        assert response.status_code == 200, response.text
    for metrics, visible in (
        (summary_metrics, fx_sensitivity == "aggregated"),
        (generic.json()["metrics"], fx_sensitivity == "confidential"),
        (latest.json(), fx_sensitivity == "confidential"),
        (detail.json(), fx_sensitivity == "confidential"),
    ):
        _assert_enterprise_fx_projection(metrics, original_metrics, visible=visible)
    if fx_sensitivity != "confidential":
        visible_inputs = generic.json()["inputs"]
        assert "fx_depreciation_pct" not in visible_inputs["plan"]
        assert all(
            point["variable"] != "fx_usd_ghs" for point in visible_inputs["scenario"]["paths"]
        )
        assert visible_inputs["capital_facts"] == original_inputs["capital_facts"]
    with get_sessionmaker()() as session:
        stored = session.get(RegulatoryRun, UUID(run_id))
        assert stored.metrics == original_metrics
        assert stored.inputs == original_inputs
