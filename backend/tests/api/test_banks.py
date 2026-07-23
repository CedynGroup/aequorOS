from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers


def _seed_demo_bank(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def test_seed_demo_is_refused_when_flag_off(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The product never seeds: data flows through the Data Engine. The endpoint
    exists solely as the test fixture behind DEMO_SEED_ENABLED (conftest turns it
    on for the suite); with the flag off — the default everywhere real — it must
    refuse."""
    monkeypatch.setenv("DEMO_SEED_ENABLED", "0")
    get_settings.cache_clear()
    r = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert r.status_code == 403
    assert "data engine" in r.json()["error"]["message"].lower()


def test_bank_read_resolves_jurisdiction_from_registry(db_client: TestClient) -> None:
    """Country identity is DATA: the bank's jurisdiction_code resolves through
    the jurisdictions registry to currency/locale/central bank — never
    hardcoded in clients."""
    _seed_demo_bank(db_client)
    bank = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}", headers=headers()).json()
    j = bank["jurisdiction"]
    assert j["code"] == "GH"
    assert j["country_name"] == "Ghana"
    assert j["currency_code"] == "GHS"
    assert j["locale"] == "en-GH"
    assert j["central_bank_name"] == "Bank of Ghana"
    assert j["regulator_short"] == "BoG"
    assert j["submission_portal"] == "ORASS"


def test_seed_demo_creates_sample_bank(db_client: TestClient) -> None:
    summary = _seed_demo_bank(db_client)
    assert summary["bank_id"] == str(SAMPLE_BANK_ID)
    assert summary["periods"] == 12
    assert summary["fact_count"] == 1308
    assert summary["param_count"] == 167

    response = db_client.get("/api/v1/banks", headers=headers())
    assert response.status_code == 200
    banks = response.json()["banks"]
    assert [bank["name"] for bank in banks] == ["Sample Bank Ltd"]
    assert banks[0]["id"] == str(SAMPLE_BANK_ID)
    assert banks[0]["short_name"] == "Sample Bank"
    assert banks[0]["currency"] == "GHS"
    assert banks[0]["jurisdiction_code"] == "GH"
    assert banks[0]["license_type"] == "universal"

    bank = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}", headers=headers())
    assert bank.status_code == 200
    assert bank.json()["name"] == "Sample Bank Ltd"


def test_seed_demo_is_idempotent_via_api(db_client: TestClient) -> None:
    first = _seed_demo_bank(db_client)
    second = _seed_demo_bank(db_client)
    assert second == first

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers())
    assert response.status_code == 200
    assert len(response.json()["periods"]) == 12


def test_seed_demo_is_forbidden_for_other_tenants(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers(ORG_2))
    assert response.status_code == 403

    assert db_client.get("/api/v1/banks", headers=headers(ORG_2)).json() == {"banks": []}


def test_reporting_periods_are_ordered_latest_first(db_client: TestClient) -> None:
    _seed_demo_bank(db_client)

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["bank_id"] == str(SAMPLE_BANK_ID)
    periods = payload["periods"]
    assert len(periods) == 12
    assert periods[0]["label"] == "2026-03"
    assert periods[0]["period_start"] == "2026-03-01"
    assert periods[0]["period_end"] == "2026-03-31"
    assert periods[0]["status"] == "open"
    assert periods[-1]["label"] == "2025-04"
    assert periods[-1]["period_end"] == "2025-04-30"
    assert all(period["status"] == "closed" for period in periods[1:])
    period_ends = [period["period_end"] for period in periods]
    assert period_ends == sorted(period_ends, reverse=True)


def test_latest_period_facts_are_grouped_with_canonical_amounts(
    db_client: TestClient,
) -> None:
    _seed_demo_bank(db_client)
    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    latest = periods[0]

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods/{latest['id']}/facts",
        headers=headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["period"]["id"] == latest["id"]
    assert payload["period"]["label"] == "2026-03"

    balance = {fact["category"]: fact for fact in payload["balance_sheet"]}
    assert len(balance) == 15
    assert _decimal(balance["loans_gross"]["amount"]) == Decimal("1400000000")
    assert balance["loans_gross"]["attributes"] == {"side": "asset"}
    assert _decimal(balance["capital_total"]["amount"]) == Decimal("340000000")
    assert balance["capital_total"]["attributes"] == {"side": "equity"}

    assert len(payload["loan_exposures"]) == 6
    assert len(payload["securities"]) == 4
    assert len(payload["off_balance"]) == 2
    assert len(payload["lcr_inflows"]) == 3
    assert len(payload["market_risk"]) == 2
    assert len(payload["operational_income"]) == 3
    assert len(payload["capital_components"]) == 9
    assert payload["deposit_behavior"] == []

    exposure_total = _decimal(0)
    for fact in payload["loan_exposures"]:
        exposure_total += _decimal(fact["amount"])
    assert exposure_total == Decimal("1400000000")

    securities = {fact["category"]: fact for fact in payload["securities"]}
    assert securities["bog_bills"]["hqla_level"] == "L1"
    assert securities["bog_bills"]["risk_weight_code"] == "RW0"
    assert securities["cash_vault_hqla"]["attributes"] == {"source": "cash"}
    hqla_total = _decimal(0)
    for fact in payload["securities"]:
        if fact["hqla_level"] is not None:
            hqla_total += _decimal(fact["amount"])
    assert hqla_total == Decimal("735000000")


def test_bank_endpoints_are_tenant_isolated(db_client: TestClient) -> None:
    _seed_demo_bank(db_client)

    assert db_client.get("/api/v1/banks", headers=headers(ORG_2)).json() == {"banks": []}
    assert (
        db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}", headers=headers(ORG_2)).status_code == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers(ORG_2)
        ).status_code
        == 404
    )

    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    ).json()["periods"]
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods/{periods[0]['id']}/facts",
            headers=headers(ORG_2),
        ).status_code
        == 404
    )


def test_unknown_bank_and_period_return_404(db_client: TestClient) -> None:
    _seed_demo_bank(db_client)

    assert db_client.get(f"/api/v1/banks/{uuid4()}", headers=headers()).status_code == 404
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods/{uuid4()}/facts",
            headers=headers(),
        ).status_code
        == 404
    )
