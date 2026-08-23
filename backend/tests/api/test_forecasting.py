"""Balance-sheet forecasting API against the ACTUAL primary database.

Invariants (never frozen goldens — the real book moves): horizon → path length
and monotone period labels; projection identities (assets = funding, income
= NII + fees, summary derived from its own path); metric statuses and
validations consistent with thresholds; year-0 LCR/NSFR equal the standalone
liquidity engine and year-0 CAR/Tier 1/CET1 the standalone capital engine;
determinism of input_hash; module scoping; tenant isolation.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data

pytestmark = requires_real_data

MONEY_TOLERANCE = Decimal("0.01")  # per-category money() rounding across a sum
OPTIMIZER_TOP_LIMIT = 10
ASSUMPTION_KEYS = {
    "loan_growth_pct",
    "deposit_growth_pct",
    "nim_pct",
    "cost_to_income_pct",
    "credit_loss_rate_pct",
    "fx_depreciation_pct",
    "dividend_payout_pct",
}
RESOLVED_ASSUMPTION_KEYS = ASSUMPTION_KEYS | {
    "fee_income_pct_assets",
    "tax_rate_pct",
    "securities_shift_pp",
}
# The declared forecast fact scope. It is a SUPERSET of both downstream
# engines' scopes (see tests/equivalence/), so a given bank's snapshot carries
# these groups minus the ones it has no facts in.
FORECAST_FACT_GROUPS = {
    "balance_sheet",
    "capital_component",
    "crm_collateral",
    "ecl_exposure",
    "lcr_inflow",
    "loan_exposure",
    "market_risk",
    "off_balance",
    "operational_income",
    "securities",
}


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _create_forecast_run(
    client: TestClient,
    period_id: str,
    scenario_code: str,
    assumptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"reporting_period_id": period_id, "scenario_code": scenario_code}
    if assumptions is not None:
        payload["assumptions"] = assumptions
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs", headers=real_headers(), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def _module_total(client: TestClient, module: str) -> int:
    response = client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": module},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["total"])


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _expected_labels(period: dict[str, Any], years: int) -> list[str]:
    """Year 0 is the period itself; year N is the same month N calendar years on."""
    end = datetime.date.fromisoformat(period["period_end"])
    return [period["label"]] + [f"{end.year + n:04d}-{end.month:02d}" for n in range(1, years + 1)]


def _assert_path_identities(path: list[dict[str, Any]], years: int, period: dict[str, Any]) -> None:
    """The projection identities that hold for ANY book, in every year."""
    assert [row["year"] for row in path] == list(range(years + 1))
    assert [row["period_label"] for row in path] == _expected_labels(period, years)
    # Constant (unprojected) assets and constant funding rows do not move, so
    # ``total_assets - (loans + securities + cash)`` and
    # ``total_assets - (deposits + borrowings_plug + equity)`` are the same
    # constants in every year: assets tie to funding through the plug.
    other_assets = {
        _dec(row["total_assets"]) - _dec(row["loans"]) - _dec(row["securities"]) - _dec(row["cash"])
        for row in path
    }
    other_funding = {
        _dec(row["total_assets"])
        - _dec(row["deposits"])
        - _dec(row["borrowings_plug"])
        - _dec(row["equity"])
        for row in path
    }
    assert len(other_assets) == 1, other_assets
    assert len(other_funding) == 1, other_funding
    assert path[0]["roe_pct"] is None
    for row in path[1:]:
        assert _dec(row["total_income"]) == _dec(row["nii"]) + _dec(row["fees"])
        assert _dec(row["borrowings_plug"]) >= 0
        assert _dec(row["dividends"]) >= 0
        if _dec(row["net_income"]) > 0:
            assert _dec(row["dividends"]) <= _dec(row["net_income"])
        assert row["roe_pct"] is not None


def _assert_summary_matches_path(summary: dict[str, Any], path: list[dict[str, Any]]) -> None:
    projected = path[1:]
    assert _dec(summary["year5_car_pct"]) == _dec(projected[-1]["car_pct"])
    assert _dec(summary["year5_lcr_pct"]) == _dec(projected[-1]["lcr_pct"])
    assert _dec(summary["year5_nsfr_pct"]) == _dec(projected[-1]["nsfr_pct"])
    assert _dec(summary["min_car_pct"]) == min(_dec(row["car_pct"]) for row in projected)
    assert _dec(summary["min_lcr_pct"]) == min(_dec(row["lcr_pct"]) for row in projected)
    assert _dec(summary["min_nsfr_pct"]) == min(_dec(row["nsfr_pct"]) for row in projected)
    assert _dec(summary["cumulative_net_income"]) == sum(
        (_dec(row["net_income"]) for row in projected), Decimal("0")
    )
    assert _dec(summary["min_car_pct"]) <= _dec(summary["year5_car_pct"])


def test_list_forecast_scenarios_returns_presets_and_defaults(real_client: TestClient) -> None:
    response = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/scenarios", headers=real_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == REAL_BANK_ID
    scenarios = {item["code"]: item["assumptions"] for item in body["scenarios"]}
    assert set(scenarios) == {"base", "adverse", "severely_adverse"}
    for assumptions in scenarios.values():
        assert set(assumptions) == ASSUMPTION_KEYS
    # Presets are bank parameters, so only their ORDERING is invariant: each
    # step of severity grows more slowly, earns less and loses more.
    base, adverse, severe = scenarios["base"], scenarios["adverse"], scenarios["severely_adverse"]
    for key in ("loan_growth_pct", "deposit_growth_pct", "nim_pct"):
        assert _dec(base[key]) >= _dec(adverse[key]) >= _dec(severe[key]), key
    for key in ("cost_to_income_pct", "credit_loss_rate_pct", "fx_depreciation_pct"):
        assert _dec(base[key]) <= _dec(adverse[key]) <= _dec(severe[key]), key
    assert _dec(base["dividend_payout_pct"]) >= _dec(severe["dividend_payout_pct"])
    defaults = body["defaults"]
    assert set(defaults) == {"fee_income_pct_assets", "tax_rate_pct", "securities_shift_pp"}
    assert _dec(defaults["fee_income_pct_assets"]) >= 0
    assert Decimal("0") <= _dec(defaults["tax_rate_pct"]) < Decimal("100")


def test_create_base_forecast_run_persists_projection_and_outputs(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    run = _create_forecast_run(real_client, period["id"], "base")

    assert run["status"] == "succeeded"
    assert run["module"] == "forecast"
    assert run["scenario_code"] == "base"
    assert run["engine_version"] == "regulatory-forecasting-v1.0.0"
    assert run["input_schema_version"] == "bank-facts-v2"
    assert run["output_schema_version"] == "forecast-projection-v1"
    assert run["error"] is None
    assert len(run["input_hash"]) == 64
    assert run["started_at"] is not None
    assert run["completed_at"] is not None

    snapshot = run["inputs"]
    assert snapshot["module"] == "forecast"
    assert snapshot["scenario_code"] == "base"
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    assert snapshot["facts"], "the forecast snapshot must carry facts"
    # The snapshot is scoped to exactly the groups the downstream engines read —
    # never wider, and it must carry ``ecl_exposure``, the capital input whose
    # absence used to make year-0 CAR diverge from the capital run.
    snapshot_groups = {fact["fact_group"] for fact in snapshot["facts"]}
    assert snapshot_groups <= FORECAST_FACT_GROUPS
    assert "ecl_exposure" in snapshot_groups
    assert FORECAST_FACT_GROUPS - snapshot_groups <= {"crm_collateral"}, (
        "the real book has no crm_collateral facts; every other declared group "
        "must be present in the snapshot"
    )
    assert snapshot["assumption_overrides"] is None
    presets = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/scenarios", headers=real_headers()
    ).json()
    base_preset = next(item for item in presets["scenarios"] if item["code"] == "base")
    for key in ASSUMPTION_KEYS:
        assert _dec(snapshot["assumptions"][key]) == _dec(base_preset["assumptions"][key])

    resolved = run["assumptions"]
    assert set(resolved) == RESOLVED_ASSUMPTION_KEYS
    for key in ASSUMPTION_KEYS:
        assert _dec(resolved[key]) == _dec(base_preset["assumptions"][key])
    for key in ("fee_income_pct_assets", "tax_rate_pct", "securities_shift_pp"):
        assert _dec(resolved[key]) == _dec(presets["defaults"][key])

    path = run["path"]
    _assert_path_identities(path, 5, period)
    # Year 0 is the as-of book: its balances are the derived balance-sheet facts
    # (positive), and no P&L has accrued yet.
    assert _dec(path[0]["total_assets"]) > 0
    assert _dec(path[0]["loans"]) > 0
    assert _dec(path[0]["deposits"]) > 0
    for field in ("nii", "fees", "total_income", "opex", "credit_losses", "net_income"):
        assert _dec(path[0][field]) == 0
    # Year 1 mechanics: loans and deposits scale by the resolved growth rates.
    loan_factor = 1 + _dec(resolved["loan_growth_pct"]) / 100
    deposit_factor = 1 + _dec(resolved["deposit_growth_pct"]) / 100
    assert abs(_dec(path[1]["loans"]) - _dec(path[0]["loans"]) * loan_factor) <= MONEY_TOLERANCE
    assert (
        abs(_dec(path[1]["deposits"]) - _dec(path[0]["deposits"]) * deposit_factor)
        <= MONEY_TOLERANCE
    )

    summary = run["summary"]
    _assert_summary_matches_path(summary, path)

    # Year-0 ratios equal the standalone engines' baselines on the same period.
    # This is cross-module consistency, not a coincidence: year 0 IS the as-of
    # book, so the projection hands the same facts to the same engines.
    #
    # The CAR arm of this used to be missing, with a comment claiming the two
    # were expected to differ because the forecast snapshot excluded
    # ``ecl_exposure``. That was the forensic audit's High finding, and the
    # comment was also wrong about the cause: the forecast additionally
    # excluded ``crm_collateral``, and excluding ECL exposures diverges only
    # once a bank ALSO configures its IFRS 9 assumption register (the modeled
    # override is gated on both). The forecast now carries the capital run's
    # full input set, so the equality below holds by construction.
    # tests/equivalence/ carries the hermetic proof and the scope guard.
    liquidity = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "liquidity",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert liquidity.status_code == 201, liquidity.text
    assert liquidity.json()["status"] == "succeeded"
    assert _dec(path[0]["lcr_pct"]) == _dec(liquidity.json()["metrics"]["lcr_pct"])
    assert _dec(path[0]["nsfr_pct"]) == _dec(liquidity.json()["metrics"]["nsfr_pct"])

    capital = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "capital",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert capital.status_code == 201, capital.text
    assert capital.json()["status"] == "succeeded"
    capital_metrics = capital.json()["metrics"]
    assert _dec(path[0]["car_pct"]) == _dec(capital_metrics["car_pct"])
    assert _dec(path[0]["tier1_ratio_pct"]) == _dec(capital_metrics["tier1_ratio_pct"])
    assert _dec(path[0]["cet1_ratio_pct"]) == _dec(capital_metrics["cet1_ratio_pct"])

    metric_results = {item["metric_code"]: item for item in run["metric_results"]}
    assert set(metric_results) == {
        "avg_roe_pct",
        "year5_car_pct",
        "year5_lcr_pct",
        "year5_nsfr_pct",
    }
    assert metric_results["avg_roe_pct"]["status"] == "na"
    assert metric_results["avg_roe_pct"]["threshold_min"] is None
    assert _dec(metric_results["avg_roe_pct"]["metric_value"]) == _dec(summary["avg_roe_pct"])
    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == {
        "projection_balance_ties",
        "year5_car_above_minimum",
        "year5_lcr_above_minimum",
        "year5_nsfr_above_minimum",
    }
    assert all(item["severity"] == "error" for item in validations.values())
    assert validations["projection_balance_ties"]["passed"] is True
    # Status and validation must agree with the value they grade.
    for code, rule in (
        ("year5_car_pct", "year5_car_above_minimum"),
        ("year5_lcr_pct", "year5_lcr_above_minimum"),
        ("year5_nsfr_pct", "year5_nsfr_above_minimum"),
    ):
        metric = metric_results[code]
        value = _dec(metric["metric_value"])
        assert value == _dec(summary[code])
        minimum = _dec(metric["threshold_min"])
        assert minimum > 0
        above = value >= minimum
        assert metric["status"] in ({"green", "amber"} if above else {"red"}), code
        assert validations[rule]["passed"] is above, rule

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs/{run['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == run["input_hash"]
    assert fetched.json()["path"] == path


def test_forecast_horizon_years_controls_the_path_length(real_client: TestClient) -> None:
    period = _latest_period(real_client)

    # Default (nothing passed) stays the 5-year projection with no snapshot key,
    # so unchanged inputs keep reproducing the pre-horizon input_hash.
    default_run = _create_forecast_run(real_client, period["id"], "base")
    assert [row["year"] for row in default_run["path"]] == [0, 1, 2, 3, 4, 5]
    assert "horizon_years" not in default_run["inputs"]

    three = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
        headers=real_headers(),
        json={
            "reporting_period_id": period["id"],
            "scenario_code": "base",
            "horizon_years": 3,
        },
    )
    assert three.status_code == 201, three.text
    run = three.json()
    assert run["status"] == "succeeded"
    _assert_path_identities(run["path"], 3, period)
    # Provenance: the non-default horizon is persisted in the run inputs and
    # therefore participates in the input hash.
    assert run["inputs"]["horizon_years"] == 3
    assert run["input_hash"] != default_run["input_hash"]
    # Year-by-year mechanics are horizon-independent: the shared years match.
    assert run["path"][:4] == default_run["path"][:4]
    # The summary's final-year fields read from the run's own last year.
    _assert_summary_matches_path(run["summary"], run["path"])
    assert run["summary"]["year5_car_pct"] == default_run["path"][3]["car_pct"]

    # An explicit 5 is the same projection; only the snapshot provenance differs.
    explicit = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
        headers=real_headers(),
        json={
            "reporting_period_id": period["id"],
            "scenario_code": "base",
            "horizon_years": 5,
        },
    )
    assert explicit.status_code == 201, explicit.text
    assert explicit.json()["input_hash"] == default_run["input_hash"]
    assert explicit.json()["path"] == default_run["path"]

    # Bounds are schema-enforced.
    for horizon in (0, 11):
        rejected = real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
            headers=real_headers(),
            json={
                "reporting_period_id": period["id"],
                "scenario_code": "base",
                "horizon_years": horizon,
            },
        )
        assert rejected.status_code == 422, rejected.text


def test_custom_scenario_requires_assumptions_then_resolves_partial_override(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)

    blocked = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
        headers=real_headers(),
        json={"reporting_period_id": period["id"], "scenario_code": "custom"},
    )
    assert blocked.status_code == 422

    base = _create_forecast_run(real_client, period["id"], "base")
    base_assumptions = base["assumptions"]
    # Override loan growth to a value distinct from the base preset.
    override = str(_dec(base_assumptions["loan_growth_pct"]) - Decimal("8"))
    run = _create_forecast_run(
        real_client, period["id"], "custom", assumptions={"loan_growth_pct": override}
    )
    assert run["status"] == "succeeded"
    assert run["scenario_code"] == "custom"
    resolved = run["assumptions"]
    # The override applies; every other key resolves from the base preset.
    assert _dec(resolved["loan_growth_pct"]) == _dec(override)
    for key in RESOLVED_ASSUMPTION_KEYS - {"loan_growth_pct"}:
        assert _dec(resolved[key]) == _dec(base_assumptions[key]), key
    assert run["inputs"]["assumption_overrides"] == {"loan_growth_pct": override}
    # Year-1 loans scale by the overridden rate off the same year-0 book.
    assert run["path"][0] == base["path"][0]
    factor = 1 + _dec(override) / 100
    assert abs(_dec(run["path"][1]["loans"]) - _dec(run["path"][0]["loans"]) * factor) <= (
        MONEY_TOLERANCE
    )
    assert _dec(run["path"][1]["loans"]) < _dec(base["path"][1]["loans"])
    assert base["input_hash"] != run["input_hash"]


def test_forecast_runs_list_and_get_are_module_scoped(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    forecast_before = _module_total(real_client, "forecast")
    first = _create_forecast_run(real_client, period["id"], "base")
    second = _create_forecast_run(real_client, period["id"], "severely_adverse")

    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
        headers=real_headers(),
        params={"limit": 100},
    )
    assert listed.status_code == 200
    body = listed.json()
    # The real bank carries prior forecast runs; ours are the two newest.
    assert body["total"] == forecast_before + 2
    assert body["has_more"] is (body["total"] > 100)
    assert [item["id"] for item in body["runs"][:2]] == [second["id"], first["id"]]
    ours = {first["id"], second["id"]}
    summaries = {item["id"]: item for item in body["runs"] if item["id"] in ours}
    assert len(summaries) == 2
    for summary in summaries.values():
        assert summary["status"] == "succeeded"
        assert summary["period_label"] == period["label"]
        assert summary["avg_roe_pct"] is not None
        assert summary["year5_car_pct"] is not None
        assert summary["year5_lcr_pct"] is not None
        assert summary["year5_nsfr_pct"] is not None
    # Severely adverse is directionally worse on profitability than base.
    assert _dec(summaries[second["id"]]["avg_roe_pct"]) < (
        _dec(summaries[first["id"]]["avg_roe_pct"])
    )
    assert _dec(summaries[second["id"]]["year5_car_pct"]) < (
        _dec(summaries[first["id"]]["year5_car_pct"])
    )

    # The shared regulatory-runs listing can filter the new modules.
    assert _module_total(real_client, "forecast") == forecast_before + 2

    # A capital run is not retrievable through the forecast-run endpoint.
    capital_run = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        json={
            "module": "capital",
            "reporting_period_id": period["id"],
            "scenario_code": "baseline",
        },
    )
    assert capital_run.status_code == 201, capital_run.text
    missing = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs/{capital_run.json()['id']}",
        headers=real_headers(),
    )
    assert missing.status_code == 404
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs/{uuid4()}", headers=real_headers()
        ).status_code
        == 404
    )


def test_strategic_optimizer_persists_a_run_and_returns_ranked_candidates(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    optimizer_before = _module_total(real_client, "optimizer")
    response = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/optimizer",
        headers=real_headers(),
        json={"reporting_period_id": period["id"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["scenario_code"] == "constrained_search"
    assert body["error"] is None
    assert len(body["input_hash"]) == 64
    evaluated = body["candidates_evaluated"]
    feasible = body["feasible_count"]
    assert evaluated > 0
    assert 0 <= feasible <= evaluated
    # The ranked shortlist is the feasible set, capped, ordered by average ROE.
    assert len(body["top"]) == min(feasible, OPTIMIZER_TOP_LIMIT)
    roes = [_dec(candidate["summary"]["avg_roe_pct"]) for candidate in body["top"]]
    assert roes == sorted(roes, reverse=True)
    for candidate in body["top"]:
        assert candidate["feasible"] is True
        statuses = {item["constraint"]: item for item in candidate["constraint_status"]}
        assert set(statuses) == {"car", "lcr", "nsfr"}
        assert all(item["passed"] is True for item in statuses.values())
    histogram = body["binding_constraint_histogram"]
    assert set(histogram) >= {"car", "lcr", "nsfr"}
    # Every infeasible candidate binds at least one constraint (or errored),
    # and a fully feasible search binds none.
    assert sum(histogram.values()) >= evaluated - feasible
    if feasible == evaluated:
        assert sum(histogram.values()) == 0
    # The search starts from the resolved base preset.
    base_preset = next(
        item
        for item in real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/scenarios", headers=real_headers()
        ).json()["scenarios"]
        if item["code"] == "base"
    )
    for key in ASSUMPTION_KEYS:
        assert _dec(body["base_assumptions"][key]) == _dec(base_preset["assumptions"][key])

    # The optimizer run persists under module='optimizer' as the newest run.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "optimizer"},
    ).json()
    assert listed["total"] == optimizer_before + 1
    assert listed["runs"][0]["id"] == body["run_id"]
    assert listed["runs"][0]["scenario_code"] == "constrained_search"
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{body['run_id']}", headers=real_headers()
    ).json()
    assert stored["module"] == "optimizer"
    assert stored["metrics"]["candidates_evaluated"] == evaluated
    assert stored["metrics"]["feasible_count"] == feasible


def test_whatif_analysis_persists_a_run_and_compares_paths(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    whatif_before = _module_total(real_client, "whatif")
    response = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/whatif",
        headers=real_headers(),
        json={"reporting_period_id": period["id"], "shock_code": "default_spike"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["shock_code"] == "default_spike"
    assert len(body["base_path"]) == 6
    assert len(body["shocked_path"]) == 6
    assert len(body["deltas"]) == 6
    # Both paths start from the same as-of book.
    assert body["base_path"][0] == body["shocked_path"][0]
    # default_spike multiplies the credit-loss rate by 2.5 and touches nothing
    # else: year-5 CAR and net income both fall relative to base.
    base_loss = _dec(body["base_assumptions"]["credit_loss_rate_pct"])
    assert _dec(body["shocked_assumptions"]["credit_loss_rate_pct"]) == base_loss * Decimal("2.5")
    for key in RESOLVED_ASSUMPTION_KEYS - {"credit_loss_rate_pct"}:
        assert _dec(body["shocked_assumptions"][key]) == _dec(body["base_assumptions"][key]), key
    year5 = body["year5"]
    assert _dec(year5["car_pct"]["shocked"]) < _dec(year5["car_pct"]["base"])
    assert _dec(year5["net_income"]["shocked"]) < _dec(year5["net_income"]["base"])
    for metric in year5.values():
        assert _dec(metric["delta"]) == _dec(metric["shocked"]) - _dec(metric["base"])
    assert _dec(year5["car_pct"]["base"]) == _dec(body["base_path"][5]["car_pct"])
    assert _dec(year5["car_pct"]["shocked"]) == _dec(body["shocked_path"][5]["car_pct"])
    assert _dec(body["base_summary"]["year5_car_pct"]) == _dec(year5["car_pct"]["base"])
    assert _dec(body["shocked_summary"]["year5_car_pct"]) == _dec(year5["car_pct"]["shocked"])

    mpr = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/whatif",
        headers=real_headers(),
        json={"reporting_period_id": period["id"], "shock_code": "mpr_cut_200"},
    )
    assert mpr.status_code == 201, mpr.text
    # A policy-rate cut stimulates lending: year-5 loans exceed the base path.
    assert _dec(mpr.json()["shocked_path"][5]["loans"]) > _dec(mpr.json()["base_path"][5]["loans"])

    unknown = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/whatif",
        headers=real_headers(),
        json={"reporting_period_id": period["id"], "shock_code": "meteor_strike"},
    )
    assert unknown.status_code == 422

    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "whatif"},
    ).json()
    assert listed["total"] == whatif_before + 2
    assert {run["scenario_code"] for run in listed["runs"][:2]} == {"default_spike", "mpr_cut_200"}


def test_forecast_modules_are_not_creatable_through_create_regulatory_run(
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    for module in ("forecast", "optimizer", "whatif"):
        response = real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            json={
                "module": module,
                "reporting_period_id": period["id"],
                "scenario_code": "base",
            },
        )
        assert response.status_code == 422, module


def test_unknown_bank_and_period_return_404(real_client: TestClient) -> None:
    assert (
        real_client.get(
            f"/api/v1/banks/{uuid4()}/forecast/scenarios", headers=real_headers()
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4()), "scenario_code": "base"},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/optimizer",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )


def test_forecasting_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    forecast_before = _module_total(real_client, "forecast")
    run = _create_forecast_run(real_client, period["id"], "base")

    other = other_headers()
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/scenarios", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs",
            headers=other,
            json={"reporting_period_id": period["id"], "scenario_code": "base"},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs/{run['id']}", headers=other
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/optimizer",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/forecast/whatif",
            headers=other,
            json={"reporting_period_id": period["id"], "shock_code": "default_spike"},
        ).status_code
        == 404
    )

    # None of the foreign attempts minted a run: exactly ours was added.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/forecast/runs", headers=real_headers()
    ).json()
    assert listed["total"] == forecast_before + 1
    assert listed["runs"][0]["id"] == run["id"]
