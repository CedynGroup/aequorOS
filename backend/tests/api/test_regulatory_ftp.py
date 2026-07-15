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

FTP_SCENARIOS = ["baseline", "rates_up_200", "funding_stress"]


def _seed_latest_period(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    return periods[0]


def _run_all(db_client: TestClient, period_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/run-all-scenarios",
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


def _delete_ftp_scenario_shock(scenario_code: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(ParamStressShock).where(
                ParamStressShock.organization_id == DEMO_ORG_ID,
                ParamStressShock.jurisdiction_code == JURISDICTION_CODE,
                ParamStressShock.module == "ftp",
                ParamStressShock.scenario_code == scenario_code,
            )
        )
        session.commit()
    finally:
        session.close()


def test_run_all_ftp_scenarios_persists_three_runs_with_golden_metrics(  # noqa: PLR0915
    db_client: TestClient,
) -> None:
    period = _seed_latest_period(db_client)
    batch = _run_all(db_client, period["id"])

    runs = batch["runs"]
    assert [run["scenario_code"] for run in runs] == FTP_SCENARIOS
    assert all(run["status"] == "succeeded" for run in runs)
    assert all(run["module"] == "ftp" for run in runs)
    assert all(run["engine_version"] == "regulatory-ftp-v1.0.0" for run in runs)
    assert all(len(run["input_hash"]) == 64 for run in runs)
    # scenario_code is part of the snapshot, so each run gets a distinct hash.
    assert len({run["input_hash"] for run in runs}) == 3

    baseline = runs[0]
    snapshot = baseline["inputs"]
    assert snapshot["module"] == "ftp"
    assert snapshot["as_of_date"] == "2026-03-31"
    assert {fact["fact_group"] for fact in snapshot["facts"]} == {
        "ftp_curve_point",
        "ftp_product",
        "ftp_branch",
        "ftp_nmd",
    }
    assert len(snapshot["facts"]) == 26
    assert set(snapshot["parameters"]) == {"thresholds", "stress_overlays_bps"}

    metrics = baseline["metrics"]
    assert Decimal(metrics["portfolio_nim_pct"]) == Decimal("7.195945")
    assert Decimal(metrics["weighted_asset_yield_pct"]) == Decimal("0.357696")
    assert Decimal(metrics["weighted_funding_credit_pct"]) == Decimal("14.702299")
    assert int(metrics["products_below_min_margin"]) == 2
    assert metrics["below_min_products"] == ["gov_securities_3y", "mortgage_10y"]
    assert int(metrics["total_products"]) == 10
    assert Decimal(metrics["total_branch_contribution_ghs"]) == Decimal("224755297.8000")
    assert Decimal(metrics["nmd_core_pct"]) == Decimal("66.500000")
    assert metrics["nmd_within_policy"] is True
    assert Decimal(metrics["total_balance_ghs"]) == Decimal("3650000000.0000")
    assert len(metrics["curve"]) == 8
    assert len(metrics["products"]) == 10
    assert len(metrics["branches"]) == 6
    assert len(metrics["nmd_segments"]) == 2
    # Branches are ranked by FTP net contribution; deposit-rich Accra leads.
    assert [branch["branch"] for branch in metrics["branches"]] == [
        "accra_main",
        "kumasi",
        "tema",
        "takoradi",
        "tamale",
        "cape_coast",
    ]

    # The rate-up and funding-stress overlays reprice the book to distinct NIMs.
    assert Decimal(runs[1]["metrics"]["portfolio_nim_pct"]) == Decimal("7.102795")
    assert int(runs[1]["metrics"]["products_below_min_margin"]) == 3
    assert Decimal(runs[2]["metrics"]["portfolio_nim_pct"]) == Decimal("7.149370")
    assert int(runs[2]["metrics"]["products_below_min_margin"]) == 3

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    assert {
        "portfolio_nim_pct",
        "weighted_asset_yield_pct",
        "weighted_funding_credit_pct",
        "nmd_core_pct",
        "total_branch_contribution_ghs",
    } == set(metric_results)
    assert metric_results["portfolio_nim_pct"]["unit"] == "pct"
    assert metric_results["nmd_core_pct"]["status"] == "green"
    assert Decimal(metric_results["nmd_core_pct"]["threshold_min"]) == Decimal("60")
    assert metric_results["total_branch_contribution_ghs"]["unit"] == "ghs"

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["ftp_curve"]) == 8
    assert len(sections["ftp_product"]) == 10
    assert len(sections["ftp_branch"]) == 6
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    validations = {item["rule_code"]: item for item in baseline["validations"]}
    assert set(validations) == {
        "all_products_above_min_margin",
        "nmd_core_within_policy",
        "curve_arithmetic_consistent",
        "curve_within_premium_limits",
    }
    # Two products price below the floor, so the margin screen fails as a warning.
    assert validations["all_products_above_min_margin"]["passed"] is False
    assert validations["all_products_above_min_margin"]["severity"] == "warning"
    assert validations["nmd_core_within_policy"]["passed"] is True
    assert validations["nmd_core_within_policy"]["severity"] == "info"
    assert validations["curve_arithmetic_consistent"]["passed"] is True
    assert validations["curve_arithmetic_consistent"]["severity"] == "error"
    assert validations["curve_within_premium_limits"]["passed"] is True

    fetched = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{baseline['id']}", headers=headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_ftp_input_hash_is_scoped_to_ftp_facts(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    first = _run_all(db_client, period["id"])["runs"][0]

    # Editing an FX position touches a different fact group; the FTP hash must not move.
    _set_fact_amount(period["id"], "fx_position", "USD", "31000000")
    second = _run_all(db_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing an IRR position likewise leaves the FTP hash untouched.
    _set_fact_amount(period["id"], "irr_position", "corp_loans_fixed", "241000000")
    third = _run_all(db_client, period["id"])["runs"][0]
    assert third["input_hash"] == first["input_hash"]

    # Editing an FTP product must change it.
    _set_fact_amount(period["id"], "ftp_product", "corporate_5y", "561000000")
    fourth = _run_all(db_client, period["id"])["runs"][0]
    assert fourth["input_hash"] != first["input_hash"]
    assert fourth["status"] == "succeeded"

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "ftp", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 4


def test_ftp_dashboard_computes_inline_then_prefers_stored_runs(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)

    inline = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/dashboard", headers=headers())
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    assert Decimal(metrics["portfolio_nim_pct"]) == Decimal("7.195945")
    assert metrics["nmd_core_status"] == "green"
    assert int(metrics["products_below_min_margin"]) == 2
    assert len(body["curve"]) == 8
    assert len(body["products"]) == 10
    assert len(body["branches"]) == 6
    assert len(body["nmd_segments"]) == 2
    assert len(body["validations"]) == 4

    trend = body["trend"]
    assert len(trend) == 12
    assert [point["label"] for point in trend][:2] == ["2025-04", "2025-05"]
    assert trend[-1]["label"] == "2026-03"
    assert all(point["stored"] is False for point in trend)

    _run_all(db_client, period["id"])
    stored = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/dashboard",
        headers=headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] is not None
    assert Decimal(body["metrics"]["total_branch_contribution_ghs"]) == Decimal("224755297.8000")
    trend = body["trend"]
    assert len(trend) == 12
    assert trend[-1]["stored"] is True
    assert all(point["stored"] is False for point in trend[:-1])


def test_missing_ftp_shock_persists_failed_runs_without_500(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _delete_ftp_scenario_shock("rates_up_200")

    batch = _run_all(db_client, period["id"])
    runs = batch["runs"]
    # Every run needs the full stress-overlay parameter set, so a missing scenario
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert runs[0]["error"]["code"] == "missing_parameter"

    dashboard = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/dashboard",
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
            f"/api/v1/banks/{uuid4()}/ftp/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/run-all-scenarios",
            headers=headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/ftp/dashboard", headers=headers()).status_code
        == 404
    )


def test_regulatory_ftp_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    period = _seed_latest_period(db_client)
    _run_all(db_client, period["id"])

    org2 = headers(ORG_2)
    assert (
        db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/run-all-scenarios",
            headers=org2,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/ftp/dashboard", headers=org2).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
            headers=org2,
            params={"module": "ftp"},
        ).status_code
        == 404
    )

    listed = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=headers(),
        params={"module": "ftp"},
    ).json()
    assert listed["total"] == 3
