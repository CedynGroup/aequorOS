from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.session import get_sessionmaker
from app.models import ParamLcrRunoffRate
from app.services.sample_bank_seed import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers

FOUR_DP = Decimal("0.0001")
GOLDEN_LCR_BY_SCENARIO = {
    "baseline": (Decimal("147.2946"), "green"),
    "idiosyncratic": (Decimal("94.8387"), "amber"),
    "market_wide": (Decimal("113.0091"), "green"),
    "combined": (Decimal("87.3566"), "red"),
}
GOLDEN_NSFR_BY_SCENARIO = {
    "baseline": (Decimal("151.4871"), "green"),
    "idiosyncratic": (Decimal("139.5133"), "green"),
    "market_wide": (Decimal("147.9442"), "green"),
    "combined": (Decimal("136.2505"), "green"),
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
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": scenario_code,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _four_dp(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(FOUR_DP)


def test_create_baseline_run_persists_snapshot_metrics_and_outputs(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    run = _create_run(db_client, period["id"], "baseline")

    assert run["status"] == "succeeded"
    assert run["module"] == "liquidity"
    assert run["scenario_code"] == "baseline"
    assert run["engine_version"] == "regulatory-liquidity-v1.0.0"
    assert run["input_schema_version"] == "bank-facts-v2"
    assert run["output_schema_version"] == "liquidity-metrics-v1"
    assert run["started_at"] is not None
    assert run["completed_at"] is not None
    assert run["error"] is None
    assert len(run["input_hash"]) == 64

    snapshot = run["inputs"]
    assert snapshot["schema_version"] == "bank-facts-v2"
    assert snapshot["scenario_code"] == "baseline"
    assert snapshot["as_of_date"] == "2026-03-31"
    assert snapshot["reporting_period"]["label"] == "2026-03"
    # Only liquidity fact groups participate: 15 balance-sheet + 6 loan exposures
    # + 4 securities + 2 off-balance + 3 LCR inflows.
    assert len(snapshot["facts"]) == 30
    assert {fact["fact_group"] for fact in snapshot["facts"]} == {
        "balance_sheet",
        "lcr_inflow",
        "loan_exposure",
        "off_balance",
        "securities",
    }
    assert snapshot["shocks"] == {}
    assert set(snapshot["parameters"]) == {
        "outflow_runoff_rates_pct",
        "inflow_rates_pct",
        "asf_weights_pct",
        "rsf_weights_pct",
        "thresholds_pct",
    }
    assert snapshot["parameters"]["outflow_runoff_rates_pct"]["retail_deposits_stable"] == (
        "5.000000"
    )

    metrics = run["metrics"]
    assert _four_dp(metrics["lcr_pct"]) == Decimal("147.2946")
    assert _four_dp(metrics["nsfr_pct"]) == Decimal("151.4871")
    assert Decimal(metrics["hqla_total_ghs"]) == Decimal("735000000")
    assert Decimal(metrics["net_outflows_30d_ghs"]) == Decimal("499000000")
    assert Decimal(metrics["asf_total_ghs"]) == Decimal("1961000000")
    assert Decimal(metrics["rsf_total_ghs"]) == Decimal("1294500000")

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert len(metric_results) == 6
    assert metric_results["lcr_pct"]["status"] == "green"
    assert metric_results["lcr_pct"]["unit"] == "pct"
    assert Decimal(metric_results["lcr_pct"]["threshold_min"]) == Decimal("100")
    assert metric_results["nsfr_pct"]["status"] == "green"
    assert metric_results["hqla_total_ghs"]["unit"] == "ghs"
    assert metric_results["hqla_total_ghs"]["status"] == "na"
    assert metric_results["hqla_total_ghs"]["threshold_min"] is None

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in run["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["hqla"]) == 4
    assert len(sections["outflow"]) == 9
    assert len(sections["inflow"]) == 3
    assert len(sections["asf"]) == 8
    assert len(sections["rsf"]) == 13
    positions = [item["position"] for item in run["line_items"]]
    assert positions == sorted(positions)

    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == {
        "lcr_above_minimum",
        "lcr_amber_zone",
        "nsfr_above_minimum",
        "inflow_cap_applied",
        "hqla_all_level1",
    }
    assert validations["lcr_above_minimum"]["passed"] is True
    assert validations["lcr_above_minimum"]["severity"] == "error"
    assert validations["lcr_amber_zone"]["passed"] is True
    assert validations["inflow_cap_applied"]["passed"] is True
    assert "did not bind" in validations["inflow_cap_applied"]["message"]
    assert validations["hqla_all_level1"]["passed"] is True

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{run['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]


def test_identical_rerun_produces_same_input_hash(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _create_run(db_client, period["id"], "baseline")
    second = _create_run(db_client, period["id"], "baseline")

    assert first["id"] != second["id"]
    assert first["input_hash"] == second["input_hash"]
    assert second["status"] == "succeeded"

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "liquidity", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert [item["period_label"] for item in body["runs"]] == ["2026-03", "2026-03"]


def test_run_all_scenarios_returns_four_runs_with_golden_statuses(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    runs = response.json()["runs"]
    assert [run["scenario_code"] for run in runs] == [
        "baseline",
        "idiosyncratic",
        "market_wide",
        "combined",
    ]
    assert all(run["status"] == "succeeded" for run in runs)

    for run in runs:
        scenario = run["scenario_code"]
        expected_lcr, expected_lcr_status = GOLDEN_LCR_BY_SCENARIO[scenario]
        expected_nsfr, expected_nsfr_status = GOLDEN_NSFR_BY_SCENARIO[scenario]
        assert _four_dp(run["metrics"]["lcr_pct"]) == expected_lcr, scenario
        assert _four_dp(run["metrics"]["nsfr_pct"]) == expected_nsfr, scenario
        metric_results = {item["metric_code"]: item for item in run["metric_results"]}
        assert metric_results["lcr_pct"]["status"] == expected_lcr_status, scenario
        assert metric_results["nsfr_pct"]["status"] == expected_nsfr_status, scenario

    combined = runs[3]
    validations = {item["rule_code"]: item for item in combined["validations"]}
    assert validations["lcr_above_minimum"]["passed"] is False
    assert validations["lcr_amber_zone"]["passed"] is True  # red, not amber


def test_dashboard_computes_inline_then_prefers_stored_runs(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    inline = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/dashboard", headers=headers())
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    assert _four_dp(body["metrics"]["lcr_pct"]) == Decimal("147.2946")
    assert _four_dp(body["metrics"]["nsfr_pct"]) == Decimal("151.4871")
    assert body["metrics"]["lcr_status"] == "green"
    assert len(body["hqla_composition"]) == 4
    assert len(body["outflows"]) == 9
    assert len(body["inflows"]) == 3
    assert len(body["validations"]) == 5
    trend = body["trend"]
    assert len(trend) == 12
    assert [point["label"] for point in trend][:2] == ["2025-04", "2025-05"]
    assert trend[-1]["label"] == "2026-03"
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert all(point["stored"] is False for point in trend)

    run = _create_run(db_client, period["id"], "baseline")
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == run["id"]
    assert _four_dp(body["metrics"]["lcr_pct"]) == Decimal("147.2946")
    trend = body["trend"]
    assert len(trend) == 12
    assert trend[-1]["stored"] is True
    assert all(point["stored"] is False for point in trend[:-1])


def test_missing_parameter_persists_failed_run_without_500(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(ParamLcrRunoffRate).where(
                ParamLcrRunoffRate.organization_id == DEMO_ORG_ID,
                ParamLcrRunoffRate.flow_direction == "outflow",
                ParamLcrRunoffRate.category == "wholesale_operational",
            )
        )
        session.commit()
    finally:
        session.close()

    run = _create_run(db_client, period["id"], "baseline")
    assert run["status"] == "failed"
    assert run["error"]["code"] == "missing_parameter"
    assert run["error"]["details"]["category"] == "wholesale_operational"
    assert run["completed_at"] is not None
    assert run["metrics"] == {}
    assert run["metric_results"] == []
    assert run["line_items"] == []
    assert run["validations"] == []

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{run['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "failed"
    assert fetched.json()["error"]["code"] == "missing_parameter"


def test_bsd3_preview_requires_baseline_run_then_renders_rows(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    blocked = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd3",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(db_client, period["id"], "baseline")
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd3",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()

    header = preview["header"]
    assert header["form_code"] == "BSD-3"
    assert header["regulator"] == "Bank of Ghana"
    assert header["bank_name"] == "Sample Bank Ltd"
    assert header["reporting_period_label"] == "2026-03"
    assert header["currency"] == "GHS"
    assert header["preview_note"] == (
        "PREVIEW ONLY — This system does not file submissions with Bank of Ghana."
    )
    assert preview["run_id"] == run["id"]
    assert preview["scenario_code"] == "baseline"

    assert [row["row_code"] for row in preview["hqla_rows"]] == ["1.1", "1.2", "1.3", "1.4"]
    hqla_total = sum(Decimal(row["amount"]) for row in preview["hqla_rows"])
    assert hqla_total == Decimal("735000000")

    outflow_rows = preview["outflow_rows"]
    assert [row["row_code"] for row in outflow_rows] == [f"4.{i}" for i in range(1, 10)]
    weighted_outflows = sum(Decimal(row["weighted_amount"]) for row in outflow_rows)
    assert weighted_outflows == Decimal("619000000")

    inflow_rows = preview["inflow_rows"]
    assert [row["row_code"] for row in inflow_rows] == ["6.1", "6.2", "6.3"]

    summary = {row["row_code"]: row for row in preview["summary_rows"]}
    assert Decimal(summary["3.0"]["value"]) == Decimal("735000000")
    assert Decimal(summary["5.0"]["value"]) == Decimal("619000000")
    assert Decimal(summary["7.0"]["value"]) == Decimal("120000000")
    assert Decimal(summary["8.0"]["value"]) == Decimal("499000000")
    assert _four_dp(summary["9.0"]["value"]) == Decimal("147.2946")
    assert summary["9.0"]["unit"] == "pct"

    nsfr = preview["nsfr"]
    assert len(nsfr["asf_rows"]) == 8
    assert len(nsfr["rsf_rows"]) == 13
    assert Decimal(nsfr["asf_total"]["value"]) == Decimal("1961000000")
    assert Decimal(nsfr["rsf_total"]["value"]) == Decimal("1294500000")
    assert _four_dp(nsfr["nsfr_ratio"]["value"]) == Decimal("151.4871")
    assert len(preview["validations"]) == 5


def test_invalid_module_and_scenario_are_rejected_with_422(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    invalid_module = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "forecast",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert invalid_module.status_code == 422

    capital_scenario_on_liquidity = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "mild",
        },
    )
    assert capital_scenario_on_liquidity.status_code == 422

    invalid_scenario = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "severe",
        },
    )
    assert invalid_scenario.status_code == 422


def test_unknown_bank_and_period_return_404(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    assert (
        db_client.post(
            f"/api/v1/banks/{uuid4()}/regulatory-runs",
            headers=headers(),
            json={
                "module": "liquidity",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=headers(),
            json={
                "module": "liquidity",
                "reporting_period_id": str(uuid4()),
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{uuid4()}", headers=headers()
        ).status_code
        == 404
    )


def test_regulatory_liquidity_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    run = _create_run(db_client, period["id"], "baseline")

    org2 = headers(ORG_2)
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            json={
                "module": "liquidity",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/run-all-scenarios",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs", headers=org2).status_code
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
            f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/dashboard", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd3",
            headers=org2,
            params={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs", headers=headers()
    ).json()
    assert listed["total"] == 1
