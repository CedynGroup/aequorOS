"""Regulatory-FTP API tests against the ACTUAL primary database.

DB-backed conversion (tests/real_data.py; docs/bog_returns/CONTRIBUTING_real_db_tests.md):
the retired sample-bank seed is gone, so these run against the real Sample Bank through
``real_client`` (opt-in via REAL_DATA_DATABASE_URL, transaction-isolated, rolled back).
Assertions are INVARIANTS, never golden magnitudes: every product margin is the engine's own
customer-rate-minus-transfer-rate arithmetic (assets net of cost, loss and capital charge;
liabilities as the funding credit), the portfolio NIM / asset yield / funding credit are the
balance-weighted margins, branches rank by their FTP contribution, the NMD core split ties to
the policy band, stress overlays shift every curve point and product margin by exactly the
configured overlay, the LTP contingent charge is undrawn × stressed draw × 1Y-minus-overnight
carry, the input hash is scoped to the FTP fact groups, plus 404s and tenant isolation.
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

MONEY = Decimal("0.0001")  # the FTP engine's money quantum
RATIO_PCT = Decimal("0.000001")  # the FTP engine's ratio quantum
CORE_AMBER_BAND_PP = Decimal("2")  # engine: amber band around the NMD core policy band
HUNDRED = Decimal("100")
FTP_SCENARIOS = ["baseline", "rates_up_200", "funding_stress"]
FTP_FACT_GROUPS = {"ftp_curve_point", "ftp_product", "ftp_branch", "ftp_nmd", "off_balance"}
CORE_FTP_FACT_GROUPS = {"ftp_curve_point", "ftp_product", "ftp_branch", "ftp_nmd"}
FTP_VALIDATION_RULES = {
    "all_products_above_min_margin",
    "nmd_core_within_policy",
    "curve_arithmetic_consistent",
    "curve_within_premium_limits",
}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def _weighted_pct(weighted: Decimal, base: Decimal) -> Decimal:
    """Mirror of the engine's ``_weighted_pct`` (zero on an empty base)."""
    if base <= 0:
        return Decimal("0")
    return _ratio_pct(weighted / base)


def _core_status(core_pct: Decimal, min_pct: Decimal, max_pct: Decimal) -> str:
    """Mirror of ``classify_core_band``: green inside the band, amber within 2pp of it."""
    if min_pct <= core_pct <= max_pct:
        return "green"
    if (min_pct - CORE_AMBER_BAND_PP) <= core_pct <= (max_pct + CORE_AMBER_BAND_PP):
        return "amber"
    return "red"


def _rate_at(points: list[dict[str, Any]], tenor_years: Decimal) -> Decimal:
    """Mirror of ``CurveResult.rate_at``: endpoint clamp outside, linear inside."""
    first, last = points[0], points[-1]
    if tenor_years <= _dec(first["tenor_years"]):
        return _dec(first["ftp_rate_pct"])
    if tenor_years >= _dec(last["tenor_years"]):
        return _dec(last["ftp_rate_pct"])
    for lower, upper in zip(points, points[1:], strict=False):
        lower_tenor, upper_tenor = _dec(lower["tenor_years"]), _dec(upper["tenor_years"])
        if lower_tenor <= tenor_years <= upper_tenor:
            if tenor_years == lower_tenor:
                return _dec(lower["ftp_rate_pct"])
            if tenor_years == upper_tenor:
                return _dec(upper["ftp_rate_pct"])
            fraction = (tenor_years - lower_tenor) / (upper_tenor - lower_tenor)
            lower_rate, upper_rate = _dec(lower["ftp_rate_pct"]), _dec(upper["ftp_rate_pct"])
            return _ratio_pct(lower_rate + fraction * (upper_rate - lower_rate))
    raise AssertionError("tenor must be bracketed by the curve")


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _latest_period(client: TestClient) -> dict[str, Any]:
    return _periods(client)[0]


