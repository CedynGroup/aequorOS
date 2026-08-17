"""Regulatory-FX API tests against the ACTUAL primary database.

DB-backed conversion (tests/real_data.py; docs/bog_returns/CONTRIBUTING_real_db_tests.md):
the retired sample-bank seed is gone, so these run against the real Sample Bank through
``real_client`` (opt-in via REAL_DATA_DATABASE_URL, transaction-isolated, rolled back).
Assertions are INVARIANTS, never golden magnitudes: the aggregate NOP is the Basel shorthand
max(Σ long, Σ short) over the run's own per-currency positions, NOP % = NOP ÷ Tier 1, the
single-currency ceiling is the largest |position| ÷ Tier 1, limit statuses / validations
follow the configured limits, depreciation shocks scale every position and grow the NOP
monotonically, standalone-minus-portfolio VaR is the diversification benefit, hedges pass
the IFRS 9 dual test exactly when both bands hold, the input hash is scoped to the FX fact
groups, plus 404s and tenant isolation.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import BankFinancialFact, ParamStressShock
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

MONEY = Decimal("0.0001")  # the FX engine's money quantum
RATIO_PCT = Decimal("0.000001")  # the FX engine's ratio quantum
LIMIT_GREEN_FRACTION = Decimal("0.75")  # engine: amber band just below a supervisory limit
HUNDRED = Decimal("100")
FX_SCENARIOS = ["baseline", "mild_depreciation", "severe_depreciation", "cedi_crisis"]
FX_FACT_GROUPS = {"fx_position", "fx_return_history", "fx_hedge"}
FX_VALIDATION_RULES = {
    "nop_within_aggregate_limit",
    "single_ccy_within_limit",
    "hedges_effective",
    "stressed_var_disclosed",
}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def _limit_status(value_pct: Decimal, limit_pct: Decimal) -> str:
    """Mirror of ``classify_limit``: red above the limit, amber inside the buffer band."""
    if value_pct > limit_pct:
        return "red"
    if value_pct > limit_pct * LIMIT_GREEN_FRACTION:
        return "amber"
    return "green"


def _nop_from_positions(nets: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """(Σ long, Σ short, NOP) — the Basel shorthand ``max(sum_long, sum_short)``."""
    sum_long = _money(sum((net for net in nets if net >= 0), Decimal("0")))
    sum_short = _money(sum((-net for net in nets if net < 0), Decimal("0")))
    return sum_long, sum_short, max(sum_long, sum_short)


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _latest_period(client: TestClient) -> dict[str, Any]:
    return _periods(client)[0]


def _period_without_stored_baseline(client: TestClient) -> dict[str, Any]:
    """The most recent period with NO succeeded baseline FX run on the primary (the
    dashboard's inline path and the missing-parameter 409 only exist for such a period)."""
    stored: set[str] = set()
    offset = 0
    while True:
        listed = client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            params={"module": "fx", "scenario_code": "baseline", "limit": 100, "offset": offset},
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
    raise AssertionError("every reporting period already carries a stored baseline FX run")


def _run_all(client: TestClient, period_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/fx/run-all-scenarios",
        headers=real_headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _baseline_total(client: TestClient) -> int:
    listed = client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "fx", "scenario_code": "baseline"},
    )
    assert listed.status_code == 200, listed.text
    return listed.json()["total"]


def _bump_one_fact(session: Session, period_id: str, fact_group: str) -> None:
    """Change ONE real fact of ``fact_group`` on the shared, rolled-back transaction."""
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


def _delete_fx_scenario_shock(session: Session, scenario_code: str) -> None:
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(ParamStressShock).where(
            ParamStressShock.organization_id == REAL_ORG_ID,
            ParamStressShock.module == "fx",
            ParamStressShock.scenario_code == scenario_code,
        )
    )
    session.commit()


