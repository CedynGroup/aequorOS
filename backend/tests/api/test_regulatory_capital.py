"""Regulatory-capital API tests against the ACTUAL primary database.

DB-backed conversion (tests/real_data.py; docs/bog_returns/CONTRIBUTING_real_db_tests.md):
the retired sample-bank seed is gone, so these run against the real Sample Bank through
``real_client`` (opt-in via REAL_DATA_DATABASE_URL, transaction-isolated, rolled back).
The book changes as data is ingested, so every assertion is an INVARIANT, never a golden
magnitude: CAR / Tier 1 / CET1 = capital ÷ RWA (from the run's own line items), total RWA =
credit + market + operational and each equals its section, statuses and validations agree
with the active thresholds, stress paths anchor on the baseline CAR with triggers tied to
their own path, the input hash is scoped to the capital fact groups, the BSD5A preview
reconciles to the run, plus 404/422 paths and tenant isolation.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import BankFinancialFact
from app.services import regulatory_capital
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

RATIO_PCT = Decimal("0.000001")  # the capital engine's ratio quantum
GREEN_BUFFER_PP = Decimal("0.5")  # engine: green clears the minimum by 0.5pp
CAPITAL_FACT_GROUPS = {
    "balance_sheet",
    "capital_component",
    "crm_collateral",
    "ecl_exposure",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
}
CORE_CAPITAL_FACT_GROUPS = {"balance_sheet", "capital_component", "loan_exposure"}
RATIO_THRESHOLDS = {
    "car_pct": "car_min",
    "tier1_ratio_pct": "tier1_min",
    "cet1_ratio_pct": "cet1_min",
    "leverage_ratio_pct": "leverage_min",
}
RATIO_VALIDATIONS = {
    "car_pct": "car_above_minimum",
    "tier1_ratio_pct": "tier1_above_minimum",
    "cet1_ratio_pct": "cet1_above_minimum",
    "leverage_ratio_pct": "leverage_above_minimum",
}
BASELINE_VALIDATION_RULES = {
    "car_above_minimum",
    "cet1_above_minimum",
    "tier1_above_minimum",
    "leverage_above_minimum",
    "tier2_gp_cap_applied",
}
STRESS_SCENARIOS = ["mild", "moderate", "severe"]
TRIGGER_CODES = ["early_warning", "breach", "critical"]


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _ratio(numerator: Any, denominator: Any) -> Decimal:
    """The engine's own ratio arithmetic: numerator / denominator × 100 at 6 dp."""
    return (_dec(numerator) / _dec(denominator) * Decimal("100")).quantize(
        RATIO_PCT, rounding=ROUND_HALF_UP
    )


def _expected_status(value: Any, minimum: Any) -> str:
    """Mirror of ``classify_capital_ratio``: green ≥ min + 0.5pp, amber ≥ min, else red."""
    if _dec(value) >= _dec(minimum) + GREEN_BUFFER_PP:
        return "green"
    if _dec(value) >= _dec(minimum):
        return "amber"
    return "red"


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _latest_period(client: TestClient) -> dict[str, Any]:
    return _periods(client)[0]


def _period_without_stored_baseline(client: TestClient) -> dict[str, Any]:
    """The most recent period with NO succeeded baseline capital run on the primary
    (the dashboard/preview inline paths only exist for such a period)."""
    stored: set[str] = set()
    offset = 0
    while True:
        listed = client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            params={
                "module": "capital",
                "scenario_code": "baseline",
                "limit": 100,
                "offset": offset,
            },
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        stored.update(
            run["reporting_period_id"] for run in body["runs"] if run["status"] == "succeeded"
        )
        if not body["has_more"]:
            break
        offset += 100
    for period in _periods(client):
        if period["id"] not in stored:
            return period
    raise AssertionError("every reporting period already carries a stored baseline capital run")