def _period_without_stored_baseline(client: TestClient) -> dict[str, Any]:
    """The most recent period with NO succeeded baseline FTP run on the primary (the
    dashboard's inline path and the missing-parameter 409 only exist for such a period)."""
    stored: set[str] = set()
    offset = 0
    while True:
        listed = client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=real_headers(),
            params={"module": "ftp", "scenario_code": "baseline", "limit": 100, "offset": offset},
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
    raise AssertionError("every reporting period already carries a stored baseline FTP run")


def _run_all(client: TestClient, period_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/ftp/run-all-scenarios",
        headers=real_headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _baseline_total(client: TestClient) -> int:
    listed = client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "ftp", "scenario_code": "baseline"},
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


def _delete_ftp_scenario_shock(session: Session, scenario_code: str) -> None:
    session.info["organization_id"] = REAL_ORG_ID
    session.execute(
        delete(ParamStressShock).where(
            ParamStressShock.organization_id == REAL_ORG_ID,
            ParamStressShock.module == "ftp",
            ParamStressShock.scenario_code == scenario_code,
        )
    )
    session.commit()


def _assert_products_consistent(metrics: dict[str, Any]) -> None:
    """Every product margin, contribution and the book-level weighted margins must be the
    engine's own arithmetic over the run's curve and the product's own rates."""
    curve = metrics["curve"]
    assert curve
    assert [_dec(p["tenor_years"]) for p in curve] == sorted(_dec(p["tenor_years"]) for p in curve)
    products = metrics["products"]
    assert products
    min_margin = _dec(metrics["min_product_margin_pct"])
    asset_bal = liability_bal = asset_weighted = liability_weighted = Decimal("0")
    below: list[str] = []
    for product in products:
        balance = _dec(product["balance_ghs"])
        ftp = _dec(product["ftp_rate_pct"])
        assert ftp == _rate_at(curve, _dec(product["tenor_years"])), product["product"]
        if product["category"] == "asset":
            margin = _ratio_pct(
                _dec(product["customer_rate_pct"])
                - ftp
                - _dec(product["operating_cost_pct"])
                - _dec(product["expected_credit_loss_pct"])
                - _dec(product["capital_charge_pct"])
            )
            asset_bal += balance
            asset_weighted += balance * margin
        else:
            margin = _ratio_pct(
                ftp - _dec(product["customer_rate_pct"]) - _dec(product["operating_cost_pct"])
            )
            liability_bal += balance
            liability_weighted += balance * margin
        assert _dec(product["net_margin_pct"]) == margin, product["product"]
        assert _dec(product["contribution_ghs"]) == _money(balance * margin / HUNDRED)
        assert product["below_min_margin"] is (margin < min_margin), product["product"]
        if margin < min_margin:
            below.append(product["product"])
    assert int(metrics["total_products"]) == len(products)
    assert int(metrics["products_below_min_margin"]) == len(below)
    assert metrics["below_min_products"] == below
    assert _dec(metrics["total_balance_ghs"]) == _money(asset_bal + liability_bal)
    assert _dec(metrics["portfolio_nim_pct"]) == _weighted_pct(
        asset_weighted + liability_weighted, asset_bal + liability_bal
    )
    assert _dec(metrics["weighted_asset_yield_pct"]) == _weighted_pct(asset_weighted, asset_bal)
    assert _dec(metrics["weighted_funding_credit_pct"]) == _weighted_pct(
        liability_weighted, liability_bal
    )
    assert _dec(metrics["total_contribution_ghs"]) == _money(
        (asset_weighted + liability_weighted) / HUNDRED
    )


