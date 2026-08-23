"""Contractual cash-flow windows: real maturities between two user dates.

The service reads the freshest current-generation book (blotter semantics) and
buckets contractual maturities into per-currency calendar months. The canonical
fixture (as-of 2026-06-30) carries hand-checkable maturities:

Inflows (asset side):        Outflows (liability side):
  LOAN/1   30M GHS 2029-06-30   DEP/4   10M GHS 2026-09-30
  LOAN/2   10M GHS 2028-06-30   DEP/5    8M GHS 2026-12-31
  LOAN/3    8M GHS 2026-07-20   IBB/1    6M GHS 2026-08-29
  LOAN/4   12M GHS 2040-01-01
  LOAN/5    9M GHS 2027-03-31
  LOAN/6    3M GHS 2027-01-31
  LOAN/USD  1M USD 2028-06-30
  SEC/1    15M GHS 2026-08-29
  SEC/2    20M GHS 2029-06-30
  IBP/1     5M GHS 2026-07-15

Excluded always: LOAN/OLD (superseded snapshot; matures 2029-01-01), LOAN/BAD
(error status; matures 2029-01-01), LC/1 (LC_GUARANTEE — off-balance type).
No contractual maturity (the honesty stat): DEP/1, DEP/2, DEP/3, DEP/USD = 4.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.session import get_sessionmaker
from app.services import cashflow_window
from tests.api.helpers import headers
from tests.factories.canonical import seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

TENANT = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
NO_MATURITY_COUNT = 4  # DEP/1, DEP/2, DEP/3, DEP/USD


def _seed(db: Session) -> None:
    materialize_canonical_test_book(db)
    db.flush()
    seed_canonical_fixture(db, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    db.flush()


def test_window_splits_covered_and_excluded_maturities(db_session: Session) -> None:
    """H2-2026 window: July–December flows in, everything 2027+ out."""
    _seed(db_session)

    result = cashflow_window.compute_cashflow_window(
        db_session,
        TENANT,
        SAMPLE_BANK_ID,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 12, 31),
    )

    assert result.bank_id == SAMPLE_BANK_ID
    # LOAN/3 + IBP/1 + SEC/1 in; IBB/1 + DEP/4 + DEP/5 out — six positions.
    assert result.position_count == 6
    assert result.no_maturity_count == NO_MATURITY_COUNT

    # Only GHS has flows in this window, over a full six-month spine.
    assert [total.currency for total in result.totals] == ["GHS"]
    ghs = result.totals[0]
    assert ghs.inflows == Decimal("28000000")
    assert ghs.outflows == Decimal("24000000")
    assert ghs.net == Decimal("4000000")
    assert ghs.position_count == 6

    by_month = {row.month: row for row in result.months}
    assert len(result.months) == 6  # Jul..Dec, one currency
    assert all(row.currency == "GHS" for row in result.months)
    jul = by_month[date(2026, 7, 1)]
    assert (jul.inflows, jul.outflows, jul.net) == (
        Decimal("13000000"),  # LOAN/3 8M + IBP/1 5M
        Decimal("0"),
        Decimal("13000000"),
    )
    aug = by_month[date(2026, 8, 1)]
    assert (aug.inflows, aug.outflows, aug.net) == (
        Decimal("15000000"),  # SEC/1
        Decimal("6000000"),  # IBB/1
        Decimal("9000000"),
    )
    sep = by_month[date(2026, 9, 1)]
    assert (sep.inflows, sep.outflows, sep.net) == (
        Decimal("0"),
        Decimal("10000000"),  # DEP/4
        Decimal("-10000000"),
    )
    dec = by_month[date(2026, 12, 1)]
    assert (dec.inflows, dec.outflows, dec.net) == (
        Decimal("0"),
        Decimal("8000000"),  # DEP/5
        Decimal("-8000000"),
    )
    # The spine keeps flow-free months as honest zeros, never omits them.
    for month in (date(2026, 10, 1), date(2026, 11, 1)):
        row = by_month[month]
        assert (row.inflows, row.outflows, row.net) == (Decimal("0"), Decimal("0"), Decimal("0"))

    # All six in-window positions carry an ingested base leg (all GHS here).
    assert result.overall.currency == "GHS"
    assert result.overall.inflows == Decimal("28000000")
    assert result.overall.outflows == Decimal("24000000")
    assert result.overall.net == Decimal("4000000")
    assert result.overall.unconverted_count == 0


def test_multi_currency_window_reports_per_currency(db_session: Session) -> None:
    """2028 window: LOAN/2 (GHS) and LOAN/USD (USD) split by currency."""
    _seed(db_session)

    result = cashflow_window.compute_cashflow_window(
        db_session,
        TENANT,
        SAMPLE_BANK_ID,
        start_date=date(2028, 1, 1),
        end_date=date(2028, 12, 31),
    )

    assert result.position_count == 2
    totals = {total.currency: total for total in result.totals}
    assert set(totals) == {"GHS", "USD"}
    assert totals["GHS"].inflows == Decimal("10000000")  # LOAN/2, own currency
    assert totals["GHS"].outflows == Decimal("0")
    assert totals["USD"].inflows == Decimal("1000000")  # LOAN/USD, own currency
    assert totals["USD"].outflows == Decimal("0")
    # 12-month spine per active currency.
    assert len(result.months) == 24
    jun_usd = next(
        row for row in result.months if row.currency == "USD" and row.month == date(2028, 6, 1)
    )
    assert jun_usd.inflows == Decimal("1000000")
    # Overall totals ride the ingested balance_ghs leg: 10M + 12.85M.
    assert result.overall.inflows == Decimal("22850000")
    assert result.overall.unconverted_count == 0


def test_superseded_and_error_snapshots_never_contribute(db_session: Session) -> None:
    """LOAN/OLD (superseded) and LOAN/BAD (error) both mature 2029-01-01 — the
    January cell must stay zero; only LOAN/1 + SEC/2 (2029-06-30) appear."""
    _seed(db_session)

    result = cashflow_window.compute_cashflow_window(
        db_session,
        TENANT,
        SAMPLE_BANK_ID,
        start_date=date(2029, 1, 1),
        end_date=date(2029, 6, 30),
    )

    assert result.position_count == 2
    assert [total.currency for total in result.totals] == ["GHS"]
    assert result.totals[0].inflows == Decimal("50000000")  # LOAN/1 30M + SEC/2 20M
    jan = next(row for row in result.months if row.month == date(2029, 1, 1))
    assert (jan.inflows, jan.outflows) == (Decimal("0"), Decimal("0"))
    jun = next(row for row in result.months if row.month == date(2029, 6, 1))
    assert jun.inflows == Decimal("50000000")


def test_inverted_window_is_422(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as excinfo:
        cashflow_window.compute_cashflow_window(
            db_session,
            TENANT,
            SAMPLE_BANK_ID,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 7, 31),
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error_code"] == "invalid_window"  # type: ignore[index]


def test_oversized_window_is_422(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as excinfo:
        cashflow_window.compute_cashflow_window(
            db_session,
            TENANT,
            SAMPLE_BANK_ID,
            start_date=date(2023, 1, 1),
            end_date=date(2026, 6, 30),
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error_code"] == "window_too_large"  # type: ignore[index]


def test_empty_window_is_valid_200_with_book_honesty_stat(db_session: Session) -> None:
    """A window with no maturities returns empty lists — but the no-maturity
    count is a property of the book, so it still reports."""
    _seed(db_session)

    result = cashflow_window.compute_cashflow_window(
        db_session,
        TENANT,
        SAMPLE_BANK_ID,
        start_date=date(2010, 1, 1),
        end_date=date(2010, 12, 31),
    )

    assert result.position_count == 0
    assert result.months == []
    assert result.totals == []
    assert result.overall.inflows == Decimal("0")
    assert result.overall.outflows == Decimal("0")
    assert result.overall.unconverted_count == 0
    assert result.no_maturity_count == NO_MATURITY_COUNT


def test_endpoint_wiring_serializes_decimals_as_strings(db_client: TestClient) -> None:
    """GET /banks/{id}/analytics/cashflow-window through the router."""
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/analytics/cashflow-window",
        params={"start_date": "2026-07-01", "end_date": "2026-12-31"},
        headers=headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["bank_id"] == SAMPLE_BANK_ID
    assert payload["no_maturity_count"] == NO_MATURITY_COUNT
    assert payload["totals"][0]["currency"] == "GHS"
    assert isinstance(payload["totals"][0]["inflows"], str)
    assert Decimal(payload["totals"][0]["inflows"]) == Decimal("28000000")
    assert isinstance(payload["months"][0]["net"], str)
    assert isinstance(payload["overall"]["net"], str)

    inverted = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/analytics/cashflow-window",
        params={"start_date": "2026-12-31", "end_date": "2026-07-01"},
        headers=headers(),
    )
    assert inverted.status_code == 422