def _create_run(client: TestClient, period_id: str, scenario_code: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "capital",
            "reporting_period_id": period_id,
            "scenario_code": scenario_code,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sections(run: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    sections: dict[str, list[dict[str, Any]]] = {}
    for item in run["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    return sections


def _sum_weighted(lines: list[dict[str, Any]], key: str = "weighted_amount") -> Decimal:
    return sum((_dec(line[key]) for line in lines), Decimal("0"))


def _row_codes(rows: list[dict[str, Any]], prefix: str) -> list[str]:
    """The BSD row codes a section of ``len(rows)`` rows must carry: ``prefix.1 … prefix.n``."""
    return [f"{prefix}.{index}" for index in range(1, len(rows) + 1)]


def _capital_tiers(component_lines: list[dict[str, Any]]) -> tuple[Decimal, Decimal, Decimal]:
    """CET1 / AT1 / Tier 2 totals from the run's own capital-component lines."""
    cet1 = _sum_weighted(
        [line for line in component_lines if line["line_code"].startswith("cet1:")]
    )
    at1 = _sum_weighted([line for line in component_lines if line["line_code"].startswith("at1:")])
    tier2 = _sum_weighted([line for line in component_lines if line["line_code"].startswith("t2:")])
    return cet1, at1, tier2


def _bump_one_fact(session: Session, period_id: str, fact_group: str) -> None:
    """Change ONE real fact of ``fact_group`` for the period on the shared, rolled-back
    transaction (the primary is never mutated) — the value-based hash must react only when
    the group belongs to the module's snapshot."""
    session.info["organization_id"] = REAL_ORG_ID
    fact = session.scalars(
        select(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == REAL_ORG_ID,
            BankFinancialFact.bank_id == REAL_BANK_ID,
            BankFinancialFact.reporting_period_id == UUID(period_id),
            BankFinancialFact.fact_group == fact_group,
        )
        .order_by(BankFinancialFact.category)
        .limit(1)
    ).one_or_none()
    assert fact is not None, f"the real period must carry a {fact_group} fact"
    session.execute(
        update(BankFinancialFact)
        .where(BankFinancialFact.id == fact.id)
        .values(amount=Decimal(str(fact.amount)) + Decimal("1"))
    )
    session.commit()  # savepoint release on the shared connection


def _assert_capital_metrics_consistent(run: dict[str, Any]) -> None:
    """The metric block must reconcile to the run's own line items and thresholds."""
    metrics = run["metrics"]
    sections = _sections(run)
    thresholds = run["inputs"]["parameters"]["thresholds_pct"]

    total_rwa = _dec(metrics["total_rwa_ghs"])
    credit_rwa = _dec(metrics["credit_rwa_ghs"])
    market_rwa = _dec(metrics["market_rwa_ghs"])
    operational_rwa = _dec(metrics["operational_rwa_ghs"])
    assert total_rwa > 0
    assert credit_rwa >= 0 and market_rwa >= 0 and operational_rwa >= 0
    assert total_rwa == credit_rwa + market_rwa + operational_rwa

    # Every RWA section reconciles to its headline figure.
    for section in ("credit_rwa", "market_rwa", "operational_rwa", "capital_component", "ratio"):
        assert sections.get(section), f"missing line items for section {section}"
    assert _sum_weighted(sections["credit_rwa"]) == credit_rwa
    market_lines = {line["line_code"]: line for line in sections["market_rwa"]}
    assert _dec(market_lines["fx_rwa"]["weighted_amount"]) == market_rwa
    operational_lines = {line["line_code"]: line for line in sections["operational_rwa"]}
    assert _dec(operational_lines["operational_rwa"]["weighted_amount"]) == operational_rwa

    # Capital = CET1 + AT1 + Tier 2 from the component lines; ratios = capital ÷ RWA.
    cet1, at1, tier2 = _capital_tiers(sections["capital_component"])
    total_capital = _dec(metrics["total_capital_ghs"])
    assert total_capital == cet1 + at1 + tier2
    assert _dec(metrics["car_pct"]) == _ratio(total_capital, total_rwa)
    assert _dec(metrics["tier1_ratio_pct"]) == _ratio(cet1 + at1, total_rwa)
    assert _dec(metrics["cet1_ratio_pct"]) == _ratio(cet1, total_rwa)
    # Ratio lines store denominator (exposure) × ratio (rate) = numerator (weighted).
    ratio_lines = {line["line_code"]: line for line in sections["ratio"]}
    assert set(ratio_lines) == {"cet1_ratio", "tier1_ratio", "car", "leverage_ratio"}
    assert _dec(ratio_lines["car"]["exposure_amount"]) == total_rwa
    assert _dec(ratio_lines["car"]["weighted_amount"]) == total_capital
    assert _dec(ratio_lines["car"]["rate_pct"]) == _dec(metrics["car_pct"])
    leverage = ratio_lines["leverage_ratio"]
    assert _dec(leverage["exposure_amount"]) > 0
    assert _dec(leverage["weighted_amount"]) == cet1 + at1
    assert _dec(metrics["leverage_ratio_pct"]) == _ratio(cet1 + at1, leverage["exposure_amount"])

    # Metric statuses and validations agree with the active thresholds.
    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    validations = {item["rule_code"]: item for item in run["validations"]}
    for code, threshold_code in RATIO_THRESHOLDS.items():
        result = metric_results[code]
        assert result["unit"] == "pct"
        assert _dec(result["threshold_min"]) == _dec(thresholds[threshold_code])
        assert _dec(result["metric_value"]) == _dec(metrics[code])
        assert result["status"] == _expected_status(metrics[code], thresholds[threshold_code])
        validation = validations[RATIO_VALIDATIONS[code]]
        assert validation["severity"] == "error"
        assert validation["passed"] is (_dec(metrics[code]) >= _dec(thresholds[threshold_code]))
    assert validations["tier2_gp_cap_applied"]["severity"] == "info"
    assert validations["tier2_gp_cap_applied"]["passed"] is True
    positions = [item["position"] for item in run["line_items"]]
    assert positions == sorted(positions)


def test_create_baseline_capital_run_persists_snapshot_metrics_and_outputs(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    run = _create_run(real_client, period["id"], "baseline")

    assert run["status"] == "succeeded"
    assert run["module"] == "capital"
    assert run["scenario_code"] == "baseline"
    # Resolved, not restated — see the liquidity peer (re-audit D-5).
    assert run["engine_version"] == regulatory_capital.ENGINE_VERSION
    assert run["input_schema_version"] == "bank-facts-v2"
    assert run["output_schema_version"] == "capital-metrics-v1"
    assert run["started_at"] is not None
    assert run["completed_at"] is not None
    assert run["error"] is None
    assert len(run["input_hash"]) == 64

    snapshot = run["inputs"]
    assert snapshot["schema_version"] == "bank-facts-v2"
    assert snapshot["module"] == "capital"
    assert snapshot["scenario_code"] == "baseline"
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    # Only capital fact groups participate (the value-based hash is scoped to them).
    assert snapshot["facts"], "baseline snapshot must carry capital facts"
    groups = {fact["fact_group"] for fact in snapshot["facts"]}
    assert groups <= CAPITAL_FACT_GROUPS
    assert groups >= CORE_CAPITAL_FACT_GROUPS
    assert snapshot["shocks"] == {}
    assert set(snapshot["parameters"]) == {"risk_weights_pct", "thresholds_pct"}
    assert Decimal(snapshot["parameters"]["risk_weights_pct"]["RW100"]) == Decimal("100")
    assert set(RATIO_THRESHOLDS.values()) <= set(snapshot["parameters"]["thresholds_pct"])
    assert "rwa_multiplier" in snapshot["parameters"]["thresholds_pct"]

    metrics = run["metrics"]
    assert "stress_path" not in metrics
    assert "triggers" not in metrics
    _assert_capital_metrics_consistent(run)

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert set(metric_results) == set(RATIO_THRESHOLDS)
    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == BASELINE_VALIDATION_RULES
    gp_message = validations["tier2_gp_cap_applied"]["message"]
    assert ("did not bind" in gp_message) or ("bound" in gp_message)

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{run['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]
    assert fetched.json()["metrics"] == run["metrics"]


def test_capital_input_hash_is_scoped_to_capital_fact_groups(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    before = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "capital", "scenario_code": "baseline"},
    ).json()["total"]
    first = _create_run(real_client, period["id"], "baseline")

    # Editing a liquidity-only fact must not disturb the capital input hash.
    _bump_one_fact(real_session, period["id"], "lcr_inflow")
    second = _create_run(real_client, period["id"], "baseline")
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing a capital fact must change it (value-based, id-independent hash).
    _bump_one_fact(real_session, period["id"], "loan_exposure")
    third = _create_run(real_client, period["id"], "baseline")
    assert third["input_hash"] != first["input_hash"]
    assert third["status"] == "succeeded"

    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "capital", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == before + 3
    assert {run["id"] for run in listed.json()["runs"][:3]} == {
        first["id"],
        second["id"],
        third["id"],
    }


def test_run_all_capital_scenarios_returns_four_runs_with_stress_outputs(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    response = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/capital/run-all-scenarios",
        headers=real_headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    runs = response.json()["runs"]
    assert [run["scenario_code"] for run in runs] == ["baseline", *STRESS_SCENARIOS]
    assert all(run["status"] == "succeeded" for run in runs)
    baseline = runs[0]
    assert "stress_path" not in baseline["metrics"]
    baseline_car = _dec(baseline["metrics"]["car_pct"])
    thresholds = baseline["inputs"]["parameters"]["thresholds_pct"]
    car_min = _dec(thresholds["car_min"])
    early_warning = _dec(thresholds["car_early_warning"])
    critical = _dec(thresholds["car_critical"])
    assert critical <= car_min <= early_warning
    # scenario_code is part of the snapshot, so each run gets a distinct hash.
    assert len({run["input_hash"] for run in runs}) == 4

    end_car: dict[str, Decimal] = {}
    for run in runs[1:]:
        scenario = run["scenario_code"]
        assert run["inputs"]["shocks"], scenario
        _assert_capital_metrics_consistent(run)
        stress_path = run["metrics"]["stress_path"]
        assert [row["quarter"] for row in stress_path] == [0, 1, 2, 3, 4], scenario
        # Q0 is the unstressed as-of position: it must anchor on the baseline CAR.
        assert _dec(stress_path[0]["car"]) == baseline_car, scenario
        for row in stress_path:
            total_rwa = _dec(row["total_rwa"])
            assert total_rwa == (
                _dec(row["credit_rwa"]) + _dec(row["market_rwa"]) + _dec(row["operational_rwa"])
            ), scenario
            assert _dec(row["car"]) == _ratio(row["total_capital"], total_rwa), scenario
            assert _dec(row["tier1_ratio"]) == _ratio(row["tier1_capital"], total_rwa), scenario
            assert _dec(row["cet1_ratio"]) == _ratio(row["cet1_capital"], total_rwa), scenario
        end_car[scenario] = _dec(stress_path[-1]["car"])

        triggers = {item["code"]: item for item in run["metrics"]["triggers"]}
        assert list(triggers) == TRIGGER_CODES, scenario
        assert _dec(triggers["early_warning"]["threshold_pct"]) == early_warning
        assert _dec(triggers["breach"]["threshold_pct"]) == car_min
        assert _dec(triggers["critical"]["threshold_pct"]) == critical
        validations = {item["rule_code"]: item for item in run["validations"]}
        for code, trigger in triggers.items():
            # A trigger fires in the FIRST quarter its own CAR path dips below the threshold.
            first_below = next(
                (
                    row["quarter"]
                    for row in stress_path
                    if _dec(row["car"]) < _dec(trigger["threshold_pct"])
                ),
                None,
            )
            assert trigger["fired"] is (first_below is not None), (scenario, code)
            assert trigger["first_quarter"] == first_below, (scenario, code)
            validation = validations[f"capital_trigger_{code}"]
            assert validation["passed"] is (not trigger["fired"]), (scenario, code)
            assert validation["severity"] == ("warning" if code == "early_warning" else "error"), (
                scenario,
                code,
            )
        # Thresholds nest, so the triggers do too: critical ⇒ breach ⇒ early warning.
        if triggers["critical"]["fired"]:
            assert triggers["breach"]["fired"]
        if triggers["breach"]["fired"]:
            assert triggers["early_warning"]["fired"]

        # A stress run seals NO end-state capital ratio as a metric result. Such a
        # row carries a threshold and a compliance status, and the STRESS-PACK
        # copies both verbatim into a filed return - a compliance verdict on a
        # post-stress ratio that no BoG instrument requires to meet the minimum.
        # The end-state figure itself is not lost: it is the last quarter of the
        # stored path, which the pack tabulates, and it is asserted here from the
        # same source the pack reads.
        metric_results = {item["metric_code"]: item for item in run["metric_results"]}
        assert "car_pct_end" not in metric_results, scenario
        assert set(metric_results) == {
            "car_pct",
            "tier1_ratio_pct",
            "cet1_ratio_pct",
            "leverage_ratio_pct",
        }, scenario
        # The CAR row this run DOES seal is the as-of ratio, which is what the
        # Capital Requirements Directive minimum binds - Q0 of the same path.
        assert _dec(metric_results["car_pct"]["metric_value"]) == baseline_car, scenario

    # The severe scenario cannot leave the bank better capitalised than the mild one.
    assert end_car["severe"] <= end_car["mild"]


def test_capital_dashboard_computes_inline_then_prefers_stored_runs(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    latest = _latest_period(real_client)
    period = _period_without_stored_baseline(real_client)

    inline = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/capital/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    total_rwa = _dec(metrics["total_rwa_ghs"])
    assert total_rwa > 0
    assert _dec(metrics["car_pct"]) == _ratio(metrics["total_capital_ghs"], total_rwa)
    buffers = body["buffers"]
    assert _dec(buffers["car_critical_pct"]) <= _dec(buffers["car_min_pct"])
    assert _dec(buffers["car_min_pct"]) <= _dec(buffers["car_early_warning_pct"])
    assert buffers["car_early_warning_label"] == "Early warning / conservation buffer floor"
    assert _dec(buffers["current_car_pct"]) == _dec(metrics["car_pct"])
    assert _dec(buffers["headroom_pp"]) == (
        _dec(metrics["car_pct"]) - _dec(buffers["car_min_pct"])
    ).quantize(RATIO_PCT, rounding=ROUND_HALF_UP)
    assert metrics["car_status"] == _expected_status(metrics["car_pct"], buffers["car_min_pct"])
    for status_key in ("car_status", "tier1_status", "cet1_status", "leverage_status"):
        assert metrics[status_key] in {"green", "amber", "red"}
    # NEW-53. The buffers block is the ONE authority for every capital floor on
    # this payload, including the Basel sub-tiers: a consumer must never fall
    # back to a stored run's threshold_min, which is absent before the bank's
    # first official run (`latest_run_id` is None on this very path). A bank
    # cannot compute these ratios without the floors, so they are always present
    # here, they are the floors the statuses were classified against, and they
    # are the floors the validation messages cite.
    for metric_code, buffer_key, status_key in (
        ("tier1_ratio_pct", "tier1_min_pct", "tier1_status"),
        ("cet1_ratio_pct", "cet1_min_pct", "cet1_status"),
        ("leverage_ratio_pct", "leverage_min_pct", "leverage_status"),
    ):
        floor = buffers[buffer_key]
        assert floor is not None, buffer_key
        assert metrics[status_key] == _expected_status(metrics[metric_code], floor), metric_code

    composition = body["rwa_composition"]
    assert _dec(composition["total_rwa_ghs"]) == total_rwa
    assert _dec(composition["total_rwa_ghs"]) == (
        _dec(composition["credit_rwa_ghs"])
        + _dec(composition["market_rwa_ghs"])
        + _dec(composition["operational_rwa_ghs"])
    )
    assert composition["credit_lines"]
    assert _sum_weighted(composition["credit_lines"]) == _dec(composition["credit_rwa_ghs"])

    structure = body["capital_structure"]
    assert structure["cet1_components"]
    cet1 = _sum_weighted(structure["cet1_components"]) + _sum_weighted(structure["cet1_deductions"])
    at1 = _sum_weighted(structure["at1_components"])
    tier2 = _sum_weighted(structure["tier2_components"])
    assert all(_dec(line["weighted_amount"]) < 0 for line in structure["cet1_deductions"])
    assert _dec(structure["cet1_capital_ghs"]) == cet1
    assert _dec(structure["at1_capital_ghs"]) == at1
    assert _dec(structure["tier1_capital_ghs"]) == cet1 + at1
    assert _dec(structure["tier2_capital_ghs"]) == tier2
    assert _dec(structure["total_capital_ghs"]) == cet1 + at1 + tier2
    assert _dec(structure["total_capital_ghs"]) == _dec(metrics["total_capital_ghs"])
    assert _dec(metrics["tier1_ratio_pct"]) == _ratio(cet1 + at1, total_rwa)
    assert _dec(metrics["cet1_ratio_pct"]) == _ratio(cet1, total_rwa)

    trend = body["trend"]
    assert trend
    assert len(trend) <= 13  # trailing window, not the bank's full history
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert trend[-1]["label"] == latest["label"]
    by_period = {point["reporting_period_id"]: point for point in trend}
    assert by_period[period["id"]]["stored"] is False
    assert _dec(by_period[period["id"]]["car_pct"]) == _dec(metrics["car_pct"])
    assert {item["rule_code"] for item in body["validations"]} == BASELINE_VALIDATION_RULES

    run = _create_run(real_client, period["id"], "baseline")
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/capital/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == run["id"]
    # The stored view reads the run back; inline and stored arithmetic agree.
    assert _dec(body["metrics"]["car_pct"]) == _dec(run["metrics"]["car_pct"])
    assert _dec(body["metrics"]["car_pct"]) == _dec(metrics["car_pct"])
    trend = {point["reporting_period_id"]: point for point in body["trend"]}
    assert trend[period["id"]]["stored"] is True
    assert _dec(trend[period["id"]]["car_pct"]) == _dec(run["metrics"]["car_pct"])


def test_structure_and_rwa_endpoints_require_a_baseline_run(real_client: TestClient) -> None:
    period = _period_without_stored_baseline(real_client)

    for path in (
        f"/api/v1/banks/{REAL_BANK_ID}/capital/structure",
        f"/api/v1/banks/{REAL_BANK_ID}/capital/rwa",
    ):
        blocked = real_client.get(
            path, headers=real_headers(), params={"reporting_period_id": period["id"]}
        )
        assert blocked.status_code == 409, path
        assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(real_client, period["id"], "baseline")
    sections = _sections(run)
    cet1, at1, tier2 = _capital_tiers(sections["capital_component"])

    structure = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/capital/structure",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert structure.status_code == 200, structure.text
    body = structure.json()
    assert body["run_id"] == run["id"]
    assert body["reporting_period_id"] == period["id"]
    assert _dec(body["cet1_capital_ghs"]) == cet1
    assert _dec(body["at1_capital_ghs"]) == at1
    assert _dec(body["tier1_capital_ghs"]) == cet1 + at1
    assert _dec(body["tier2_capital_ghs"]) == tier2
    assert _dec(body["total_capital_ghs"]) == _dec(run["metrics"]["total_capital_ghs"])
    assert all(line["line_code"].startswith("at1:") for line in body["at1_components"])
    assert all(line["line_code"].startswith("t2:") for line in body["tier2_components"])
    assert all(_dec(line["weighted_amount"]) < 0 for line in body["cet1_deductions"])
    assert all(_dec(line["weighted_amount"]) >= 0 for line in body["cet1_components"])

    rwa = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/capital/rwa",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert rwa.status_code == 200, rwa.text
    body = rwa.json()
    assert body["run_id"] == run["id"]
    assert _dec(body["total_rwa_ghs"]) == _dec(run["metrics"]["total_rwa_ghs"])
    assert _dec(body["total_rwa_ghs"]) == (
        _dec(body["credit_rwa_ghs"])
        + _dec(body["market_rwa_ghs"])
        + _dec(body["operational_rwa_ghs"])
    )
    assert len(body["credit_lines"]) == len(sections["credit_rwa"])
    assert len(body["market_lines"]) == len(sections["market_rwa"])
    assert len(body["operational_lines"]) == len(sections["operational_rwa"])
    assert _sum_weighted(body["credit_lines"]) == _dec(body["credit_rwa_ghs"])


def test_bsd2_preview_requires_baseline_run_then_renders_rows(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _period_without_stored_baseline(real_client)
    bank = real_client.get(f"/api/v1/banks/{REAL_BANK_ID}", headers=real_headers()).json()

    blocked = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd2",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["error_code"] == "no_baseline_run"

    run = _create_run(real_client, period["id"], "baseline")
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd2",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    metrics = run["metrics"]
    sections = _sections(run)
    thresholds = run["inputs"]["parameters"]["thresholds_pct"]

    header = preview["header"]
    assert header["form_code"] == "BSD5A"
    assert header["form_title"] == "Capital Adequacy Return"
    assert header["regulator"] == "Bank of Ghana"
    assert header["bank_name"] == bank["name"]
    assert header["reporting_period_label"] == period["label"]
    assert header["currency"] == bank["currency"]
    assert header["preview_note"] == (
        "PREVIEW ONLY — This system does not file submissions with Bank of Ghana."
    )
    assert preview["run_id"] == run["id"]
    assert preview["scenario_code"] == "baseline"

    cet1_rows = preview["cet1_rows"]
    assert cet1_rows
    assert [row["row_code"] for row in cet1_rows] == _row_codes(cet1_rows, "1")
    deduction_rows = preview["deduction_rows"]
    assert [row["row_code"] for row in deduction_rows] == _row_codes(deduction_rows, "2")
    assert all(_dec(row["amount"]) >= 0 for row in cet1_rows)
    assert all(_dec(row["amount"]) < 0 for row in deduction_rows)
    cet1_total = _sum_weighted(cet1_rows, "amount") + _sum_weighted(deduction_rows, "amount")
    assert preview["cet1_total"]["row_code"] == "3.0"
    assert _dec(preview["cet1_total"]["value"]) == cet1_total
    at1_rows = preview["at1_rows"]
    assert [row["row_code"] for row in at1_rows] == _row_codes(at1_rows, "4")
    tier1_total = cet1_total + _sum_weighted(at1_rows, "amount")
    assert preview["tier1_total"]["row_code"] == "5.0"
    assert _dec(preview["tier1_total"]["value"]) == tier1_total
    tier2_rows = preview["tier2_rows"]
    assert [row["row_code"] for row in tier2_rows] == _row_codes(tier2_rows, "6")
    gp_rows = [row for row in tier2_rows if "General Provisions" in row["description"]]
    validations = {item["rule_code"]: item for item in run["validations"]}
    for gp_row in gp_rows:
        cap_pct = _dec(thresholds["tier2_gp_cap_pct_credit_rwa"]).normalize()
        assert f"{cap_pct:f}% of credit RWA" in gp_row["description"]
        # The preview's cap wording must agree with the run's own cap validation.
        bound = "cap bound" in gp_row["description"]
        assert ("did not bind" in validations["tier2_gp_cap_applied"]["message"]) is (not bound)
    assert preview["total_capital"]["row_code"] == "7.0"
    assert _dec(preview["total_capital"]["value"]) == tier1_total + _sum_weighted(
        tier2_rows, "amount"
    )
    assert _dec(preview["total_capital"]["value"]) == _dec(metrics["total_capital_ghs"])

    credit_rows = preview["credit_rwa_rows"]
    assert len(credit_rows) == len(sections["credit_rwa"])
    assert [row["row_code"] for row in credit_rows] == _row_codes(credit_rows, "8")
    assert _sum_weighted(credit_rows) == _dec(metrics["credit_rwa_ghs"])
    market_rows = preview["market_rwa_rows"]
    assert [row["row_code"] for row in market_rows] == _row_codes(market_rows, "9")
    assert len(market_rows) == len(sections["market_rwa"])
    operational_rows = preview["operational_rwa_rows"]
    assert [row["row_code"] for row in operational_rows] == _row_codes(operational_rows, "10")
    assert len(operational_rows) == len(sections["operational_rwa"])
    assert preview["total_rwa"]["row_code"] == "11.0"
    assert _dec(preview["total_rwa"]["value"]) == _dec(metrics["total_rwa_ghs"])

    ratio_rows = {row["row_code"]: row for row in preview["ratio_rows"]}
    assert set(ratio_rows) == {"12.1", "12.2", "12.3", "12.4"}
    layout = {
        "12.1": ("cet1_ratio_pct", "cet1_min"),
        "12.2": ("tier1_ratio_pct", "tier1_min"),
        "12.3": ("car_pct", "car_min"),
        "12.4": ("leverage_ratio_pct", "leverage_min"),
    }
    for row_code, (metric_code, threshold_code) in layout.items():
        row = ratio_rows[row_code]
        assert _dec(row["minimum_pct"]) == _dec(thresholds[threshold_code]), row_code
        assert _dec(row["value_pct"]) == _dec(metrics[metric_code]), row_code
        assert row["passed"] is (_dec(row["value_pct"]) >= _dec(row["minimum_pct"])), row_code
    assert {item["rule_code"] for item in preview["validations"]} == BASELINE_VALIDATION_RULES


def test_invalid_module_scenario_combinations_are_rejected_with_422(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    combos = (
        ("capital", "idiosyncratic"),
        ("capital", "market_wide"),
        ("capital", "combined"),
        ("liquidity", "mild"),
        ("liquidity", "severe"),
        ("forecast", "baseline"),
    )
    for module, scenario_code in combos:
        response = real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            json={
                "module": module,
                "reporting_period_id": period["id"],
                "scenario_code": scenario_code,
            },
        )
        assert response.status_code == 422, (module, scenario_code)


def test_unknown_bank_and_period_return_404(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    assert (
        real_client.post(
            f"/api/v1/banks/{uuid4()}/capital/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/capital/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{uuid4()}/capital/dashboard", headers=real_headers()
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd2",
            headers=real_headers(),
            params={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{uuid4()}", headers=real_headers()
        ).status_code
        == 404
    )


def test_regulatory_capital_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    run = _create_run(real_client, period["id"], "baseline")

    other = other_headers()
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            json={
                "module": "capital",
                "reporting_period_id": period["id"],
                "scenario_code": "baseline",
            },
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/capital/run-all-scenarios",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    for path in (
        f"/api/v1/banks/{REAL_BANK_ID}/capital/dashboard",
        f"/api/v1/banks/{REAL_BANK_ID}/capital/rwa",
        f"/api/v1/banks/{REAL_BANK_ID}/capital/structure",
    ):
        assert real_client.get(path, headers=other).status_code == 404, path
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/submissions/bsd2",
            headers=other,
            params={"reporting_period_id": period["id"]},
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
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            params={"module": "capital"},
        ).status_code
        == 404
    )

    # The owning tenant still sees the run it just created.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "capital", "scenario_code": "baseline"},
    ).json()
    assert listed["total"] >= 1
    assert listed["runs"][0]["id"] == run["id"]
