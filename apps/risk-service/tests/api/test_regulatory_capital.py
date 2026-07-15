from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db.session import get_sessionmaker
from app.models import BankFinancialFact
from app.services.sample_bank_seed import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers

FOUR_DP = Decimal("0.0001")
GOLDEN_BASELINE_RATIOS = {
    "car_pct": Decimal("15.8324"),
    "tier1_ratio_pct": Decimal("13.0384"),
    "cet1_ratio_pct": Decimal("12.1071"),
    "leverage_ratio_pct": Decimal("10.9375"),
}
GOLDEN_END_CAR_BY_SCENARIO = {
    "mild": Decimal("17.8370"),
    "moderate": Decimal("15.7442"),
    "severe": Decimal("9.3173"),
}
CAPITAL_FACT_GROUPS = {
    "balance_sheet",
    "capital_component",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
}


def _seed_latest_period(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _create_run(db_client: TestClient, period_id: str, scenario_code: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "capital",
            "reporting_period_id": period_id,
            "scenario_code": scenario_code,
        },
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


def test_create_baseline_capital_run_persists_snapshot_metrics_and_outputs(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    run = _create_run(db_client, period["id"], "baseline")

    assert run["status"] == "succeeded"
    assert run["module"] == "capital"
    assert run["scenario_code"] == "baseline"
    assert run["engine_version"] == "regulatory-capital-v1.0.0"
    assert run["input_schema_version"] == "bank-facts-v1"
    assert run["output_schema_version"] == "capital-metrics-v1"
    assert run["started_at"] is not None
    assert run["completed_at"] is not None
    assert run["error"] is None
    assert len(run["input_hash"]) == 64

    snapshot = run["inputs"]
    assert snapshot["schema_version"] == "bank-facts-v1"
    assert snapshot["module"] == "capital"
    assert snapshot["scenario_code"] == "baseline"
    assert snapshot["as_of_date"] == "2026-03-31"
    assert snapshot["reporting_period"]["label"] == "2026-03"
    # Only capital fact groups participate: 15 balance-sheet + 9 capital components
    # + 6 loan exposures + 2 market-risk + 2 off-balance + 3 operational income
    # + 4 securities.
    assert len(snapshot["facts"]) == 41
    assert {fact["fact_group"] for fact in snapshot["facts"]} == CAPITAL_FACT_GROUPS
    assert snapshot["shocks"] == {}
    assert set(snapshot["parameters"]) == {"risk_weights_pct", "thresholds_pct"}
    assert snapshot["parameters"]["risk_weights_pct"]["RW100"] == "100.000000"
    assert snapshot["parameters"]["thresholds_pct"]["rwa_multiplier"] == "1250.000000"

    metrics = run["metrics"]
    for code, expected in GOLDEN_BASELINE_RATIOS.items():
        assert _four_dp(metrics[code]) == expected, code
    assert Decimal(metrics["total_rwa_ghs"]) == Decimal("2147500000")
    assert Decimal(metrics["credit_rwa_ghs"]) == Decimal("1402500000")
    assert Decimal(metrics["market_rwa_ghs"]) == Decimal("45000000")
    assert Decimal(metrics["operational_rwa_ghs"]) == Decimal("700000000")
    assert Decimal(metrics["total_capital_ghs"]) == Decimal("340000000")
    assert "stress_path" not in metrics
    assert "triggers" not in metrics

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert set(metric_results) == {
        "car_pct",
        "tier1_ratio_pct",
        "cet1_ratio_pct",
        "leverage_ratio_pct",
    }
    assert metric_results["car_pct"]["status"] == "green"
    assert metric_results["car_pct"]["unit"] == "pct"
    assert Decimal(metric_results["car_pct"]["threshold_min"]) == Decimal("10")
    assert Decimal(metric_results["tier1_ratio_pct"]["threshold_min"]) == Decimal("8")
    assert Decimal(metric_results["cet1_ratio_pct"]["threshold_min"]) == Decimal("6.5")
    assert Decimal(metric_results["leverage_ratio_pct"]["threshold_min"]) == Decimal("3")
    assert all(item["status"] == "green" for item in metric_results.values())

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in run["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["credit_rwa"]) == 12
    assert len(sections["market_rwa"]) == 4
    assert len(sections["operational_rwa"]) == 5
    assert len(sections["capital_component"]) == 9
    assert len(sections["ratio"]) == 4
    positions = [item["position"] for item in run["line_items"]]
    assert positions == sorted(positions)
    credit = {item["line_code"]: item for item in sections["credit_rwa"]}
    assert Decimal(credit["corporate_unrated"]["weighted_amount"]) == Decimal("560000000")
    assert Decimal(credit["committed_retail"]["exposure_amount"]) == Decimal("40000000")
    assert Decimal(credit["committed_retail"]["weighted_amount"]) == Decimal("30000000")
    assert Decimal(credit["cash_and_reserves"]["exposure_amount"]) == Decimal("290000000")
    assert Decimal(credit["cash_and_reserves"]["weighted_amount"]) == Decimal("0")
    components = {item["line_code"]: item for item in sections["capital_component"]}
    assert Decimal(components["cet1:intangibles"]["weighted_amount"]) == Decimal("-25000000")
    assert Decimal(components["t2:general_provisions"]["weighted_amount"]) == Decimal("15000000")

    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == {
        "car_above_minimum",
        "cet1_above_minimum",
        "tier1_above_minimum",
        "leverage_above_minimum",
        "tier2_gp_cap_applied",
    }
    assert all(item["passed"] is True for item in validations.values())
    assert validations["car_above_minimum"]["severity"] == "error"
    assert validations["tier2_gp_cap_applied"]["severity"] == "info"
    assert "did not bind" in validations["tier2_gp_cap_applied"]["message"]

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{run['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]


def test_capital_input_hash_is_scoped_to_capital_fact_groups(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _create_run(db_client, period["id"], "baseline")

    # Editing a liquidity-only fact must not disturb the capital input hash.
    _set_fact_amount(period["id"], "lcr_inflow", "retail_loan_repayments", "61000000")
    second = _create_run(db_client, period["id"], "baseline")
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing a capital fact must change it.
    _set_fact_amount(period["id"], "loan_exposure", "corporate_unrated", "561000000")
    third = _create_run(db_client, period["id"], "baseline")
    assert third["input_hash"] != first["input_hash"]
    assert third["status"] == "succeeded"

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "capital", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 3


def test_run_all_capital_scenarios_returns_four_runs_with_stress_outputs(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    runs = response.json()["runs"]
    assert [run["scenario_code"] for run in runs] == ["baseline", "mild", "moderate", "severe"]
    assert all(run["status"] == "succeeded" for run in runs)
    assert "stress_path" not in runs[0]["metrics"]

    for run in runs[1:]:
        scenario = run["scenario_code"]
        stress_path = run["metrics"]["stress_path"]
        assert [row["quarter"] for row in stress_path] == [0, 1, 2, 3, 4], scenario
        assert _four_dp(stress_path[0]["car"]) == Decimal("15.8324"), scenario
        assert _four_dp(stress_path[-1]["car"]) == GOLDEN_END_CAR_BY_SCENARIO[scenario], scenario
        assert len(run["metrics"]["triggers"]) == 3, scenario
        metric_results = {item["metric_code"]: item for item in run["metric_results"]}
        assert (
            _four_dp(metric_results["car_pct_end"]["metric_value"])
            == (GOLDEN_END_CAR_BY_SCENARIO[scenario])
        ), scenario

    mild, moderate, severe = runs[1], runs[2], runs[3]
    for run in (mild, moderate):
        triggers = {item["code"]: item for item in run["metrics"]["triggers"]}
        assert all(item["fired"] is False for item in triggers.values())
        metric_results = {item["metric_code"]: item for item in run["metric_results"]}
        assert metric_results["car_pct_end"]["status"] == "green"

    severe_triggers = {item["code"]: item for item in severe["metrics"]["triggers"]}
    assert severe_triggers["early_warning"]["fired"] is True
    assert severe_triggers["early_warning"]["first_quarter"] == 4
    assert severe_triggers["breach"]["fired"] is True
    assert severe_triggers["breach"]["first_quarter"] == 4
    assert severe_triggers["critical"]["fired"] is False
    assert severe_triggers["critical"]["first_quarter"] is None
    severe_results = {item["metric_code"]: item for item in severe["metric_results"]}
    assert severe_results["car_pct_end"]["status"] == "red"
    severe_validations = {item["rule_code"]: item for item in severe["validations"]}
    assert severe_validations["capital_trigger_early_warning"]["passed"] is False
    assert severe_validations["capital_trigger_early_warning"]["severity"] == "warning"
    assert severe_validations["capital_trigger_breach"]["passed"] is False
    assert severe_validations["capital_trigger_breach"]["severity"] == "error"
    assert severe_validations["capital_trigger_critical"]["passed"] is True


def test_capital_dashboard_computes_inline_then_prefers_stored_runs(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)

    inline = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard", headers=headers())
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    for code, expected in GOLDEN_BASELINE_RATIOS.items():
        assert _four_dp(metrics[code]) == expected, code
    assert metrics["car_status"] == "green"
    assert metrics["leverage_status"] == "green"

    composition = body["rwa_composition"]
    assert Decimal(composition["credit_rwa_ghs"]) == Decimal("1402500000")
    assert Decimal(composition["market_rwa_ghs"]) == Decimal("45000000")
    assert Decimal(composition["operational_rwa_ghs"]) == Decimal("700000000")
    assert Decimal(composition["total_rwa_ghs"]) == Decimal("2147500000")
    assert len(composition["credit_lines"]) == 12

    structure = body["capital_structure"]
    assert len(structure["cet1_components"]) == 4
    assert len(structure["cet1_deductions"]) == 2
    assert len(structure["at1_components"]) == 1
    assert len(structure["tier2_components"]) == 2
    assert Decimal(structure["cet1_capital_ghs"]) == Decimal("260000000")
    assert Decimal(structure["at1_capital_ghs"]) == Decimal("20000000")
    assert Decimal(structure["tier1_capital_ghs"]) == Decimal("280000000")
    assert Decimal(structure["tier2_capital_ghs"]) == Decimal("60000000")
    assert Decimal(structure["total_capital_ghs"]) == Decimal("340000000")

    buffers = body["buffers"]
    assert Decimal(buffers["car_min_pct"]) == Decimal("10")
    assert Decimal(buffers["car_early_warning_pct"]) == Decimal("10.5")
    assert buffers["car_early_warning_label"] == "Early warning / conservation buffer floor"
    assert Decimal(buffers["car_critical_pct"]) == Decimal("9")
    assert _four_dp(buffers["current_car_pct"]) == Decimal("15.8324")
    assert _four_dp(buffers["headroom_pp"]) == Decimal("5.8324")

    trend = body["trend"]
    assert len(trend) == 12
    assert [point["label"] for point in trend][:2] == ["2025-04", "2025-05"]
    assert trend[-1]["label"] == "2026-03"
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert all(point["stored"] is False for point in trend)
    assert len(body["validations"]) == 5

    run = _create_run(db_client, period["id"], "baseline")
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == run["id"]
    assert _four_dp(body["metrics"]["car_pct"]) == Decimal("15.8324")
    trend = body["trend"]
    assert len(trend) == 12
    assert trend[-1]["stored"] is True
    assert all(point["stored"] is False for point in trend[:-1])


def test_structure_and_rwa_endpoints_require_a_baseline_run(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    for path in (
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/structure",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/rwa",
    ):
        blocked = db_client.get(path, headers=headers())
        assert blocked.status_code == 409, path
        assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(db_client, period["id"], "baseline")

    structure = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/structure",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert structure.status_code == 200, structure.text
    body = structure.json()
    assert body["run_id"] == run["id"]
    assert body["reporting_period_id"] == period["id"]
    assert Decimal(body["cet1_capital_ghs"]) == Decimal("260000000")
    assert Decimal(body["tier2_capital_ghs"]) == Decimal("60000000")
    assert Decimal(body["total_capital_ghs"]) == Decimal("340000000")
    assert [line["line_code"] for line in body["at1_components"]] == ["at1:perpetual_instruments"]

    rwa = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/rwa",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert rwa.status_code == 200, rwa.text
    body = rwa.json()
    assert body["run_id"] == run["id"]
    assert Decimal(body["total_rwa_ghs"]) == Decimal("2147500000")
    assert len(body["credit_lines"]) == 12
    assert len(body["market_lines"]) == 4
    assert len(body["operational_lines"]) == 5
    weighted_credit = sum(Decimal(line["weighted_amount"]) for line in body["credit_lines"])
    assert weighted_credit == Decimal("1402500000")


def test_bsd2_preview_requires_baseline_run_then_renders_rows(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)

    blocked = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(db_client, period["id"], "baseline")
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()

    header = preview["header"]
    assert header["form_code"] == "BSD-2"
    assert header["form_title"] == "Capital Adequacy Return"
    assert header["regulator"] == "Bank of Ghana"
    assert header["bank_name"] == "Sample Bank Ltd"
    assert header["reporting_period_label"] == "2026-03"
    assert header["currency"] == "GHS"
    assert header["preview_note"] == (
        "PREVIEW ONLY — This system does not file submissions with Bank of Ghana."
    )
    assert preview["run_id"] == run["id"]
    assert preview["scenario_code"] == "baseline"

    assert [row["row_code"] for row in preview["cet1_rows"]] == ["1.1", "1.2", "1.3", "1.4"]
    cet1_gross = sum(Decimal(row["amount"]) for row in preview["cet1_rows"])
    assert cet1_gross == Decimal("300000000")
    assert [row["row_code"] for row in preview["deduction_rows"]] == ["2.1", "2.2"]
    deductions = sum(Decimal(row["amount"]) for row in preview["deduction_rows"])
    assert deductions == Decimal("-40000000")
    assert preview["cet1_total"]["row_code"] == "3.0"
    assert Decimal(preview["cet1_total"]["value"]) == Decimal("260000000")
    assert [row["row_code"] for row in preview["at1_rows"]] == ["4.1"]
    assert Decimal(preview["tier1_total"]["value"]) == Decimal("280000000")
    assert [row["row_code"] for row in preview["tier2_rows"]] == ["6.1", "6.2"]
    gp_row = next(
        row for row in preview["tier2_rows"] if "General Provisions" in row["description"]
    )
    assert "cap not binding" in gp_row["description"]
    assert "1.25% of credit RWA" in gp_row["description"]
    assert Decimal(gp_row["amount"]) == Decimal("15000000")
    assert preview["total_capital"]["row_code"] == "7.0"
    assert Decimal(preview["total_capital"]["value"]) == Decimal("340000000")

    credit_rows = preview["credit_rwa_rows"]
    assert [row["row_code"] for row in credit_rows] == [f"8.{i}" for i in range(1, 13)]
    weighted_credit = sum(Decimal(row["weighted_amount"]) for row in credit_rows)
    assert weighted_credit == Decimal("1402500000")
    assert [row["row_code"] for row in preview["market_rwa_rows"]] == [
        f"9.{i}" for i in range(1, 5)
    ]
    assert [row["row_code"] for row in preview["operational_rwa_rows"]] == [
        f"10.{i}" for i in range(1, 6)
    ]
    assert preview["total_rwa"]["row_code"] == "11.0"
    assert Decimal(preview["total_rwa"]["value"]) == Decimal("2147500000")

    ratio_rows = {row["row_code"]: row for row in preview["ratio_rows"]}
    assert set(ratio_rows) == {"12.1", "12.2", "12.3", "12.4"}
    assert Decimal(ratio_rows["12.1"]["minimum_pct"]) == Decimal("6.5")
    assert Decimal(ratio_rows["12.2"]["minimum_pct"]) == Decimal("8")
    assert Decimal(ratio_rows["12.3"]["minimum_pct"]) == Decimal("10")
    assert Decimal(ratio_rows["12.4"]["minimum_pct"]) == Decimal("3")
    assert _four_dp(ratio_rows["12.3"]["value_pct"]) == Decimal("15.8324")
    assert all(row["passed"] is True for row in ratio_rows.values())
    assert len(preview["validations"]) == 5


def test_invalid_module_scenario_combinations_are_rejected_with_422(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    combos = (
        ("capital", "idiosyncratic"),
        ("capital", "market_wide"),
        ("capital", "combined"),
        ("liquidity", "mild"),
        ("liquidity", "severe"),
        ("forecast", "baseline"),
    )
    for module, scenario_code in combos:
        response = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=headers(),
            json={
                "module": module,
                "reporting_period_id": period["id"],
                "scenario_code": scenario_code,
            },
        )
        assert response.status_code == 422, (module, scenario_code)


def test_unknown_bank_and_period_return_404(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    assert (
        db_client.post(
            f"/api/v1/banks/{uuid4()}/capital/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/capital/dashboard", headers=headers()).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
            headers=headers(),
            params={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )


def test_regulatory_capital_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    run = _create_run(db_client, period["id"], "baseline")

    org2 = headers(ORG_2)
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            json={
                "module": "capital",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/run-all-scenarios",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/rwa", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/structure", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
            headers=org2,
            params={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{run['id']}", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            params={"module": "capital"},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "capital"},
    ).json()
    assert listed["total"] == 1
