"""Tests for test_quota_tracker."""

from __future__ import annotations

from datetime import UTC, datetime

from app.identity.models.bank import Bank
from app.market_data.adapters.quota_tracker import (
    current_month_usage,
    record_consumption,
)
from app.market_data.models.market_data import MarketDataQuotaUsage
from tests.support.helpers import ORG_1


def test_record_and_read_consumption_roundtrip(db_session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Quota Test Bank",
        short_name="QTB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()

    when = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
    assert current_month_usage(db_session, ORG_1, bank.id, "bloomberg", when) == 0
    record_consumption(db_session, ORG_1, bank.id, "bloomberg", 7, when)
    record_consumption(db_session, ORG_1, bank.id, "bloomberg", 3, when)
    assert current_month_usage(db_session, ORG_1, bank.id, "bloomberg", when) == 10
    # A different vendor and a different month are accounted separately.
    assert current_month_usage(db_session, ORG_1, bank.id, "refinitiv", when) == 0
    next_month = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    assert current_month_usage(db_session, ORG_1, bank.id, "bloomberg", next_month) == 0
    record_consumption(db_session, ORG_1, bank.id, "bloomberg", 5, next_month)
    assert current_month_usage(db_session, ORG_1, bank.id, "bloomberg", next_month) == 5


def test_record_consumption_tracks_pull_count(db_session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Pull Count Bank",
        short_name="PCB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()

    when = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
    record_consumption(db_session, ORG_1, bank.id, "refinitiv", 4, when)
    record_consumption(db_session, ORG_1, bank.id, "refinitiv", 4, when)
    row = (
        db_session.query(MarketDataQuotaUsage)
        .filter(MarketDataQuotaUsage.vendor == "refinitiv")
        .one()
    )
    assert row.units_consumed == 8
    assert row.pull_count == 2
    assert row.month == "2026-07"