def _assert_branches_consistent(metrics: dict[str, Any]) -> None:
    """Branches rank by net FTP contribution = deposits × funding credit + loans × asset yield."""
    branches = metrics["branches"]
    assert branches
    asset_yield = _dec(metrics["weighted_asset_yield_pct"])
    funding_credit = _dec(metrics["weighted_funding_credit_pct"])
    total = Decimal("0")
    contributions: list[Decimal] = []
    for index, branch in enumerate(branches, start=1):
        deposits, loans = _dec(branch["deposits_ghs"]), _dec(branch["loans_ghs"])
        contribution = _money(deposits * funding_credit / HUNDRED + loans * asset_yield / HUNDRED)
        assert _dec(branch["net_contribution_ghs"]) == contribution, branch["branch"]
        assert _dec(branch["book_ghs"]) == _money(deposits + loans), branch["branch"]
        assert _dec(branch["ftp_adjusted_nim_pct"]) == _weighted_pct(
            contribution * HUNDRED, _money(deposits + loans)
        )
        assert branch["rank"] == index, branch["branch"]
        contributions.append(contribution)
        total += contribution
    assert contributions == sorted(contributions, reverse=True)
    assert _dec(metrics["total_branch_contribution_ghs"]) == _money(total)


def _assert_nmd_consistent(metrics: dict[str, Any]) -> None:
    """The NMD core/volatile split reconciles per segment and to the policy band."""
    segments = metrics["nmd_segments"]
    assert segments
    core_min, core_max = _dec(metrics["nmd_core_min_pct"]), _dec(metrics["nmd_core_max_pct"])
    assert core_min <= core_max
    curve = metrics["curve"]
    overnight = _dec(curve[0]["ftp_rate_pct"])
    total_balance = total_core = Decimal("0")
    for segment in segments:
        balance = _dec(segment["balance_ghs"])
        core_pct, volatile_pct = _dec(segment["core_pct"]), _dec(segment["volatile_pct"])
        core_amount = _dec(segment["core_amount_ghs"])
        assert core_amount == _money(balance * core_pct / HUNDRED), segment["segment"]
        assert _dec(segment["volatile_amount_ghs"]) == _money(balance - core_amount)
        assert _dec(segment["volatile_ftp_pct"]) == overnight
        assert _dec(segment["core_ftp_pct"]) == _rate_at(
            curve, _dec(segment["effective_duration_years"])
        )
        assert _dec(segment["assigned_ftp_pct"]) == _ratio_pct(
            (core_pct * _dec(segment["core_ftp_pct"]) + volatile_pct * overnight) / HUNDRED
        )
        assert segment["within_policy"] is (core_min <= core_pct <= core_max)
        total_balance += balance
        total_core += core_amount
    assert total_balance > 0
    core_pct = _weighted_pct(total_core * HUNDRED, total_balance)
    assert _dec(metrics["nmd_core_pct"]) == core_pct
    # Core and volatile shares partition the book (each side rounds independently).
    assert abs(core_pct + _dec(metrics["nmd_volatile_pct"]) - HUNDRED) <= RATIO_PCT
    assert metrics["nmd_within_policy"] is (core_min <= core_pct <= core_max)


def _assert_validations_consistent(run: dict[str, Any]) -> None:
    metrics = run["metrics"]
    validations = {item["rule_code"]: item for item in run["validations"]}
    assert set(validations) == FTP_VALIDATION_RULES
    margin = validations["all_products_above_min_margin"]
    assert margin["severity"] == "warning"
    assert margin["passed"] is (int(metrics["products_below_min_margin"]) == 0)
    nmd = validations["nmd_core_within_policy"]
    assert nmd["severity"] == "info"
    assert nmd["passed"] is metrics["nmd_within_policy"]
    consistent = validations["curve_arithmetic_consistent"]
    assert consistent["severity"] == "error"
    shift = _dec(metrics["curve_shift_pct"])
    reconciles = all(
        _money(
            _dec(point["base_yield_pct"])
            + (_dec(point["liquidity_premium_bps"]) + _dec(point["funding_spread_bps"])) / HUNDRED
            + shift
        )
        == _money(_dec(point["ftp_rate_pct"]))
        for point in metrics["curve"]
    )
    # An overlay lifts the transfer rate above base + premia by design; only an
    # unshifted curve can reconcile — the validation must say exactly that.
    assert consistent["passed"] is (reconciles and shift == 0)
    premium = validations["curve_within_premium_limits"]
    assert premium["severity"] == "info"
    assert premium["passed"] is all(
        _dec(point["liquidity_premium_bps"]) <= _dec(metrics["liquidity_premium_max_bps"])
        and _dec(point["funding_spread_bps"]) <= _dec(metrics["funding_spread_max_bps"])
        for point in metrics["curve"]
    )


