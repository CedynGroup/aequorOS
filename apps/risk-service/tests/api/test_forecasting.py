from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers

FOUR_DP = Decimal("0.0001")
ASSUMPTION_KEYS = {
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
}
FORECAST_FACT_GROUPS = {
    "balance_sheet",
    "capital_component",
    "lcr_inflow",
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


def _create_forecast_run(
    db_client: TestClient,
    period_id: str,
    scenario_code: str,
    assumptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reporting_period_id": period_id, "scenario_code": scenario_code}
    if assumptions is not None:
        payload["assumptions"] = assumptions
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs", headers=headers(), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def test_list_forecast_scenarios_returns_presets_and_defaults(db_client: TestClient) -> None:
    _seed_latest_period(db_client)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/scenarios", headers=headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == str(SAMPLE_BANK_ID)
    scenarios = {item["code"]: item["assumptions"] for item in body["scenarios"]}
    assert set(scenarios) == {"base", "adverse", "severely_adverse"}
    for assumptions in scenarios.values():
        assert set(assumptions) == ASSUMPTION_KEYS
    assert _dec(scenarios["base"]["loan_growth_pct"]) == Decimal("18")
    assert _dec(scenarios["base"]["deposit_growth_pct"]) == Decimal("16")
    assert _dec(scenarios["adverse"]["fx_depreciation_pct"]) == Decimal("15")
    assert _dec(scenarios["severely_adverse"]["loan_growth_pct"]) == Decimal("-2")
    defaults = body["defaults"]
    assert _dec(defaults["fee_income_pct_assets"]) == Decimal("1.2")
    assert _dec(defaults["tax_rate_pct"]) == Decimal("25")
    assert _dec(defaults["securities_shift_pp"]) == Decimal("0")


def test_create_base_forecast_run_persists_projection_and_outputs(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    run = _create_forecast_run(db_client, period["id"], "base")

    assert run["status"] == "succeeded"
    assert run["module"] == "forecast"
    assert run["scenario_code"] == "base"
    assert run["engine_version"] == "regulatory-forecasting-v1.0.0"
    assert run["input_schema_version"] == "bank-facts-v1"
    assert run["output_schema_version"] == "forecast-projection-v1"
    assert run["error"] is None
    assert len(run["input_hash"]) == 64
    assert run["started_at"] is not None
    assert run["completed_at"] is not None

    snapshot = run["inputs"]
    assert snapshot["module"] == "forecast"
    assert snapshot["scenario_code"] == "base"
    assert snapshot["as_of_date"] == "2026-03-31"
    # 41 capital-module facts + 3 LCR inflow facts participate in the forecast.
    assert len(snapshot["facts"]) == 44
    assert {fact["fact_group"] for fact in snapshot["facts"]} == FORECAST_FACT_GROUPS
    assert snapshot["assumption_overrides"] is None
    assert _dec(snapshot["assumptions"]["loan_growth_pct"]) == Decimal("18")

    resolved = run["assumptions"]
    assert _dec(resolved["loan_growth_pct"]) == Decimal("18")
    assert _dec(resolved["deposit_growth_pct"]) == Decimal("16")
    assert _dec(resolved["fee_income_pct_assets"]) == Decimal("1.2")
    assert _dec(resolved["tax_rate_pct"]) == Decimal("25")
    assert _dec(resolved["securities_shift_pp"]) == Decimal("0")

    path = run["path"]
    assert [row["year"] for row in path] == [0, 1, 2, 3, 4, 5]
    assert path[0]["period_label"] == "2026-03"
    assert path[5]["period_label"] == "2031-03"
    # Year 0 ratios equal the standalone engine baselines (cross-module consistency).
    assert _dec(path[0]["car_pct"]) == Decimal("15.832363")
    assert _dec(path[0]["lcr_pct"]) == Decimal("147.294589")
    assert path[0]["roe_pct"] is None
    # Year 1 goldens (derived in tests/domain/test_forecasting_engine.py).
    assert _dec(path[1]["loans"]) == Decimal("1652000000")
    assert _dec(path[1]["deposits"]) == Decimal("2204000000")
    assert _dec(path[1]["securities"]) == Decimal("719200000")
    assert _dec(path[1]["nii"]) == Decimal("105388800")
    assert _dec(path[1]["net_income"]) == Decimal("40874016")
    for row in path:
        assert _dec(row["total_assets"]) == (
            _dec(row["deposits"])
            + Decimal("60000000")
            + _dec(row["borrowings_plug"])
            + _dec(row["equity"])
        )

    summary = run["summary"]
    assert _dec(summary["year5_car_pct"]) >= Decimal("10")
    assert _dec(summary["year5_lcr_pct"]) >= Decimal("100")
    assert _dec(summary["year5_nsfr_pct"]) >= Decimal("100")
    assert _dec(summary["min_car_pct"]) <= _dec(summary["year5_car_pct"])

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert set(metric_results) == {
        "avg_roe_pct",
        "year5_car_pct",
        "year5_lcr_pct",
        "year5_nsfr_pct",
    }
    assert metric_results["avg_roe_pct"]["status"] == "na"
    assert metric_results["avg_roe_pct"]["threshold_min"] is None
    assert metric_results["year5_car_pct"]["status"] == "green"
    assert _dec(metric_results["year5_car_pct"]["threshold_min"]) == Decimal("10")
    assert metric_results["year5_lcr_pct"]["status"] == "green"
    assert _dec(metric_results["year5_lcr_pct"]["threshold_min"]) == Decimal("100")
    assert metric_results["year5_nsfr_pct"]["status"] == "green"

    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == {
        "projection_balance_ties",
        "year5_car_above_minimum",
        "year5_lcr_above_minimum",
        "year5_nsfr_above_minimum",
    }
    assert all(item["passed"] is True for item in validations.values())
    assert all(item["severity"] == "error" for item in validations.values())

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs/{run['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]


def test_custom_scenario_requires_assumptions_then_resolves_partial_override(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)

    blocked = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs",
        headers=headers(),
        json={"reporting_period_id": period["id"], "scenario_code": "custom"},
    )
    assert blocked.status_code == 422

    run = _create_forecast_run(
        db_client, period["id"], "custom", assumptions={"loan_growth_pct": "10"}
    )
    assert run["status"] == "succeeded"
    assert run["scenario_code"] == "custom"
    resolved = run["assumptions"]
    # The override applies; every other key resolves from the base preset.
    assert _dec(resolved["loan_growth_pct"]) == Decimal("10")
    assert _dec(resolved["deposit_growth_pct"]) == Decimal("16")
    assert _dec(resolved["nim_pct"]) == Decimal("4.8")
    assert _dec(resolved["dividend_payout_pct"]) == Decimal("30")
    assert run["inputs"]["assumption_overrides"] == {"loan_growth_pct": "10"}
    assert _dec(run["path"][1]["loans"]) == Decimal("1540000000")  # 1400M x 1.10

    base = _create_forecast_run(db_client, period["id"], "base")
    assert base["input_hash"] != run["input_hash"]


def test_forecast_runs_list_and_get_are_module_scoped(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _create_forecast_run(db_client, period["id"], "base")
    second = _create_forecast_run(db_client, period["id"], "severely_adverse")

    listed = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs", headers=headers())
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert body["has_more"] is False
    summaries = {item["id"]: item for item in body["runs"]}
    assert set(summaries) == {first["id"], second["id"]}
    for summary in summaries.values():
        assert summary["status"] == "succeeded"
        assert summary["period_label"] == "2026-03"
        assert summary["avg_roe_pct"] is not None
        assert summary["year5_car_pct"] is not None
        assert summary["year5_lcr_pct"] is not None
        assert summary["year5_nsfr_pct"] is not None
    # Severely adverse is directionally worse on profitability than base.
    assert _dec(summaries[second["id"]]["avg_roe_pct"]) < (
        _dec(summaries[first["id"]]["avg_roe_pct"])
    )

    # The shared regulatory-runs listing can filter the new modules.
    filtered = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "forecast"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2

    # A capital run is not retrievable through the forecast-run endpoint.
    capital_run = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        json={
            "module": "capital",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    ).json()
    missing = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs/{capital_run['id']}", headers=headers()
    )
    assert missing.status_code == 404
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs/{uuid4()}", headers=headers()
        ).status_code
        == 404
    )


def test_strategic_optimizer_persists_a_run_and_returns_ranked_candidates(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/optimizer",
        headers=headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["scenario_code"] == "constrained_search"
    assert body["candidates_evaluated"] == 108
    assert 1 <= body["feasible_count"] <= 108
    assert len(body["top"]) <= 10
    roes = [_dec(candidate["summary"]["avg_roe_pct"]) for candidate in body["top"]]
    assert roes == sorted(roes, reverse=True)
    for candidate in body["top"]:
        assert candidate["feasible"] is True
        statuses = {item["constraint"]: item for item in candidate["constraint_status"]}
        assert set(statuses) == {"car", "lcr", "nsfr"}
        assert all(item["passed"] is True for item in statuses.values())
    assert set(body["binding_constraint_histogram"]) >= {"car", "lcr", "nsfr"}
    assert _dec(body["base_assumptions"]["loan_growth_pct"]) == Decimal("18")

    # The optimizer run persists under module='optimizer'.
    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "optimizer"},
    ).json()
    assert listed["total"] == 1
    assert listed["runs"][0]["id"] == body["run_id"]
    assert listed["runs"][0]["scenario_code"] == "constrained_search"
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{body['run_id']}", headers=headers()
    ).json()
    assert stored["module"] == "optimizer"
    assert stored["metrics"]["candidates_evaluated"] == 108


