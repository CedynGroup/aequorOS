from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.db.session import get_sessionmaker
from app.models import BankFinancialFact, ParamStressShock
from app.services.sample_bank_seed import DEMO_ORG_ID, JURISDICTION_CODE, SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers

FOUR_DP = Decimal("0.0001")
IRR_SCENARIOS = [
    "baseline",
    "parallel_up_200",
    "parallel_down_200",
    "short_up_250",
    "short_down_250",
    "steepener",
    "flattener",
]


def _seed_latest_period(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _run_all(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _four_dp(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(FOUR_DP)


def _set_fact_amount(period_id: str, fact_group: str, category: str, amount: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            update(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == DEMO_ORG_ID,
                BankFinancialFact.bank_id == SAMPLE_BANK_ID,
                BankFinancialFact.reporting_period_id == UUID(period_id),
                BankFinancialFact.fact_group == fact_group,
                BankFinancialFact.category == category,
            )
            .values(amount=Decimal(amount))
        )
        session.commit()
    finally:
        session.close()


def _delete_irr_scenario_shock(scenario_code: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(ParamStressShock).where(
                ParamStressShock.organization_id == DEMO_ORG_ID,
                ParamStressShock.jurisdiction_code == JURISDICTION_CODE,
                ParamStressShock.module == "irr",
                ParamStressShock.scenario_code == scenario_code,
            )
        )
        session.commit()
    finally:
        session.close()


def test_run_all_irr_scenarios_persists_seven_runs_with_golden_metrics(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    batch = _run_all(db_client, period["id"])

    runs = batch["runs"]
    assert [run["scenario_code"] for run in runs] == IRR_SCENARIOS
    assert all(run["status"] == "succeeded" for run in runs)
    assert all(run["module"] == "irr" for run in runs)
    assert all(run["engine_version"] == "regulatory-irr-v1.0.0" for run in runs)
    assert all(len(run["input_hash"]) == 64 for run in runs)
    # scenario_code is part of the snapshot, so each run gets a distinct hash.
    assert len({run["input_hash"] for run in runs}) == 7

    baseline = runs[0]
    snapshot = baseline["inputs"]
    assert snapshot["module"] == "irr"
    assert snapshot["as_of_date"] == "2026-03-31"
    assert {fact["fact_group"] for fact in snapshot["facts"]} == {"irr_position", "irr_swap"}
    assert len(snapshot["facts"]) == 24
    assert set(snapshot["parameters"]) == {"base_curve_pct", "scenario_shocks", "limits_pct"}

    metrics = baseline["metrics"]
    assert Decimal(metrics["cumulative_12m_gap_ghs"]) == Decimal("-370000000")
    assert Decimal(metrics["ear_up_200_ghs"]) == Decimal("-7450600")
    assert Decimal(metrics["ear_down_200_ghs"]) == Decimal("7450600")
    assert Decimal(metrics["eve_base_ghs"]) == Decimal("-100562865.7068")
    assert metrics["worst_scenario"] == "parallel_down_200"
    assert Decimal(metrics["worst_eve_change_ghs"]) == Decimal("14986975.3393")
    assert _four_dp(metrics["worst_eve_change_pct_tier1"]) == Decimal("5.3525")
    assert Decimal(metrics["tier1_ghs"]) == Decimal("280000000")
    assert Decimal(metrics["nii_base_ghs"]) == Decimal("253780000")
    assert _four_dp(metrics["asset_duration"]) == Decimal("0.8290")
    assert _four_dp(metrics["liability_duration"]) == Decimal("0.3267")
    assert _four_dp(metrics["duration_gap"]) == Decimal("0.4807")
    assert len(metrics["gap_buckets"]) == 9
    assert len(metrics["eve_by_scenario"]) == 6

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    assert {
        "worst_eve_change_pct_tier1",
        "duration_gap",
        "asset_duration",
        "liability_duration",
        "cumulative_12m_gap_ghs",
        "eve_base_ghs",
        "ear_up_200_ghs",
        "ear_down_200_ghs",
    } == set(metric_results)
    assert metric_results["worst_eve_change_pct_tier1"]["unit"] == "pct"
    assert metric_results["worst_eve_change_pct_tier1"]["status"] == "green"
    assert Decimal(metric_results["worst_eve_change_pct_tier1"]["threshold_min"]) == Decimal("15")
    assert metric_results["duration_gap"]["unit"] == "years"
    assert metric_results["cumulative_12m_gap_ghs"]["unit"] == "ghs"

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["irr_gap"]) == 9
    assert len(sections["irr_eve"]) == 7
    assert len(sections["irr_ear"]) == 2
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    validations = {item["rule_code"]: item for item in baseline["validations"]}
    assert set(validations) == {
        "eve_within_limit",
        "ear_within_limit",
        "duration_gap_reasonable",
    }
    assert validations["eve_within_limit"]["passed"] is True
    assert validations["eve_within_limit"]["severity"] == "error"
    assert validations["ear_within_limit"]["passed"] is True
    assert validations["ear_within_limit"]["severity"] == "warning"
    assert validations["duration_gap_reasonable"]["severity"] == "info"

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{baseline['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_irr_input_hash_is_scoped_to_irr_facts(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _run_all(db_client, period["id"])["runs"][0]

    # Editing a capital-component fact changes Tier 1 (an external reference) but
    # must NOT disturb the IRR input hash.
    _set_fact_amount(period["id"], "capital_component", "retained_earnings", "96000000")
    second = _run_all(db_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing an IRR position must change it.
    _set_fact_amount(period["id"], "irr_position", "corp_loans_fixed", "241000000")
    third = _run_all(db_client, period["id"])["runs"][0]
    assert third["input_hash"] != first["input_hash"]
    assert third["status"] == "succeeded"

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "irr", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 3


def test_irr_dashboard_computes_inline_then_prefers_stored_runs(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    inline = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/dashboard", headers=headers())
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    assert Decimal(metrics["cumulative_12m_gap_ghs"]) == Decimal("-370000000")
    assert metrics["worst_scenario_code"] == "parallel_down_200"
    assert metrics["eve_status"] == "green"
    assert _four_dp(metrics["duration_gap"]) == Decimal("0.4807")
    assert len(body["gap_table"]) == 9
    assert len(body["eve_scenarios"]) == 6
    assert len(body["validations"]) == 3

    trend = body["trend"]
    assert len(trend) == 12
    assert [point["label"] for point in trend][:2] == ["2025-04", "2025-05"]
    assert trend[-1]["label"] == "2026-03"
    assert all(point["stored"] is False for point in trend)

    _run_all(db_client, period["id"])
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] is not None
    assert Decimal(body["metrics"]["eve_base_ghs"]) == Decimal("-100562865.7068")
    trend = body["trend"]
    assert len(trend) == 12
    assert trend[-1]["stored"] is True
    assert all(point["stored"] is False for point in trend[:-1])


def test_missing_irr_shock_persists_failed_runs_without_500(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _delete_irr_scenario_shock("flattener")

    batch = _run_all(db_client, period["id"])
    runs = batch["runs"]
    # Every run evaluates all six EVE scenarios, so the missing flattener shock
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert runs[0]["error"]["code"] == "missing_parameter"
    assert "flattener" in str(runs[0]["error"]["details"])

    dashboard = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    # With no successful stored run, the dashboard falls back to inline compute
    # and surfaces the missing parameter as a 409, not a 500.
    assert dashboard.status_code == 409
    assert dashboard.json()["error"]["details"]["error_code"] == "missing_parameter"


def test_unknown_bank_and_period_return_404(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    assert (
        db_client.post(
            f"/api/v1/banks/{uuid4()}/irr/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/irr/dashboard", headers=headers()).status_code
        == 404
    )


def test_regulatory_irr_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_all(db_client, period["id"])

    org2 = headers(ORG_2)
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/run-all-scenarios",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/dashboard", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            params={"module": "irr"},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "irr"},
    ).json()
    assert listed["total"] == 7
