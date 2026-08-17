"""Regulatory-IRR (IRRBB) API tests against the ACTUAL primary database.

DB-backed conversion (tests/real_data.py; docs/bog_returns/CONTRIBUTING_real_db_tests.md):
the retired sample-bank seed is gone, so these run against the real Sample Bank through
``real_client`` (opt-in via REAL_DATA_DATABASE_URL, transaction-isolated, rolled back).
Assertions are INVARIANTS, never golden magnitudes: every gap bucket is RSA − RSL with a
running cumulative, the ≤12m cumulative gap and the ±200 bp EaR are the engine's own sums
over the run's buckets (EaR down = −EaR up), each scenario ΔEVE = EVE − base EVE with % of
Tier 1 and breach against the configured limit, the worst scenario is the largest |ΔEVE|
among the Basel six, statuses/validations follow the limits, the duration gap ties to the
two sides' PVs, the EaR analysis reproduces the official 12m formula and re-weights shorter
horizons without persisting anything, the input hash is scoped to the IRR fact groups, plus
404/422 paths and tenant isolation.
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

MONEY = Decimal("0.0001")  # the IRR engine's money quantum
RATIO_PCT = Decimal("0.000001")  # the IRR engine's ratio quantum
DURATION = Decimal("0.0001")  # the IRR engine's duration quantum
EVE_GREEN_FRACTION = Decimal("0.75")  # engine: amber band just below the EVE limit
HUNDRED = Decimal("100")
TEN_THOUSAND = Decimal("10000")
TWELVE = Decimal("12")
IRR_SCENARIOS = [
    "baseline",
    "parallel_up_200",
    "parallel_down_200",
    "short_up_250",
    "short_down_250",
    "steepener",
    "flattener",
]
BASEL_EVE_SCENARIOS = IRR_SCENARIOS[1:]
OPTIONAL_EVE_SCENARIOS = ["parallel_up_450", "parallel_down_450"]
IRR_FACT_GROUPS = {"irr_position", "irr_swap"}
GAP_BUCKET_COUNT = 9  # the engine's fixed repricing ladder (overnight … 5y+)
IRR_VALIDATION_RULES = {"eve_within_limit", "ear_within_limit", "duration_gap_reasonable"}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def _eve_status(abs_pct: Decimal, limit_pct: Decimal) -> str:
    """Mirror of ``classify_eve_change``: red above the limit, amber inside the buffer band."""
    if abs_pct > limit_pct:
        return "red"
    if abs_pct > limit_pct * EVE_GREEN_FRACTION:
        return "amber"
    return "green"


def _ear(buckets: list[dict[str, Any]], delta_bp: Decimal, horizon_months: Decimal) -> Decimal:
    """Mirror of ``compute_ear``: Σ Gap_i · Δbp/10000 · (N − m_i)/N over buckets inside N."""
    total = Decimal("0")
    for bucket in buckets:
        months = _dec(bucket["midpoint_years"]) * TWELVE
        if months >= horizon_months:
            continue
        total += (
            _dec(bucket["gap_ghs"])
            * (delta_bp / TEN_THOUSAND)
            * ((horizon_months - months) / horizon_months)
        )
    return _money(total)


def _gap_within(buckets: list[dict[str, Any]], horizon_months: Decimal) -> Decimal:
    return sum(
        (
            _dec(bucket["gap_ghs"])
            for bucket in buckets
            if _dec(bucket["midpoint_years"]) * TWELVE < horizon_months
        ),
        Decimal("0"),
    )


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _latest_period(client: TestClient) -> dict[str, Any]:
    return _periods(client)[0]


def _period_without_stored_baseline(client: TestClient) -> dict[str, Any]:
    """The most recent period with NO succeeded baseline IRR run on the primary (the
    dashboard's inline path and the missing-parameter 409 only exist for such a period)."""
    stored: set[str] = set()
    offset = 0
    while True:
        listed = client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            params={"module": "irr", "scenario_code": "baseline", "limit": 100, "offset": offset},
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
    raise AssertionError("every reporting period already carries a stored baseline IRR run")


def _run_all(client: TestClient, period_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/irr/run-all-scenarios",
        headers=real_headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _irr_total(client: TestClient, scenario_code: str | None = None) -> int:
    params: dict[str, Any] = {"module": "irr"}
    if scenario_code is not None:
        params["scenario_code"] = scenario_code
    listed = client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs", headers=real_headers(), params=params
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


def _delete_irr_scenario_shock(session: Session, scenario_code: str) -> None:
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(ParamStressShock).where(
            ParamStressShock.organization_id == REAL_ORG_ID,
            ParamStressShock.module == "irr",
            ParamStressShock.scenario_code == scenario_code,
        )
    )
    session.commit()


def _ear_analysis(
    client: TestClient,
    period_id: str,
    horizon_months: int,
    delta_bp: int,
    request_headers: dict[str, str] | None = None,
) -> Any:
    return client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/irr/ear-analysis",
        headers=request_headers or real_headers(),
        params={
            "reporting_period_id": period_id,
            "horizon_months": horizon_months,
            "delta_bp": delta_bp,
        },
    )