def _assert_nop_consistent(metrics: dict[str, Any]) -> None:
    """The NOP block must be the engine's own arithmetic over its per-currency positions."""
    tier1 = _dec(metrics["tier1_ghs"])
    assert tier1 > 0
    single_limit = _dec(metrics["nop_single_limit_pct"])
    aggregate_limit = _dec(metrics["nop_aggregate_limit_pct"])
    currencies = metrics["currencies"]
    assert currencies
    assert [c["currency"] for c in currencies] == sorted(c["currency"] for c in currencies)
    nets: list[Decimal] = []
    single_max_pct, single_max_ccy = Decimal("0"), ""
    for position in currencies:
        net = _dec(position["net_ghs"])
        nets.append(net)
        assert position["side"] == ("long" if net >= 0 else "short"), position["currency"]
        abs_pct = _ratio_pct(abs(net) / tier1 * HUNDRED)
        assert _dec(position["abs_pct_tier1"]) == abs_pct, position["currency"]
        assert position["within_single_limit"] is (abs_pct <= single_limit)
        if abs_pct > single_max_pct:
            single_max_pct, single_max_ccy = abs_pct, position["currency"]
    sum_long, sum_short, nop = _nop_from_positions(nets)
    assert _dec(metrics["sum_long_ghs"]) == sum_long
    assert _dec(metrics["sum_short_ghs"]) == sum_short
    assert _dec(metrics["nop_ghs"]) == nop
    nop_pct = _ratio_pct(nop / tier1 * HUNDRED)
    assert _dec(metrics["nop_pct_tier1"]) == nop_pct
    assert _dec(metrics["single_ccy_max_pct"]) == single_max_pct
    assert metrics["single_ccy_max_currency"] == single_max_ccy
    assert metrics["within_single_limit"] is (single_max_pct <= single_limit)
    assert metrics["within_aggregate_limit"] is (nop_pct <= aggregate_limit)


def _assert_var_and_hedges_consistent(metrics: dict[str, Any], hedge_bands: dict[str, str]) -> None:
    standalone = metrics["standalone_vars"]
    assert len(standalone) == len(metrics["currencies"])
    assert {item["currency"] for item in standalone} == {
        item["currency"] for item in metrics["currencies"]
    }
    standalone_total = _money(
        sum((_dec(item["standalone_var_ghs"]) for item in standalone), Decimal("0"))
    )
    assert _dec(metrics["standalone_var_total_ghs"]) == standalone_total
    portfolio_var = _dec(metrics["var_99_1d_ghs"])
    assert portfolio_var >= 0
    assert _dec(metrics["diversification_benefit_ghs"]) == _money(standalone_total - portfolio_var)
    assert _dec(metrics["stressed_var_ghs"]) >= 0
    assert int(metrics["var_observations"]) > 0

    hedges = metrics["hedges"]
    r2_min = _dec(hedge_bands["hedge_r2_min_pct"])
    offset_low = _dec(hedge_bands["hedge_offset_low_pct"])
    offset_high = _dec(hedge_bands["hedge_offset_high_pct"])
    effective = 0
    aggregate_mtm = Decimal("0")
    for hedge in hedges:
        r2_pct, offset_pct = _dec(hedge["prospective_r2_pct"]), _dec(hedge["dollar_offset_pct"])
        expected = r2_pct >= r2_min and offset_low <= offset_pct <= offset_high
        assert hedge["effective"] is expected, hedge["hedge_id"]
        effective += int(expected)
        aggregate_mtm += _dec(hedge["mtm_ghs"])
    assert int(metrics["hedge_total_count"]) == len(hedges)
    assert int(metrics["hedge_effective_count"]) == effective
    assert int(metrics["hedge_ineffective_count"]) == len(hedges) - effective
    assert _dec(metrics["hedge_aggregate_mtm_ghs"]) == _money(aggregate_mtm)


def _assert_scenarios_consistent(metrics: dict[str, Any], shocks: dict[str, str]) -> None:
    """Each depreciation shock scales every GHS-equivalent position by (1 + s/100), so the
    scenario NOP is the shorthand NOP of the scaled book and grows with the shock."""
    tier1 = _dec(metrics["tier1_ghs"])
    aggregate_limit = _dec(metrics["nop_aggregate_limit_pct"])
    scenarios = {item["scenario_code"]: item for item in metrics["nop_by_scenario"]}
    assert list(scenarios) == FX_SCENARIOS
    nets = [_dec(position["net_ghs"]) for position in metrics["currencies"]]
    for code, scenario in scenarios.items():
        shock = Decimal("0") if code == "baseline" else _dec(shocks[code])
        assert _dec(scenario["shock_pct"]) == shock, code
        factor = 1 + shock / HUNDRED
        _long, _short, nop = _nop_from_positions([_money(net * factor) for net in nets])
        assert _dec(scenario["nop_ghs"]) == nop, code
        nop_pct = _ratio_pct(nop / tier1 * HUNDRED)
        assert _dec(scenario["nop_pct_tier1"]) == nop_pct, code
        assert scenario["within_aggregate_limit"] is (nop_pct <= aggregate_limit), code
    assert _dec(scenarios["baseline"]["nop_ghs"]) == _dec(metrics["nop_ghs"])
    ordered = sorted(scenarios.values(), key=lambda item: _dec(item["shock_pct"]))
    nops = [_dec(item["nop_ghs"]) for item in ordered]
    assert nops == sorted(nops)  # a bigger depreciation never shrinks the open position


