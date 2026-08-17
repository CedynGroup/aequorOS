"""Bank identity + reporting-period + facts API against the ACTUAL primary.

Invariants (never magnitudes): the real bank's identity/jurisdiction resolve from
the registry; periods are contiguous latest-first; the facts view honours the
derivation relationships (loans_gross = Σ loan exposures, HQLA mirrors of the
balance sheet, assets = liabilities + equity); tenant isolation; clean 404s; and
the retired seed route stays retired. Opt-in via REAL_DATA_DATABASE_URL, rolled
back on teardown (see tests/real_data.py).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankFinancialFact, Jurisdiction
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

# Groups the canonical facts view surfaces (schemas/banks.py::BankFactsRead).
FACT_VIEW_FIELDS = {
    "balance_sheet",
    "loan_exposures",
    "securities",
    "off_balance",
    "lcr_inflows",
    "market_risk",
    "operational_income",
    "cash_flows",
    "capital_components",
    "deposit_behavior",
}
# balance_sheet ↔ securities mirrors written by fact derivation: the HQLA leg
# of a cash/securities line must equal the balance-sheet line it mirrors.
HQLA_MIRRORS = {
    "bog_bills": "securities_bog_bills",
    "gog_bonds": "securities_gog_bonds",
    "cash_vault_hqla": "cash_vault",
    "bog_excess_reserves_hqla": "bog_excess_reserves",
}


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _get_bank(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}", headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["bank_id"] == REAL_BANK_ID
    assert payload["periods"], "the real Sample Bank must have reporting periods"
    return payload["periods"]


def _facts(client: TestClient, period_id: str) -> Any:
    return client.get(
        f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods/{period_id}/facts",
        headers=real_headers(),
    )


def _period_ids_with_group(session: Session, fact_group: str) -> set[UUID]:
    session.info["organization_id"] = REAL_ORG_ID
    return set(
        session.scalars(
            select(BankFinancialFact.reporting_period_id)
            .where(
                BankFinancialFact.bank_id == REAL_BANK_ID,
                BankFinancialFact.fact_group == fact_group,
            )
            .distinct()
        )
    )


def test_seed_route_is_retired(real_client: TestClient) -> None:
    """The product never seeds: data flows through the Data Engine (order of
    2026-07-21) and the hermetic seed endpoint itself was retired 2026-08-15.
    The path must not resolve to any handler — for any role, any tenant."""
    for headers in (real_headers(), real_headers(roles=("analyst",)), other_headers()):
        response = real_client.post("/api/v1/banks/seed-demo", headers=headers)
        assert response.status_code in (404, 405), response.text


def test_bank_read_resolves_jurisdiction_from_registry(
    real_client: TestClient, real_session: Session
) -> None:
    """Country identity is DATA: the bank's jurisdiction_code resolves through
    the jurisdictions registry to currency/locale/central bank — never
    hardcoded in clients. Compared against the registry row itself, so the
    assertion holds for a bank in any jurisdiction."""
    bank = _get_bank(real_client)
    registry = real_session.get(Jurisdiction, bank["jurisdiction_code"])
    assert registry is not None, "the real bank's jurisdiction must be registered"

    j = bank["jurisdiction"]
    assert j["code"] == bank["jurisdiction_code"] == registry.code
    assert j["country_name"] == registry.country_name
    assert j["currency_code"] == registry.currency_code == bank["currency"]
    assert j["currency_name"] == registry.currency_name
    assert j["locale"] == registry.locale
    assert j["central_bank_name"] == registry.central_bank_name
    assert j["regulator_short"] == registry.regulator_short
    assert j["submission_portal"] == registry.submission_portal
    assert j["timezone"] == registry.timezone


def test_bank_list_and_detail_carry_the_platform_identity(real_client: TestClient) -> None:
    """One identity: the platform ID is the list key, the detail key and the
    path token; the tenant's list contains the real bank with a consistent
    detail read."""
    response = real_client.get("/api/v1/banks", headers=real_headers())
    assert response.status_code == 200, response.text
    banks = {bank["id"]: bank for bank in response.json()["banks"]}
    assert REAL_BANK_ID in banks
    listed = banks[REAL_BANK_ID]
    assert listed["organization_id"] == REAL_ORG_ID
    assert listed["name"] and listed["short_name"]
    assert len(listed["currency"]) == 3
    assert listed["jurisdiction_code"]
    assert listed["license_type"]
    assert "public_id" not in listed

    detail = _get_bank(real_client)
    assert detail == listed


def test_reporting_periods_are_ordered_latest_first(real_client: TestClient) -> None:
    periods = _periods(real_client)
    period_ends = [period["period_end"] for period in periods]
    assert period_ends == sorted(period_ends, reverse=True)
    assert len(set(period_ends)) == len(period_ends)  # one row per period end
    for period in periods:
        assert period["bank_id"] == REAL_BANK_ID
        assert period["period_start"] <= period["period_end"]
        assert period["status"] in {"open", "closed"}
        # A monthly period's label is its calendar month.
        assert period["label"] == period["period_end"][:7]
    # The spine is contiguous: each period starts right after the previous ends.
    for later, earlier in zip(periods, periods[1:], strict=False):
        assert earlier["period_end"] < later["period_start"] <= later["period_end"]


def test_period_facts_are_grouped_with_canonical_relationships(
    real_client: TestClient, real_session: Session
) -> None:
    """The facts view groups by fact_group and the derivation relationships hold
    on any real book: loans_gross = Σ loan exposures; every HQLA mirror equals
    the balance-sheet line it mirrors; assets = liabilities + equity; HQLA rows
    carry a level. Runs on the latest period the view's schema admits (see the
    strict-xfail companion below for the ``cashflow`` gap)."""
    periods = _periods(real_client)
    with_cashflow = _period_ids_with_group(real_session, "cashflow")
    period = next(p for p in periods if UUID(p["id"]) not in with_cashflow)

    response = _facts(real_client, period["id"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == FACT_VIEW_FIELDS | {"period"}
    assert payload["period"]["id"] == period["id"]
    assert payload["period"]["label"] == period["label"]

    balance = {fact["category"]: fact for fact in payload["balance_sheet"]}
    assert balance, "a real period must carry balance-sheet facts"
    for fact in payload["balance_sheet"]:
        assert fact["fact_group"] == "balance_sheet"
        assert fact["attributes"]["side"] in {"asset", "liability", "equity"}
        assert fact["currency"] == payload["balance_sheet"][0]["currency"]
    sides: dict[str, Decimal] = {"asset": Decimal(0), "liability": Decimal(0), "equity": Decimal(0)}
    for fact in payload["balance_sheet"]:
        sides[fact["attributes"]["side"]] += _decimal(fact["amount"])
    assert sides["asset"] == sides["liability"] + sides["equity"]

    exposures = payload["loan_exposures"]
    assert exposures
    assert all(fact["fact_group"] == "loan_exposure" for fact in exposures)
    exposure_total = sum((_decimal(fact["amount"]) for fact in exposures), Decimal(0))
    assert exposure_total == _decimal(balance["loans_gross"]["amount"])

    securities = {fact["category"]: fact for fact in payload["securities"]}
    assert securities
    for category, mirrored in HQLA_MIRRORS.items():
        if category in securities and mirrored in balance:
            assert _decimal(securities[category]["amount"]) == _decimal(
                balance[mirrored]["amount"]
            ), category
    for fact in payload["securities"]:
        assert fact["hqla_level"] in {None, "L1", "L2A", "L2B"}
        if fact["hqla_level"] == "L1":
            assert fact["risk_weight_code"] == "RW0"

    for field in ("off_balance", "lcr_inflows", "capital_components"):
        assert payload[field], f"real period must carry {field}"
    assert {fact["fact_group"] for fact in payload["capital_components"]} == {"capital_component"}


# (was a strict xfail pinning the BankFactGroup/'cashflow' Literal gap — fixed
# 2026-08-16 in app/schemas/banks.py; the test now guards the fix.)
def test_period_facts_view_admits_cashflow_facts(
    real_client: TestClient, real_session: Session
) -> None:
    with_cashflow = _period_ids_with_group(real_session, "cashflow")
    if not with_cashflow:
        pytest.skip("no period on the real bank carries cashflow facts")
    period = next(p for p in _periods(real_client) if UUID(p["id"]) in with_cashflow)
    response = _facts(real_client, period["id"])
    assert response.status_code == 200, response.text
    assert response.json()["cash_flows"]


def test_bank_endpoints_are_tenant_isolated(real_client: TestClient) -> None:
    other = other_headers()
    listed = real_client.get("/api/v1/banks", headers=other)
    assert listed.status_code == 200
    assert REAL_BANK_ID not in {bank["id"] for bank in listed.json()["banks"]}
    assert real_client.get(f"/api/v1/banks/{REAL_BANK_ID}", headers=other).status_code == 404
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=other
        ).status_code
        == 404
    )

    periods = _periods(real_client)
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods/{periods[0]['id']}/facts",
            headers=other,
        ).status_code
        == 404
    )


def test_unknown_bank_and_period_return_404(real_client: TestClient) -> None:
    assert real_client.get(f"/api/v1/banks/{uuid4()}", headers=real_headers()).status_code == 404
    assert (
        real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods/{uuid4()}/facts",
            headers=real_headers(),
        ).status_code
        == 404
    )
