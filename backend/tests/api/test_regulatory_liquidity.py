"""Regulatory-liquidity API tests against the ACTUAL primary database.

Reference conversion for the DB-backed suite: the retired sample-bank seed is
gone, so these run against the real Sample Bank (``real_client``, opt-in via
REAL_DATA_DATABASE_URL — see tests/real_data.py). Because the real book changes
as data is ingested, the assertions are INVARIANTS and relationships (LCR =
HQLA / net outflows; NSFR = ASF / RSF; statuses consistent with thresholds;
structural completeness; determinism; tenant isolation), never frozen golden
magnitudes. The whole test is transaction-isolated and rolled back — nothing is
ever committed to the primary.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import ParamLcrRunoffRate
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

FOUR_DP = Decimal("0.0001")
RATIO_TOLERANCE = Decimal("0.05")  # ratio vs its own components, in pct points
LIQUIDITY_FACT_GROUPS = {
    "balance_sheet",
    "lcr_inflow",
    "loan_exposure",
    "off_balance",
    "securities",
}
LIQUIDITY_SCENARIOS = [
    "baseline",
    "idiosyncratic",
    "market_wide",
    "combined",
    "usd_funding_stress",
]


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers()
    )
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _create_run(client: TestClient, period_id: str, scenario_code: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": scenario_code,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _four_dp(value: Any) -> Decimal:
    return _dec(value).quantize(FOUR_DP)


def _assert_ratio(ratio_pct: Any, numerator: Any, denominator: Any) -> None:
    """A ratio metric must equal numerator/denominator*100 (the engine's own
    arithmetic) — a relationship that holds for ANY real book."""
    expected = (_dec(numerator) / _dec(denominator)) * Decimal("100")
    assert abs(_dec(ratio_pct) - expected) <= RATIO_TOLERANCE


def test_baseline_run_persists_snapshot_metrics_and_outputs(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    run = _create_run(real_client, period["id"], "baseline")

    assert run["status"] == "succeeded"
    assert run["module"] == "liquidity"
    assert run["scenario_code"] == "baseline"
    assert run["engine_version"] == "regulatory-liquidity-v1.0.0"
    assert run["input_schema_version"] == "bank-facts-v3"
    assert run["output_schema_version"] == "liquidity-metrics-v1"
    assert run["started_at"] is not None
    assert run["completed_at"] is not None
    assert run["error"] is None
    assert len(run["input_hash"]) == 64

    snapshot = run["inputs"]
    assert snapshot["schema_version"] == "bank-facts-v3"
    assert snapshot["scenario_code"] == "baseline"
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    assert snapshot["facts"], "baseline snapshot must carry liquidity facts"
    assert {fact["fact_group"] for fact in snapshot["facts"]} <= LIQUIDITY_FACT_GROUPS
    assert snapshot["shocks"] == {}
    assert set(snapshot["parameters"]) == {
        "outflow_runoff_rates_pct",
        "inflow_rates_pct",
        "asf_weights_pct",
        "rsf_weights_pct",
        "thresholds_pct",
    }

    metrics = run["metrics"]
    assert _dec(metrics["hqla_total_ghs"]) > 0
    assert _dec(metrics["net_outflows_30d_ghs"]) > 0
    assert _dec(metrics["asf_total_ghs"]) > 0
    assert _dec(metrics["rsf_total_ghs"]) > 0
    _assert_ratio(metrics["lcr_pct"], metrics["hqla_total_ghs"], metrics["net_outflows_30d_ghs"])
    _assert_ratio(metrics["nsfr_pct"], metrics["asf_total_ghs"], metrics["rsf_total_ghs"])

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert set(metric_results) == {
        "lcr_pct",
        "nsfr_pct",
        "hqla_total_ghs",
        "net_outflows_30d_ghs",
        "asf_total_ghs",
        "rsf_total_ghs",
    }
    assert metric_results["lcr_pct"]["unit"] == "pct"
    assert metric_results["lcr_pct"]["status"] in {"green", "amber", "red"}
    assert Decimal(metric_results["lcr_pct"]["threshold_min"]) == Decimal("100")
    # Status must be consistent with the value it grades.
    lcr = _dec(metrics["lcr_pct"])
    if lcr >= Decimal("100"):
        assert metric_results["lcr_pct"]["status"] in {"green", "amber"}
    else:
        assert metric_results["lcr_pct"]["status"] == "red"
    assert metric_results["hqla_total_ghs"]["unit"] == "ghs"
    assert metric_results["hqla_total_ghs"]["status"] == "na"
    assert metric_results["hqla_total_ghs"]["threshold_min"] is None

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in run["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    # Every LCR/NSFR section must be populated (exact counts depend on the book).
    for section in ("hqla", "outflow", "inflow", "asf", "rsf"):
        assert sections.get(section), f"missing line items for section {section}"
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
    assert validations["lcr_above_minimum"]["severity"] == "error"

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{run['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]


def test_identical_rerun_produces_same_input_hash(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    first = _create_run(real_client, period["id"], "baseline")
    second = _create_run(real_client, period["id"], "baseline")

    assert first["id"] != second["id"]
    assert first["input_hash"] == second["input_hash"]
    assert second["status"] == "succeeded"

    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "liquidity", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 2
    assert all(item["period_label"] == period["label"] for item in body["runs"][:2])


def test_run_all_scenarios_returns_five_runs_with_consistent_statuses(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    response = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/liquidity/run-all-scenarios",
        headers=real_headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    runs = response.json()["runs"]
    assert [run["scenario_code"] for run in runs] == LIQUIDITY_SCENARIOS
    assert all(run["status"] == "succeeded" for run in runs)

    for run in runs:
        scenario = run["scenario_code"]
        metrics = run["metrics"]
        _assert_ratio(
            metrics["lcr_pct"], metrics["hqla_total_ghs"], metrics["net_outflows_30d_ghs"]
        )
        _assert_ratio(metrics["nsfr_pct"], metrics["asf_total_ghs"], metrics["rsf_total_ghs"])
        metric_results = {item["metric_code"]: item for item in run["metric_results"]}
        lcr = _dec(metrics["lcr_pct"])
        expected_status = "red" if lcr < Decimal("100") else metric_results["lcr_pct"]["status"]
        assert metric_results["lcr_pct"]["status"] == expected_status, scenario
        assert metric_results["lcr_pct"]["status"] in {"green", "amber", "red"}, scenario

    # Stress scenarios must not improve LCR over baseline (more run-off, not less).
    lcr_by_scenario = {run["scenario_code"]: _dec(run["metrics"]["lcr_pct"]) for run in runs}
    assert lcr_by_scenario["combined"] <= lcr_by_scenario["baseline"]


def test_dashboard_computes_inline_then_prefers_stored_runs(real_client: TestClient) -> None:
    period = _latest_period(real_client)

    inline = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/liquidity/dashboard", headers=real_headers()
    )
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    assert _dec(body["metrics"]["lcr_pct"]) >= 0
    assert body["metrics"]["lcr_status"] in {"green", "amber", "red"}
    assert body["hqla_composition"]
    assert body["outflows"]
    assert body["inflows"]
    assert len(body["validations"]) == 5
    trend = body["trend"]
    assert trend
    assert trend[-1]["label"] == period["label"]
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert all(point["stored"] is False for point in trend)

    run = _create_run(real_client, period["id"], "baseline")
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/liquidity/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == run["id"]
    trend = body["trend"]
    assert trend[-1]["stored"] is True


def test_missing_parameter_persists_failed_run_without_500(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)

    # Delete a required run-off parameter on the SHARED transaction so the run
    # sees it missing; the outer rollback restores it (the primary is untouched).
    real_session.info["organization_id"] = REAL_ORG_ID
    real_session.execute(
        delete(ParamLcrRunoffRate).where(
            ParamLcrRunoffRate.organization_id == REAL_ORG_ID,
            ParamLcrRunoffRate.flow_direction == "outflow",
            ParamLcrRunoffRate.category == "wholesale_operational",
        )
    )
    real_session.commit()  # savepoint release on the shared connection

    run = _create_run(real_client, period["id"], "baseline")
    assert run["status"] == "failed"
    assert run["error"]["code"] == "missing_parameter"
    assert run["error"]["details"]["category"] == "wholesale_operational"
    assert run["completed_at"] is not None
    assert run["metrics"] == {}
    assert run["metric_results"] == []
    assert run["line_items"] == []
    assert run["validations"] == []

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{run['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "failed"
    assert fetched.json()["error"]["code"] == "missing_parameter"


def test_bsd3_preview_requires_baseline_run_then_renders_rows(real_client: TestClient) -> None:
    period = _latest_period(real_client)

    blocked = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd3",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(real_client, period["id"], "baseline")
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd3",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()

    header = preview["header"]
    assert header["form_code"] == "BSD-3"
    assert header["regulator"] == "Bank of Ghana"
    assert header["bank_name"] == "Sample Bank Ltd"
    assert header["reporting_period_label"] == period["label"]
    assert header["currency"] == "GHS"
    assert header["preview_note"] == (
        "PREVIEW ONLY — This system does not file submissions with Bank of Ghana."
    )
    assert preview["run_id"] == run["id"]
    assert preview["scenario_code"] == "baseline"

    assert [row["row_code"] for row in preview["hqla_rows"]] == ["1.1", "1.2", "1.3", "1.4"]
    hqla_total = sum(Decimal(row["amount"]) for row in preview["hqla_rows"])
    assert hqla_total == _dec(run["metrics"]["hqla_total_ghs"])

    inflow_rows = preview["inflow_rows"]
    assert [row["row_code"] for row in inflow_rows] == ["6.1", "6.2", "6.3"]

    summary = {row["row_code"]: row for row in preview["summary_rows"]}
    assert _dec(summary["3.0"]["value"]) == _dec(run["metrics"]["hqla_total_ghs"])
    assert _four_dp(summary["9.0"]["value"]) == _four_dp(run["metrics"]["lcr_pct"])
    assert summary["9.0"]["unit"] == "pct"

    nsfr = preview["nsfr"]
    assert _dec(nsfr["asf_total"]["value"]) == _dec(run["metrics"]["asf_total_ghs"])
    assert _dec(nsfr["rsf_total"]["value"]) == _dec(run["metrics"]["rsf_total_ghs"])
    assert _four_dp(nsfr["nsfr_ratio"]["value"]) == _four_dp(run["metrics"]["nsfr_pct"])
    assert len(preview["validations"]) == 5


def test_invalid_module_and_scenario_are_rejected_with_422(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    invalid_module = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "forecast",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert invalid_module.status_code == 422

    capital_scenario_on_liquidity = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "mild",
        },
    )
    assert capital_scenario_on_liquidity.status_code == 422

    invalid_scenario = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "severe",
        },
    )
    assert invalid_scenario.status_code == 422


def test_unknown_bank_and_period_return_404(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    assert (
        real_client.post(
            f"/api/v1/banks/{uuid4()}/regulatory-runs",
            headers=real_headers(),
            json={
                "module": "liquidity",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            json={
                "module": "liquidity",
                "reporting_period_id": str(uuid4()),
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{uuid4()}", headers=real_headers()
        ).status_code
        == 404
    )


def test_regulatory_liquidity_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    run = _create_run(real_client, period["id"], "baseline")

    other = other_headers()
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            json={
                "module": "liquidity",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/liquidity/run-all-scenarios",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{run['id']}", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/liquidity/dashboard", headers=other
        ).status_code
        == 404
    )
