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

FX_SCENARIOS = ["baseline", "mild_depreciation", "severe_depreciation", "cedi_crisis"]


def _seed_latest_period(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _run_all(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def _delete_fx_scenario_shock(scenario_code: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(ParamStressShock).where(
                ParamStressShock.organization_id == DEMO_ORG_ID,
                ParamStressShock.jurisdiction_code == JURISDICTION_CODE,
                ParamStressShock.module == "fx",
                ParamStressShock.scenario_code == scenario_code,
            )
        )
        session.commit()
    finally:
        session.close()


def test_run_all_fx_scenarios_persists_four_runs_with_golden_metrics(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    batch = _run_all(db_client, period["id"])

    runs = batch["runs"]
    assert [run["scenario_code"] for run in runs] == FX_SCENARIOS
    assert all(run["status"] == "succeeded" for run in runs)
    assert all(run["module"] == "fx" for run in runs)
    assert all(run["engine_version"] == "regulatory-fx-v1.0.0" for run in runs)
    assert all(len(run["input_hash"]) == 64 for run in runs)
    # scenario_code is part of the snapshot, so each run gets a distinct hash.
    assert len({run["input_hash"] for run in runs}) == 4

    baseline = runs[0]
    snapshot = baseline["inputs"]
    assert snapshot["module"] == "fx"
    assert snapshot["as_of_date"] == "2026-03-31"
    assert {fact["fact_group"] for fact in snapshot["facts"]} == {
        "fx_position",
        "fx_return_history",
        "fx_hedge",
    }
    assert len(snapshot["facts"]) == 15
    assert set(snapshot["parameters"]) == {
        "limits_pct",
        "hedge_bands_pct",
        "depreciation_shocks_pct",
        "crisis",
    }

    metrics = baseline["metrics"]
    assert Decimal(metrics["nop_ghs"]) == Decimal("45000000")
    assert Decimal(metrics["sum_long_ghs"]) == Decimal("45000000")
    assert Decimal(metrics["sum_short_ghs"]) == Decimal("12000000")
    assert Decimal(metrics["nop_pct_tier1"]) == Decimal("16.071429")
    assert metrics["single_ccy_max_currency"] == "USD"
    assert Decimal(metrics["single_ccy_max_pct"]) == Decimal("10.714286")
    assert Decimal(metrics["tier1_ghs"]) == Decimal("280000000")
    assert Decimal(metrics["var_99_1d_ghs"]) == Decimal("731231")
    assert Decimal(metrics["stressed_var_ghs"]) == Decimal("1427292")
    assert Decimal(metrics["diversification_benefit_ghs"]) == Decimal("574154")
    assert Decimal(metrics["standalone_var_total_ghs"]) == Decimal("1305385")
    assert int(metrics["var_observations"]) == 250
    assert int(metrics["hedge_effective_count"]) == 2
    assert int(metrics["hedge_total_count"]) == 3
    assert Decimal(metrics["hedge_aggregate_mtm_ghs"]) == Decimal("3700000")
    assert len(metrics["currencies"]) == 6
    assert len(metrics["standalone_vars"]) == 6
    assert len(metrics["hedges"]) == 3

    scenarios = {item["scenario_code"]: item for item in metrics["nop_by_scenario"]}
    assert set(scenarios) == set(FX_SCENARIOS)
    assert Decimal(scenarios["severe_depreciation"]["nop_ghs"]) == Decimal("54000000")
    assert scenarios["severe_depreciation"]["within_aggregate_limit"] is True
    # The 30% cedi-crisis shock pushes the aggregate NOP above the 20% limit.
    assert Decimal(scenarios["cedi_crisis"]["nop_ghs"]) == Decimal("58500000")
    assert scenarios["cedi_crisis"]["within_aggregate_limit"] is False

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    assert {
        "nop_pct_tier1",
        "single_ccy_max_pct",
        "nop_ghs",
        "var_99_1d_ghs",
        "stressed_var_ghs",
        "diversification_benefit_ghs",
    } == set(metric_results)
    assert metric_results["nop_pct_tier1"]["unit"] == "pct"
    assert metric_results["nop_pct_tier1"]["status"] == "amber"
    assert Decimal(metric_results["nop_pct_tier1"]["threshold_min"]) == Decimal("20")
    assert metric_results["single_ccy_max_pct"]["status"] == "red"
    assert Decimal(metric_results["single_ccy_max_pct"]["threshold_min"]) == Decimal("10")
    assert metric_results["var_99_1d_ghs"]["unit"] == "ghs"

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["fx_position"]) == 6
    # portfolio_var + diversification + 6 standalone + stressed_var = 9.
    assert len(sections["fx_var"]) == 9
    assert len(sections["fx_hedge"]) == 3
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    validations = {item["rule_code"]: item for item in baseline["validations"]}
    assert set(validations) == {
        "nop_within_aggregate_limit",
        "single_ccy_within_limit",
        "hedges_effective",
        "stressed_var_disclosed",
    }
    assert validations["nop_within_aggregate_limit"]["passed"] is True
    assert validations["nop_within_aggregate_limit"]["severity"] == "error"
    # USD breaches the 10% single-currency limit.
    assert validations["single_ccy_within_limit"]["passed"] is False
    assert validations["single_ccy_within_limit"]["severity"] == "error"
    assert validations["hedges_effective"]["passed"] is False
    assert validations["hedges_effective"]["severity"] == "warning"
    assert validations["stressed_var_disclosed"]["passed"] is True
    assert validations["stressed_var_disclosed"]["severity"] == "info"

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{baseline['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_fx_input_hash_is_scoped_to_fx_facts(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _run_all(db_client, period["id"])["runs"][0]

    # Editing an IRR position touches a different fact group; the FX hash must not move.
    _set_fact_amount(period["id"], "irr_position", "corp_loans_fixed", "241000000")
    second = _run_all(db_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing a capital-component fact changes Tier 1 (an external reference) but
    # must NOT disturb the FX input hash.
    _set_fact_amount(period["id"], "capital_component", "retained_earnings", "96000000")
    third = _run_all(db_client, period["id"])["runs"][0]
    assert third["input_hash"] == first["input_hash"]

    # Editing an FX position must change it.
    _set_fact_amount(period["id"], "fx_position", "USD", "31000000")
    fourth = _run_all(db_client, period["id"])["runs"][0]
    assert fourth["input_hash"] != first["input_hash"]
    assert fourth["status"] == "succeeded"

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "fx", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 4


def test_fx_dashboard_computes_inline_then_prefers_stored_runs(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    inline = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard", headers=headers())
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    assert Decimal(metrics["nop_ghs"]) == Decimal("45000000")
    assert Decimal(metrics["nop_pct_tier1"]) == Decimal("16.071429")
    assert metrics["nop_status"] == "amber"
    assert metrics["single_ccy_status"] == "red"
    assert Decimal(metrics["var_99_1d_ghs"]) == Decimal("731231")
    assert len(body["positions"]) == 6
    assert len(body["standalone_vars"]) == 6
    assert len(body["hedges"]) == 3
    assert len(body["scenarios"]) == 4
    assert len(body["validations"]) == 4

    trend = body["trend"]
    assert len(trend) == 12
    assert [point["label"] for point in trend][:2] == ["2025-04", "2025-05"]
    assert trend[-1]["label"] == "2026-03"
    assert all(point["stored"] is False for point in trend)

    _run_all(db_client, period["id"])
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] is not None
    assert Decimal(body["metrics"]["stressed_var_ghs"]) == Decimal("1427292")
    trend = body["trend"]
    assert len(trend) == 12
    assert trend[-1]["stored"] is True
    assert all(point["stored"] is False for point in trend[:-1])


def test_missing_fx_shock_persists_failed_runs_without_500(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _delete_fx_scenario_shock("severe_depreciation")

    batch = _run_all(db_client, period["id"])
    runs = batch["runs"]
    # Every run needs the full depreciation-scenario set, so a missing scenario
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert runs[0]["error"]["code"] == "missing_parameter"

    dashboard = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
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
            f"/api/v1/banks/{uuid4()}/fx/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/fx/dashboard", headers=headers()).status_code == 404
    )


def test_regulatory_fx_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_all(db_client, period["id"])

    org2 = headers(ORG_2)
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            params={"module": "fx"},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "fx"},
    ).json()
    assert listed["total"] == 4