def test_run_all_ftp_scenarios_persists_three_runs_with_consistent_metrics(  # noqa: PLR0915
    real_client: TestClient,
) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

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
    assert snapshot["as_of_date"] == period["period_end"]
    assert snapshot["reporting_period"]["label"] == period["label"]
    groups = {fact["fact_group"] for fact in snapshot["facts"]}
    assert groups <= FTP_FACT_GROUPS
    assert groups >= CORE_FTP_FACT_GROUPS
    assert set(snapshot["parameters"]) == {"thresholds", "stress_overlays_bps"}
    overlays = snapshot["parameters"]["stress_overlays_bps"]
    assert set(overlays) == {"rates_up_200", "funding_stress"}

    metrics = baseline["metrics"]
    assert _dec(metrics["curve_shift_pct"]) == 0
    _assert_products_consistent(metrics)
    _assert_branches_consistent(metrics)
    _assert_nmd_consistent(metrics)
    _assert_validations_consistent(baseline)
    assert _dec(metrics["total_balance_ghs"]) > 0

    # The rate-up and funding-stress overlays lift every curve point — and therefore
    # every product's transfer rate — by exactly the configured overlay.
    for run in runs[1:]:
        scenario = run["scenario_code"]
        shift = _dec(overlays[scenario]) / HUNDRED
        assert _dec(run["metrics"]["curve_shift_pct"]) == shift, scenario
        assert shift > 0, scenario
        _assert_products_consistent(run["metrics"])
        _assert_branches_consistent(run["metrics"])
        _assert_nmd_consistent(run["metrics"])
        _assert_validations_consistent(run)
        base_points = {point["tenor_label"]: point for point in metrics["curve"]}
        for point in run["metrics"]["curve"]:
            assert _dec(point["ftp_rate_pct"]) == (
                _dec(base_points[point["tenor_label"]]["ftp_rate_pct"]) + shift
            ), (scenario, point["tenor_label"])
        base_products = {product["product"]: product for product in metrics["products"]}
        for product in run["metrics"]["products"]:
            base = base_products[product["product"]]
            assert _dec(product["ftp_rate_pct"]) == _dec(base["ftp_rate_pct"]) + shift
            # Assets pay more for funds (margin down); liabilities earn a bigger credit.
            expected = (
                _dec(base["net_margin_pct"]) - shift
                if product["category"] == "asset"
                else _dec(base["net_margin_pct"]) + shift
            )
            assert _dec(product["net_margin_pct"]) == expected, (scenario, product["product"])
        assert int(run["metrics"]["total_products"]) == int(metrics["total_products"])
        assert _dec(run["metrics"]["total_balance_ghs"]) == _dec(metrics["total_balance_ghs"])

    metric_results = {item["metric_code"]: item for item in baseline["metric_results"]}
    assert set(metric_results) == {
        "portfolio_nim_pct",
        "weighted_asset_yield_pct",
        "weighted_funding_credit_pct",
        "nmd_core_pct",
        "total_branch_contribution_ghs",
    }
    assert metric_results["portfolio_nim_pct"]["unit"] == "pct"
    assert _dec(metric_results["portfolio_nim_pct"]["metric_value"]) == _dec(
        metrics["portfolio_nim_pct"]
    )
    nmd_result = metric_results["nmd_core_pct"]
    assert _dec(nmd_result["threshold_min"]) == _dec(metrics["nmd_core_min_pct"])
    assert nmd_result["status"] == _core_status(
        _dec(metrics["nmd_core_pct"]),
        _dec(metrics["nmd_core_min_pct"]),
        _dec(metrics["nmd_core_max_pct"]),
    )
    assert metric_results["total_branch_contribution_ghs"]["unit"] == "ghs"
    assert _dec(metric_results["total_branch_contribution_ghs"]["metric_value"]) == _dec(
        metrics["total_branch_contribution_ghs"]
    )

    sections: dict[str, list[dict[str, Any]]] = {}
    for item in baseline["line_items"]:
        sections.setdefault(item["section"], []).append(item)
    assert len(sections["ftp_curve"]) == len(metrics["curve"])
    assert len(sections["ftp_product"]) == len(metrics["products"])
    assert len(sections["ftp_branch"]) == len(metrics["branches"])
    product_lines = {line["line_code"]: line for line in sections["ftp_product"]}
    for product in metrics["products"]:
        line = product_lines[product["product"]]
        assert _dec(line["exposure_amount"]) == _dec(product["balance_ghs"])
        assert _dec(line["rate_pct"]) == _dec(product["net_margin_pct"])
        assert _dec(line["weighted_amount"]) == _dec(product["contribution_ghs"])
    positions = [item["position"] for item in baseline["line_items"]]
    assert positions == sorted(positions)

    fetched = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs/{baseline['id']}", headers=real_headers()
    )
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == baseline["input_hash"]