def _assert_gap_consistent(buckets: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    """Every bucket is RSA − RSL with a running cumulative; the ≤12m gap and ±200 bp EaR
    are the engine's own sums over these buckets."""
    assert len(buckets) == GAP_BUCKET_COUNT
    midpoints = [_dec(bucket["midpoint_years"]) for bucket in buckets]
    assert midpoints == sorted(midpoints)
    cumulative = Decimal("0")
    for bucket in buckets:
        gap = _money(_dec(bucket["rsa_ghs"]) - _dec(bucket["rsl_ghs"]))
        assert _dec(bucket["gap_ghs"]) == gap, bucket["bucket"]
        cumulative = _money(cumulative + gap)
        assert _dec(bucket["cumulative_gap_ghs"]) == cumulative, bucket["bucket"]
        assert bucket["within_12m"] is (_dec(bucket["midpoint_years"]) * TWELVE <= TWELVE)
    assert _dec(metrics["cumulative_12m_gap_ghs"]) == _money(
        sum((_dec(b["gap_ghs"]) for b in buckets if b["within_12m"]), Decimal("0"))
    )
    ear_up = _ear(buckets, Decimal("200"), TWELVE)
    assert _dec(metrics["ear_up_200_ghs"]) == ear_up
    assert _dec(metrics["ear_down_200_ghs"]) == _ear(buckets, Decimal("-200"), TWELVE)
    # A parallel shock is linear in the gap: the down move mirrors the up move.
    assert _dec(metrics["ear_down_200_ghs"]) == -ear_up


def _assert_eve_consistent(scenarios: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    """ΔEVE = EVE − base per scenario, % of Tier 1, breach vs the limit, worst = max |ΔEVE|."""
    tier1 = _dec(metrics["tier1_ghs"])
    assert tier1 > 0
    limit = _dec(metrics["eve_limit_pct"])
    base_eve = _dec(metrics["eve_base_ghs"])
    codes = [scenario["scenario_code"] for scenario in scenarios]
    optional = [code for code in OPTIONAL_EVE_SCENARIOS if code in codes]
    assert codes == [*BASEL_EVE_SCENARIOS, *optional]
    by_code: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        code = scenario["scenario_code"]
        delta = _money(_dec(scenario["eve_ghs"]) - base_eve)
        assert _dec(scenario["delta_eve_ghs"]) == delta, code
        pct = _ratio_pct(delta / tier1 * HUNDRED)
        assert _dec(scenario["delta_eve_pct_tier1"]) == pct, code
        informational = code in OPTIONAL_EVE_SCENARIOS
        expected_breach = (
            False if informational else _ratio_pct(abs(delta) / tier1 * HUNDRED) > limit
        )
        assert scenario["breach"] is expected_breach, code
        by_code[code] = scenario
    worst = max(
        (by_code[code] for code in BASEL_EVE_SCENARIOS),
        key=lambda scenario: abs(_dec(scenario["delta_eve_ghs"])),
    )
    assert metrics["worst_scenario"] == worst["scenario_code"]
    assert _dec(metrics["worst_eve_change_ghs"]) == _dec(worst["delta_eve_ghs"])
    assert _dec(metrics["worst_eve_change_pct_tier1"]) == _dec(worst["delta_eve_pct_tier1"])
    # Sign convention: a materially positive duration gap loses value when rates rise.
    duration_gap = _dec(metrics["duration_gap"])
    up = _dec(by_code["parallel_up_200"]["delta_eve_ghs"])
    down = _dec(by_code["parallel_down_200"]["delta_eve_ghs"])
    if duration_gap > Decimal("0.1"):
        assert up < 0 < down
    elif duration_gap < Decimal("-0.1"):
        assert down < 0 < up


def _assert_duration_consistent(metrics: dict[str, Any]) -> None:
    pv_assets, pv_liabilities = _dec(metrics["pv_assets_ghs"]), _dec(metrics["pv_liabilities_ghs"])
    assert pv_assets > 0
    asset_mod, liability_mod = _dec(metrics["asset_duration"]), _dec(metrics["liability_duration"])
    expected = (asset_mod - (pv_liabilities / pv_assets) * liability_mod).quantize(
        DURATION, rounding=ROUND_HALF_UP
    )
    assert _dec(metrics["duration_gap"]) == expected
    # Modified duration is Macaulay discounted by (1 + y): never larger, same sign.
    assert abs(asset_mod) <= abs(_dec(metrics["asset_macaulay"]))
    assert abs(liability_mod) <= abs(_dec(metrics["liability_macaulay"]))


def _assert_validations_consistent(run: dict[str, Any]) -> None:
    metrics = run["metrics"]
    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == IRR_VALIDATION_RULES
    eve = validations["eve_within_limit"]
    assert eve["severity"] == "error"
    assert eve["passed"] is (not any(s["breach"] for s in metrics["eve_by_scenario"]))
    ear = validations["ear_within_limit"]
    assert ear["severity"] == "warning"
    nii = _dec(metrics["nii_base_ghs"])
    nii_limit = _dec(run["inputs"]["parameters"]["limits_pct"]["irr_nii_limit_pct"])
    if nii > 0:
        worst_ear = max(
            abs(_dec(metrics["ear_up_200_ghs"])), abs(_dec(metrics["ear_down_200_ghs"]))
        )
        ratio = (worst_ear / nii * HUNDRED).quantize(Decimal("0.0001"))
        assert ear["passed"] is (ratio <= nii_limit)
    else:
        assert ear["passed"] is True
    assert validations["duration_gap_reasonable"]["severity"] == "info"
    assert validations["duration_gap_reasonable"]["passed"] is True


def test_run_all_irr_scenarios_persists_seven_runs_with_consistent_metrics(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

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
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    groups = {fact["fact_group"] for fact in snapshot["facts"]}
    assert groups <= IRR_FACT_GROUPS
    assert "irr_position" in groups
    parameters = snapshot["parameters"]
    assert {"base_curve_pct", "scenario_shocks", "limits_pct"} <= set(parameters)
    assert set(parameters["scenario_shocks"]) >= set(BASEL_EVE_SCENARIOS)
    assert set(parameters["limits_pct"]) == {"eve_tier1_limit_pct", "irr_nii_limit_pct"}

    metrics = baseline["metrics"]
    assert _dec(metrics["eve_limit_pct"]) == _dec(parameters["limits_pct"]["eve_tier1_limit_pct"])
    _assert_gap_consistent(metrics["gap_buckets"], metrics)
    _assert_eve_consistent(metrics["eve_by_scenario"], metrics)
    _assert_duration_consistent(metrics)
    _assert_validations_consistent(baseline)
    # The BoG ±450 bp add-ons appear only when their shock rows exist — never fabricated.
    has_450 = set(OPTIONAL_EVE_SCENARIOS) <= set(parameters["scenario_shocks"])
    assert ("ear_up_450_ghs" in metrics) is has_450
    assert ("ear_down_450_ghs" in metrics) is has_450
    if has_450:
        up_450 = _dec(parameters["scenario_shocks"]["parallel_up_450"]["parallel_bp"])
        assert _dec(metrics["ear_up_450_ghs"]) == _ear(metrics["gap_buckets"], up_450, TWELVE)
    # Every scenario run analyses the same book: gap, EVE sweep and durations agree.
    for run in runs[1:]:
        assert run["metrics"]["gap_buckets"] == metrics["gap_buckets"], run["scenario_code"]
        assert run["metrics"]["eve_by_scenario"] == metrics["eve_by_scenario"]
        assert run["metrics"]["duration_gap"] == metrics["duration_gap"]
        _assert_validations_consistent(run)

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    expected_codes = {
        "worst_eve_change_pct_tier1",
        "duration_gap",
        "asset_duration",
        "liability_duration",
        "cumulative_12m_gap_ghs",
        "eve_base_ghs",
        "ear_up_200_ghs",
        "ear_down_200_ghs",
    }
    if has_450:
        expected_codes |= {"ear_up_450_ghs", "ear_down_450_ghs"}
    assert set(metric_results) == expected_codes
    worst = metric_results["worst_eve_change_pct_tier1"]
    assert worst["unit"] == "pct"
    assert _dec(worst["threshold_min"]) == _dec(metrics["eve_limit_pct"])
    assert worst["status"] == _eve_status(
        abs(_dec(metrics["worst_eve_change_pct_tier1"])), _dec(metrics["eve_limit_pct"])
    )
    assert metric_results["duration_gap"]["unit"] == "years"
    assert metric_results["cumulative_12m_gap_ghs"]["unit"] == "ghs"
    assert _dec(metric_results["ear_up_200_ghs"]["metric_value"]) == _dec(metrics["ear_up_200_ghs"])

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["irr_gap"]) == GAP_BUCKET_COUNT
    gap_lines = {line["line_code"]: line for line in sections["irr_gap"]}
    for bucket in metrics["gap_buckets"]:
        assert _dec(gap_lines[bucket["bucket"]]["weighted_amount"]) == _dec(bucket["gap_ghs"])
    assert len(sections["irr_eve"]) == 1 + len(metrics["eve_by_scenario"])  # base + scenarios
    eve_lines = {line["line_code"]: line for line in sections["irr_eve"]}
    assert _dec(eve_lines["base"]["exposure_amount"]) == _dec(metrics["eve_base_ghs"])
    for scenario in metrics["eve_by_scenario"]:
        line = eve_lines[scenario["scenario_code"]]
        assert _dec(line["weighted_amount"]) == _dec(scenario["delta_eve_ghs"])
    assert len(sections["irr_ear"]) == 2
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{baseline['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_irr_input_hash_is_scoped_to_irr_facts(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    before = _irr_total(real_client, "baseline")
    first = _run_all(real_client, period["id"])["runs"][0]

    # Editing a capital-component fact changes Tier 1 (an external reference) but
    # must NOT disturb the IRR input hash.
    _bump_one_fact(real_session, period["id"], "capital_component")
    second = _run_all(real_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing an IRR position must change it (value-based, id-independent hash).
    _bump_one_fact(real_session, period["id"], "irr_position")
    third = _run_all(real_client, period["id"])["runs"][0]
    assert third["input_hash"] != first["input_hash"]
    assert third["status"] == "succeeded"

    assert _irr_total(real_client, "baseline") == before + 3


def test_irr_dashboard_computes_inline_then_prefers_stored_runs(real_client: TestClient) -> None:
    latest = _latest_period(real_client)
    period = _period_without_stored_baseline(real_client)

    inline = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/irr/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    gap_table = body["gap_table"]
    assert len(gap_table) == GAP_BUCKET_COUNT
    cumulative = Decimal("0")
    for bucket in gap_table:
        gap = _money(_dec(bucket["rsa_ghs"]) - _dec(bucket["rsl_ghs"]))
        assert _dec(bucket["gap_ghs"]) == gap
        cumulative = _money(cumulative + gap)
        assert _dec(bucket["cumulative_gap_ghs"]) == cumulative
    assert _dec(metrics["cumulative_12m_gap_ghs"]) == _money(
        sum((_dec(b["gap_ghs"]) for b in gap_table if b["within_12m"]), Decimal("0"))
    )
    assert _dec(metrics["ear_up_200_ghs"]) == _ear(gap_table, Decimal("200"), TWELVE)
    assert _dec(metrics["ear_down_200_ghs"]) == -_dec(metrics["ear_up_200_ghs"])
    scenarios = {item["scenario_code"]: item for item in body["eve_scenarios"]}
    assert set(BASEL_EVE_SCENARIOS) <= set(scenarios)
    assert len(scenarios) in {6, 8}  # the six Basel shocks (+ the two BoG ±450 add-ons)
    worst = max(
        (scenarios[code] for code in BASEL_EVE_SCENARIOS),
        key=lambda scenario: abs(_dec(scenario["delta_eve_ghs"])),
    )
    assert metrics["worst_scenario_code"] == worst["scenario_code"]
    assert _dec(metrics["worst_eve_change_pct_tier1"]) == _dec(worst["delta_eve_pct_tier1"])
    assert metrics["eve_status"] == _eve_status(
        abs(_dec(metrics["worst_eve_change_pct_tier1"])), _dec(metrics["eve_limit_pct"])
    )
    assert {item["rule_code"] for item in body["validations"]} == IRR_VALIDATION_RULES

    trend = body["trend"]
    assert trend
    assert len(trend) <= 13  # trailing window, not the bank's full history
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert trend[-1]["label"] == latest["label"]
    by_period = {point["reporting_period_id"]: point for point in trend}
    assert by_period[period["id"]]["stored"] is False
    assert _dec(by_period[period["id"]]["duration_gap"]) == _dec(metrics["duration_gap"])

    batch = _run_all(real_client, period["id"])
    baseline = batch["runs"][0]
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/irr/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == baseline["id"]
    # The stored view reads the run back; inline and stored arithmetic agree.
    assert _dec(body["metrics"]["eve_base_ghs"]) == _dec(baseline["metrics"]["eve_base_ghs"])
    assert _dec(body["metrics"]["eve_base_ghs"]) == _dec(metrics["eve_base_ghs"])
    assert _dec(body["metrics"]["duration_gap"]) == _dec(metrics["duration_gap"])
    trend = {point["reporting_period_id"]: point for point in body["trend"]}
    assert trend[period["id"]]["stored"] is True


def test_missing_irr_shock_persists_failed_runs_without_500(
    real_client: TestClient, real_session: Session
) -> None:
    period = _period_without_stored_baseline(real_client)
    _delete_irr_scenario_shock(real_session, "flattener")

    batch = _run_all(real_client, period["id"])
    runs = batch["runs"]
    # Every run evaluates all six EVE scenarios, so the missing flattener shock
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert all(run["error"]["code"] == "missing_parameter" for run in runs)
    assert "flattener" in str(runs[0]["error"]["details"])
    assert all(run["metrics"] == {} for run in runs)

    dashboard = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/irr/dashboard",
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
            f"/api/v1/banks/{uuid4()}/irr/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/irr/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{uuid4()}/irr/dashboard", headers=real_headers()
        ).status_code
        == 404
    )
    assert _ear_analysis(real_client, str(uuid4()), 12, 200).status_code == 404


def test_ear_analysis_generalizes_horizon_and_persists_nothing(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    before = _irr_total(real_client)

    # N=12 reproduces the regulatory formula on the same canonical gap.
    twelve = _ear_analysis(real_client, period["id"], 12, 200)
    assert twelve.status_code == 200, twelve.text
    body_12 = twelve.json()
    assert body_12["bank_id"] == REAL_BANK_ID
    assert body_12["reporting_period_id"] == period["id"]
    assert body_12["horizon_months"] == 12
    assert body_12["delta_bp"] == 200
    assert _dec(body_12["ear_down"]) == -_dec(body_12["ear_up"])
    assert "12 months" in body_12["basis"]

    # N=6 drops the 6-12m bucket and re-weights the residuals (hand-derived in
    # tests/domain/test_irr_engine.py::test_ear_six_month_horizon_hand_derived).
    six = _ear_analysis(real_client, period["id"], 6, 200)
    assert six.status_code == 200, six.text
    body_6 = six.json()
    assert body_6["horizon_months"] == 6
    assert _dec(body_6["ear_down"]) == -_dec(body_6["ear_up"])
    assert "6 months" in body_6["basis"]

    # The analysis endpoint writes nothing: the run ledger is exactly as it was.
    assert _irr_total(real_client) == before

    # The official 12m ±200bp run is the same arithmetic on the same gap; the run's
    # buckets then let the shorter horizon be re-derived by hand.
    baseline = _run_all(real_client, period["id"])["runs"][0]
    metrics = baseline["metrics"]
    buckets = metrics["gap_buckets"]
    assert _dec(body_12["ear_up"]) == _dec(metrics["ear_up_200_ghs"])
    assert _dec(body_12["ear_down"]) == _dec(metrics["ear_down_200_ghs"])
    assert _dec(body_12["cumulative_gap_within_horizon"]) == _dec(metrics["cumulative_12m_gap_ghs"])
    assert _dec(body_6["ear_up"]) == _ear(buckets, Decimal("200"), Decimal("6"))
    assert _dec(body_6["ear_down"]) == _ear(buckets, Decimal("-200"), Decimal("6"))
    assert _dec(body_6["cumulative_gap_within_horizon"]) == _gap_within(buckets, Decimal("6"))
    # Leaving the horizon: the 6-12m bucket (and only it) drops out of the ≤N gap.
    six_to_twelve = next(b for b in buckets if b["bucket"] == "6-12m")
    assert _dec(body_12["cumulative_gap_within_horizon"]) - _dec(
        body_6["cumulative_gap_within_horizon"]
    ) == _dec(six_to_twelve["gap_ghs"])


def test_ear_analysis_validates_horizon_and_delta(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    for horizon_months, delta_bp in (
        (0, 200),  # horizon below 1 month
        (61, 200),  # horizon above 60 months
        (12, 10),  # shock below ±25 bp
        (12, 999),  # shock above ±500 bp
        (12, 130),  # shock off the 25 bp grid
        (12, 0),  # no shock at all
    ):
        response = _ear_analysis(real_client, period["id"], horizon_months, delta_bp)
        assert response.status_code == 422, response.text
        details = response.json()["error"]["details"]
        assert details["error_code"] in ("invalid_ear_horizon", "invalid_ear_delta_bp")

    # Negative shocks on the grid are valid and mirror the positive direction.
    positive = _ear_analysis(real_client, period["id"], 12, 200)
    negative = _ear_analysis(real_client, period["id"], 12, -200)
    assert positive.status_code == 200, positive.text
    assert negative.status_code == 200, negative.text
    assert _dec(negative.json()["ear_up"]) == _dec(positive.json()["ear_down"])
    assert _dec(negative.json()["ear_down"]) == _dec(positive.json()["ear_up"])

    # Tenant isolation matches the other IRR endpoints.
    assert _ear_analysis(real_client, period["id"], 12, 200, other_headers()).status_code == 404


def test_regulatory_irr_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

    other = other_headers()
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/irr/run-all-scenarios",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"/api/v1/banks/{REAL_BANK_ID}/irr/dashboard", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            params={"module": "irr"},
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

    # The owning tenant still sees the seven runs it just created, newest first.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "irr"},
    ).json()
    assert listed["total"] >= 7
    assert {run["id"] for run in listed["runs"][:7]} == {run["id"] for run in batch["runs"]}