def test_whatif_analysis_persists_a_run_and_compares_paths(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/whatif",
        headers=headers(),
        json={"reporting_period_id": period["id"], "shock_code": "default_spike"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["shock_code"] == "default_spike"
    assert len(body["base_path"]) == 6
    assert len(body["shocked_path"]) == 6
    assert len(body["deltas"]) == 6
    # default_spike multiplies the credit-loss rate by 2.5: year-5 CAR and net
    # income both fall relative to base.
    year5 = body["year5"]
    assert _dec(year5["car_pct"]["shocked"]) < _dec(year5["car_pct"]["base"])
    assert _dec(year5["net_income"]["shocked"]) < _dec(year5["net_income"]["base"])
    assert _dec(year5["car_pct"]["delta"]) == (
        _dec(year5["car_pct"]["shocked"]) - _dec(year5["car_pct"]["base"])
    )
    assert _dec(body["shocked_assumptions"]["credit_loss_rate_pct"]) == Decimal("2.5")

    mpr = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/whatif",
        headers=headers(),
        json={"reporting_period_id": period["id"], "shock_code": "mpr_cut_200"},
    ).json()
    assert _dec(mpr["shocked_path"][5]["loans"]) > _dec(mpr["base_path"][5]["loans"])

    unknown = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/whatif",
        headers=headers(),
        json={"reporting_period_id": period["id"], "shock_code": "meteor_strike"},
    )
    assert unknown.status_code == 422

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "whatif"},
    ).json()
    assert listed["total"] == 2
    assert {run["scenario_code"] for run in listed["runs"]} == {"default_spike", "mpr_cut_200"}


def test_forecast_modules_are_not_creatable_through_create_regulatory_run(
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    for module in ("forecast", "optimizer", "whatif"):
        response = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=headers(),
            json={
                "module": module,
                "reporting_period_id": period["id"],
                "scenario_code": "base",
            },
        )
        assert response.status_code == 422, module


def test_unknown_bank_and_period_return_404(db_client: TestClient) -> None:
    _seed_latest_period(db_client)
    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/forecast/scenarios", headers=headers()).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs",
            headers=headers(),
            json={"reporting_period_id": str(uuid4()), "scenario_code": "base"},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/optimizer",
            headers=headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )


def test_forecasting_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    run = _create_forecast_run(db_client, period["id"], "base")

    org2 = headers(ORG_2)
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/scenarios", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs",
            headers=org2,
            json={"reporting_period_id": period["id"], "scenario_code": "base"},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs/{run['id']}", headers=org2
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/optimizer",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/whatif",
            headers=org2,
            json={"reporting_period_id": period["id"], "shock_code": "default_spike"},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast/runs", headers=headers()
    ).json()
    assert listed["total"] == 1