def test_ltp_contingent_charge_prices_committed_facilities(real_client: TestClient) -> None:
    """Phase 2 item 11 (LRMD ¶78–79): the LTP block charges each committed
    facility family for its expected stressed draw (the liquidity combined
    scenario's runoff — ¶48(b)) at the FTP curve's 1Y-minus-overnight carry."""
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])
    baseline = batch["runs"][0]
    metrics = baseline["metrics"]
    assert "ltp_items" in metrics, "the real book must carry a committed facility to price"

    curve = metrics["curve"]
    carry = _dec(metrics["ltp_buffer_cost_pct"])
    assert carry == max(
        _rate_at(curve, Decimal("1")) - _dec(curve[0]["ftp_rate_pct"]), Decimal("0")
    )
    assert carry >= 0  # an inverted curve never turns the charge into a rebate

    items = {item["line_code"]: item for item in metrics["ltp_items"]}
    assert items
    undrawn_by_family = {
        fact["category"]: _dec(fact["amount"])
        for fact in baseline["inputs"]["facts"]
        if fact["fact_group"] == "off_balance"
    }
    # Every priced family is a real committed-facility fact, priced on its undrawn amount.
    assert set(items) <= set(undrawn_by_family)
    assert list(items) == sorted(items)
    total = Decimal("0")
    for line_code, item in items.items():
        undrawn = _dec(item["undrawn_amount_ghs"])
        assert undrawn == _money(undrawn_by_family[line_code])
        draw_pct = _dec(item["expected_draw_pct"])
        assert 0 <= draw_pct <= 100
        assert _dec(item["buffer_cost_pct"]) == carry
        expected = _money(undrawn * draw_pct / HUNDRED * carry / HUNDRED)
        assert _dec(item["annual_charge_ghs"]) == expected, line_code
        total += expected
    assert _dec(metrics["ltp_total_charge_ghs"]) == _money(total)


