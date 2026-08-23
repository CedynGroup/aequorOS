"""Forecast year 0 vs the capital and liquidity runs, on the ACTUAL book.

``test_forecast_capital_parity.py`` proves the equality in the pure domain.
This file proves it survives the whole service path — two loaders, two
snapshots, two hashes, three governed registers — on the real Sample Bank.

It is the end-to-end form of the audit's High finding. Year 0 of a projection
is the as-of book, so every ratio the projection reports for it must equal the
standalone run's ratio for the same reporting period. Not "close": equal.
Both sides are Decimal and quantize once, at the same place.

Opt-in via ``REAL_DATA_DATABASE_URL``; the whole transaction rolls back.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.capital.engine import compute_capital_ratios, compute_rwa
from app.domain.forecasting.engine import (
    _general_provisions_override,
    _parse_facts,
    _state_facts,
    _to_capital_facts,
)
from app.models import Bank, BankReportingPeriod
from app.services import regulatory_forecasting
from tests.equivalence.conftest import EXACT
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_USER_ID,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

BASE = f"/api/v1/banks/{REAL_BANK_ID}"

#: forecast path field -> run metric key, per module.
CAPITAL_PARITY = {
    "car_pct": "car_pct",
    "tier1_ratio_pct": "tier1_ratio_pct",
    "cet1_ratio_pct": "cet1_ratio_pct",
}
LIQUIDITY_PARITY = {"lcr_pct": "lcr_pct", "nsfr_pct": "nsfr_pct"}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BASE}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _run(client: TestClient, module: str, period_id: str) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        json={"module": module, "reporting_period_id": period_id, "scenario_code": "baseline"},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    if run["status"] != "succeeded":
        pytest.skip(f"the real book cannot currently compute a {module} run: {run.get('error')}")
    return run


def _forecast_year_zero(client: TestClient, period_id: str) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/forecast/runs",
        headers=real_headers(),
        json={"reporting_period_id": period_id, "scenario_code": "base"},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    if run["status"] != "succeeded":
        pytest.skip(f"the real book cannot currently project: {run.get('error')}")
    year_zero = run["path"][0]
    assert year_zero["year"] == 0
    return year_zero


@pytest.mark.parametrize(
    ("module", "parity"),
    [("capital", CAPITAL_PARITY), ("liquidity", LIQUIDITY_PARITY)],
    ids=["capital", "liquidity"],
)
def test_forecast_year_zero_equals_the_standalone_run(
    real_client: TestClient, module: str, parity: dict[str, str]
) -> None:
    period = _latest_period(real_client)
    run = _run(real_client, module, period["id"])
    year_zero = _forecast_year_zero(real_client, period["id"])
    for field, metric_key in parity.items():
        difference = _dec(year_zero[field]) - _dec(run["metrics"][metric_key])
        assert abs(difference) <= EXACT, (
            f"forecast year-0 {field} ({year_zero[field]}) != {module} run "
            f"{metric_key} ({run['metrics'][metric_key]}) for period {period['label']}"
        )


def test_the_forecast_snapshot_carries_the_capital_runs_input_scope(
    real_client: TestClient,
) -> None:
    """Provenance, not just arithmetic.

    The equality above is only reproducible if the forecast's sealed snapshot
    records the same inputs the capital run recorded. A projection that agreed
    on the number while hashing a narrower input set would be a coincidence
    waiting to expire.
    """
    period = _latest_period(real_client)
    capital = _run(real_client, "capital", period["id"])
    forecast = real_client.post(
        f"{BASE}/forecast/runs",
        headers=real_headers(),
        json={"reporting_period_id": period["id"], "scenario_code": "base"},
    )
    assert forecast.status_code == 201, forecast.text

    capital_groups = {fact["fact_group"] for fact in capital["inputs"]["facts"]}
    forecast_groups = {fact["fact_group"] for fact in forecast.json()["inputs"]["facts"]}
    missing = sorted(capital_groups - forecast_groups)
    assert not missing, (
        f"the forecast snapshot omits capital input groups {missing}; its year-0 CAR is "
        "then computed over a different book than the capital run's"
    )


def test_year_zero_capital_ratios_match_without_needing_the_liquidity_engine(
    real_client: TestClient, real_session: Session
) -> None:
    """The capital arm of the parity, isolated from liquidity availability.

    The projection computes LCR/NSFR for year 0 as well, so a liquidity
    parameter the real register is missing takes the whole projection down with
    it and the test above can only skip. The capital equality is independent of
    that, and it is the one the audit rated High — so it is proved here by
    driving the forecast's own loader and engine seam and handing the result to
    the capital engine, exactly as ``_regulatory_ratios`` does.

    This is also the only assertion in the suite that exercises the real book's
    ``ecl_exposure`` facts, which is where the divergence lived.
    """
    period_read = _latest_period(real_client)
    capital = _run(real_client, "capital", period_read["id"])

    real_session.info["organization_id"] = REAL_ORG_ID
    bank = real_session.get(Bank, REAL_BANK_ID)
    assert bank is not None
    period = real_session.get(BankReportingPeriod, UUID(period_read["id"]))
    assert period is not None

    ctx = TenantContext(organization_id=REAL_ORG_ID, actor_user_id=REAL_USER_ID)
    facts = regulatory_forecasting._load_facts(real_session, ctx, bank, period)
    assert any(fact.fact_group == "ecl_exposure" for fact in facts), (
        "the real book is expected to carry ecl_exposure facts; without them this test "
        "would prove the parity on a book that never had the divergence"
    )
    engine_facts = tuple(regulatory_forecasting._to_engine_fact(fact) for fact in facts)
    active = regulatory_forecasting._load_active_params(
        real_session, ctx, bank, period.period_end
    )
    params = regulatory_forecasting._forecast_engine_params(active)

    state, meta = _parse_facts(engine_facts)
    capital_facts = _to_capital_facts(_state_facts(state, meta))
    rwa = compute_rwa(capital_facts, params.capital)
    ratios = compute_capital_ratios(
        capital_facts, rwa, params.capital, _general_provisions_override(capital_facts, params)
    )

    metrics = capital["metrics"]
    assert ratios.car_pct == _dec(metrics["car_pct"])
    assert ratios.tier1_ratio_pct == _dec(metrics["tier1_ratio_pct"])
    assert ratios.cet1_ratio_pct == _dec(metrics["cet1_ratio_pct"])
    assert rwa.total_rwa == _dec(metrics["total_rwa_ghs"])
    assert rwa.credit_rwa == _dec(metrics["credit_rwa_ghs"])