def _assert_validations_consistent(run: dict[str, Any]) -> None:
    metrics = run["metrics"]
    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == FX_VALIDATION_RULES
    assert validations["nop_within_aggregate_limit"]["severity"] == "error"
    assert validations["nop_within_aggregate_limit"]["passed"] is metrics["within_aggregate_limit"]
    assert validations["single_ccy_within_limit"]["severity"] == "error"
    assert validations["single_ccy_within_limit"]["passed"] is metrics["within_single_limit"]
    assert validations["hedges_effective"]["severity"] == "warning"
    assert validations["hedges_effective"]["passed"] is (
        int(metrics["hedge_ineffective_count"]) == 0
    )
    assert validations["stressed_var_disclosed"]["severity"] == "info"
    assert validations["stressed_var_disclosed"]["passed"] is True


def test_run_all_fx_scenarios_persists_four_runs_with_consistent_metrics(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

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
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    groups = {fact["fact_group"] for fact in snapshot["facts"]}
    assert groups <= FX_FACT_GROUPS
    assert "fx_position" in groups
    parameters = snapshot["parameters"]
    assert set(parameters) == {"limits_pct", "hedge_bands_pct", "depreciation_shocks_pct", "crisis"}
    assert set(parameters["depreciation_shocks_pct"]) == set(FX_SCENARIOS) - {"baseline"}

    metrics = baseline["metrics"]
    _assert_nop_consistent(metrics)
    _assert_var_and_hedges_consistent(metrics, parameters["hedge_bands_pct"])
    _assert_scenarios_consistent(metrics, parameters["depreciation_shocks_pct"])
    _assert_validations_consistent(baseline)
    limits = parameters["limits_pct"]
    assert _dec(metrics["nop_single_limit_pct"]) == _dec(limits["fx_nop_single_limit_pct"])
    assert _dec(metrics["nop_aggregate_limit_pct"]) == _dec(limits["fx_nop_aggregate_limit_pct"])
    assert _dec(metrics["var_confidence_pct"]) == _dec(limits["fx_var_confidence_pct"])
    # Every scenario run prices the same book: the position block is scenario-independent.
    for run in runs[1:]:
        assert run["metrics"]["currencies"] == metrics["currencies"], run["scenario_code"]
        assert run["metrics"]["nop_by_scenario"] == metrics["nop_by_scenario"]
        _assert_validations_consistent(run)

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    assert set(metric_results) == {
        "nop_pct_tier1",
        "single_ccy_max_pct",
        "nop_ghs",
        "var_99_1d_ghs",
        "stressed_var_ghs",
        "diversification_benefit_ghs",
    }
    nop_result = metric_results["nop_pct_tier1"]
    assert nop_result["unit"] == "pct"
    assert _dec(nop_result["threshold_min"]) == _dec(metrics["nop_aggregate_limit_pct"])
    assert nop_result["status"] == _limit_status(
        _dec(metrics["nop_pct_tier1"]), _dec(metrics["nop_aggregate_limit_pct"])
    )
    single_result = metric_results["single_ccy_max_pct"]
    assert _dec(single_result["threshold_min"]) == _dec(metrics["nop_single_limit_pct"])
    assert single_result["status"] == _limit_status(
        _dec(metrics["single_ccy_max_pct"]), _dec(metrics["nop_single_limit_pct"])
    )
    assert metric_results["var_99_1d_ghs"]["unit"] == "ghs"
    assert metric_results["var_99_1d_ghs"]["status"] == "na"
    assert _dec(metric_results["nop_ghs"]["metric_value"]) == _dec(metrics["nop_ghs"])

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["fx_position"]) == len(metrics["currencies"])
    position_lines = {line["line_code"]: line for line in sections["fx_position"]}
    for position in metrics["currencies"]:
        assert _dec(position_lines[position["currency"]]["weighted_amount"]) == _dec(
            position["net_ghs"]
        )
    # portfolio_var + diversification + one standalone per currency + stressed_var.
    assert len(sections["fx_var"]) == 3 + len(metrics["currencies"])
    var_lines = {line["line_code"]: line for line in sections["fx_var"]}
    assert _dec(var_lines["portfolio_var"]["weighted_amount"]) == _dec(metrics["var_99_1d_ghs"])
    assert _dec(var_lines["stressed_var"]["weighted_amount"]) == _dec(metrics["stressed_var_ghs"])
    assert len(sections.get("fx_hedge", [])) == len(metrics["hedges"])
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{baseline['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_fx_input_hash_is_scoped_to_fx_facts(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    before = _baseline_total(real_client)
    first = _run_all(real_client, period["id"])["runs"][0]

    # Editing an IRR position touches a different fact group; the FX hash must not move.
    _bump_one_fact(real_session, period["id"], "irr_position")
    second = _run_all(real_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing a capital-component fact changes Tier 1 (an external reference) but
    # must NOT disturb the FX input hash.
    _bump_one_fact(real_session, period["id"], "capital_component")
    third = _run_all(real_client, period["id"])["runs"][0]
    assert third["input_hash"] == first["input_hash"]

    # Editing an FX position must change it (value-based, id-independent hash).
    _bump_one_fact(real_session, period["id"], "fx_position")
    fourth = _run_all(real_client, period["id"])["runs"][0]
    assert fourth["input_hash"] != first["input_hash"]
    assert fourth["status"] == "succeeded"

    assert _baseline_total(real_client) == before + 4


def test_fx_dashboard_computes_inline_then_prefers_stored_runs(real_client: TestClient) -> None:
    latest = _latest_period(real_client)
    period = _period_without_stored_baseline(real_client)

    inline = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    tier1 = _dec(metrics["tier1_ghs"])
    assert tier1 > 0
    nets = [_dec(position["net_ghs"]) for position in body["positions"]]
    sum_long, sum_short, nop = _nop_from_positions(nets)
    assert _dec(metrics["sum_long_ghs"]) == sum_long
    assert _dec(metrics["sum_short_ghs"]) == sum_short
    assert _dec(metrics["nop_ghs"]) == nop
    assert _dec(metrics["nop_pct_tier1"]) == _ratio_pct(nop / tier1 * HUNDRED)
    assert metrics["nop_status"] == _limit_status(
        _dec(metrics["nop_pct_tier1"]), _dec(metrics["nop_aggregate_limit_pct"])
    )
    assert metrics["single_ccy_status"] == _limit_status(
        _dec(metrics["single_ccy_max_pct"]), _dec(metrics["nop_single_limit_pct"])
    )
    assert _dec(metrics["diversification_benefit_ghs"]) == _money(
        _dec(metrics["standalone_var_total_ghs"]) - _dec(metrics["var_99_1d_ghs"])
    )
    assert body["positions"]
    assert len(body["standalone_vars"]) == len(body["positions"])
    assert len(body["hedges"]) == int(metrics["hedge_total_count"])
    assert [item["scenario_code"] for item in body["scenarios"]] == FX_SCENARIOS
    assert {item["rule_code"] for item in body["validations"]} == FX_VALIDATION_RULES

    trend = body["trend"]
    assert trend
    assert len(trend) <= 13  # trailing window, not the bank's full history
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert trend[-1]["label"] == latest["label"]
    by_period = {point["reporting_period_id"]: point for point in trend}
    assert by_period[period["id"]]["stored"] is False
    assert _dec(by_period[period["id"]]["nop_ghs"]) == _dec(metrics["nop_ghs"])

    batch = _run_all(real_client, period["id"])
    baseline = batch["runs"][0]
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == baseline["id"]
    # The stored view reads the run back; inline and stored arithmetic agree.
    assert _dec(body["metrics"]["stressed_var_ghs"]) == _dec(
        baseline["metrics"]["stressed_var_ghs"]
    )
    assert _dec(body["metrics"]["nop_ghs"]) == _dec(metrics["nop_ghs"])
    assert _dec(body["metrics"]["var_99_1d_ghs"]) == _dec(metrics["var_99_1d_ghs"])
    trend = {point["reporting_period_id"]: point for point in body["trend"]}
    assert trend[period["id"]]["stored"] is True


def test_missing_fx_shock_persists_failed_runs_without_500(
    real_client: TestClient, real_session: Session
) -> None:
    period = _period_without_stored_baseline(real_client)
    _delete_fx_scenario_shock(real_session, "severe_depreciation")

    batch = _run_all(real_client, period["id"])
    runs = batch["runs"]
    # Every run needs the full depreciation-scenario set, so a missing scenario
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert all(run["error"]["code"] == "missing_parameter" for run in runs)
    assert all(run["metrics"] == {} for run in runs)

    dashboard = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    # With no successful stored run, the dashboard falls back to inline compute
    # and surfaces the missing parameter as a 409, not a 500.
    assert dashboard.status_code == 409
    assert dashboard.json()["error"]["details"]["error_code"] == "missing_parameter"


def test_unknown_bank_and_period_return_404(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    assert (
        real_client.post(
            f"/api/v1/banks/{uuid4()}/fx/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/fx/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"/api/v1/banks/{uuid4()}/fx/dashboard", headers=real_headers()).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard",
            headers=real_headers(),
            params={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )


def test_regulatory_fx_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

    other = other_headers()
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/fx/run-all-scenarios",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            params={"module": "fx"},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{batch['runs'][0]['id']}",
            headers=other,
        ).status_code
        == 404
    )

    # The owning tenant still sees the four runs it just created, newest first.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "fx"},
    ).json()
    assert listed["total"] >= 4
    assert {run["id"] for run in listed["runs"][:4]} == {run["id"] for run in batch["runs"]}