def test_ftp_input_hash_is_scoped_to_ftp_facts(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    before = _baseline_total(real_client)
    first = _run_all(real_client, period["id"])["runs"][0]

    # Editing an FX position touches a different fact group; the FTP hash must not move.
    _bump_one_fact(real_session, period["id"], "fx_position")
    second = _run_all(real_client, period["id"])["runs"][0]
    assert second["id"] != first["id"]
    assert second["input_hash"] == first["input_hash"]

    # Editing an IRR position likewise leaves the FTP hash untouched.
    _bump_one_fact(real_session, period["id"], "irr_position")
    third = _run_all(real_client, period["id"])["runs"][0]
    assert third["input_hash"] == first["input_hash"]

    # Editing an FTP product must change it (value-based, id-independent hash).
    _bump_one_fact(real_session, period["id"], "ftp_product")
    fourth = _run_all(real_client, period["id"])["runs"][0]
    assert fourth["input_hash"] != first["input_hash"]
    assert fourth["status"] == "succeeded"

    assert _baseline_total(real_client) == before + 4


def test_ftp_dashboard_computes_inline_then_prefers_stored_runs(real_client: TestClient) -> None:
    latest = _latest_period(real_client)
    period = _period_without_stored_baseline(real_client)

    inline = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert inline.status_code == 200, inline.text
    body = inline.json()
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    assert body["period"]["id"] == period["id"]
    metrics = body["metrics"]
    assert metrics["nmd_core_status"] == _core_status(
        _dec(metrics["nmd_core_pct"]),
        _dec(metrics["nmd_core_min_pct"]),
        _dec(metrics["nmd_core_max_pct"]),
    )
    assert int(metrics["total_products"]) == len(body["products"])
    assert int(metrics["products_below_min_margin"]) == sum(
        1 for product in body["products"] if product["below_min_margin"]
    )
    assert body["curve"]
    assert body["branches"]
    assert [branch["rank"] for branch in body["branches"]] == list(
        range(1, len(body["branches"]) + 1)
    )
    assert body["nmd_segments"]
    assert {item["rule_code"] for item in body["validations"]} == FTP_VALIDATION_RULES

    trend = body["trend"]
    assert trend
    assert len(trend) <= 13  # trailing window, not the bank's full history
    period_ends = [point["period_end"] for point in trend]
    assert period_ends == sorted(period_ends)
    assert trend[-1]["label"] == latest["label"]
    by_period = {point["reporting_period_id"]: point for point in trend}
    assert by_period[period["id"]]["stored"] is False

    batch = _run_all(real_client, period["id"])
    baseline = batch["runs"][0]
    stored = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard",
        headers=real_headers(),
        params={"reporting_period_id": period["id"]},
    )
    assert stored.status_code == 200
    body = stored.json()
    assert body["stored"] is True
    assert body["latest_run_id"] == baseline["id"]
    # The stored view reads the run back; inline and stored arithmetic agree.
    assert _dec(body["metrics"]["total_branch_contribution_ghs"]) == _dec(
        baseline["metrics"]["total_branch_contribution_ghs"]
    )
    assert _dec(body["metrics"]["portfolio_nim_pct"]) == _dec(metrics["portfolio_nim_pct"])
    trend = {point["reporting_period_id"]: point for point in body["trend"]}
    assert trend[period["id"]]["stored"] is True


def test_missing_ftp_shock_persists_failed_runs_without_500(
    real_client: TestClient, real_session: Session
) -> None:
    period = _period_without_stored_baseline(real_client)
    _delete_ftp_scenario_shock(real_session, "rates_up_200")

    batch = _run_all(real_client, period["id"])
    runs = batch["runs"]
    # Every run needs the full stress-overlay parameter set, so a missing scenario
    # fails each one as data (named error code), never a 500.
    assert all(run["status"] == "failed" for run in runs)
    assert all(run["error"]["code"] == "missing_parameter" for run in runs)
    assert all(run["metrics"] == {} for run in runs)

    dashboard = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard",
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
            f"/api/v1/banks/{uuid4()}/ftp/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/ftp/run-all-scenarios",
            headers=real_headers(),
            json={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{uuid4()}/ftp/dashboard", headers=real_headers()
        ).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard",
            headers=real_headers(),
            params={"reporting_period_id": str(uuid4())},
        ).status_code
        == 404
    )


def test_regulatory_ftp_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    batch = _run_all(real_client, period["id"])

    other = other_headers()
    assert (
        real_client.post(
            f"/api/v1/banks/{REAL_BANK_ID}/ftp/run-all-scenarios",
            headers=other,
            json={"reporting_period_id": period["id"]},
        ).status_code
        == 404
    )
    assert (
        real_client.get(f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard", headers=other).status_code
        == 404
    )
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
            headers=other,
            params={"module": "ftp"},
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

    # The owning tenant still sees the three runs it just created, newest first.
    listed = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs",
        headers=real_headers(),
        params={"module": "ftp"},
    ).json()
    assert listed["total"] >= 3
    assert {run["id"] for run in listed["runs"][:3]} == {run["id"] for run in batch["runs"]}
