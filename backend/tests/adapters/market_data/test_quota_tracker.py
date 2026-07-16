from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.market_data.quota_tracker import (
    PULLS_PER_MONTH_BY_FREQUENCY,
    current_month_usage,
    estimate,
    month_key,
    record_consumption,
)
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency
from app.adapters.market_data.scope_translator import Catalog, CatalogEntry
from app.models.market_data import MarketDataQuotaUsage
from app.models.regulatory import Bank
from tests.api.helpers import ORG_1


def _catalog() -> Catalog:
    entries = {
        DataScope.YIELD_CURVE_GHS: CatalogEntry(
            scope=DataScope.YIELD_CURVE_GHS,
            supported=True,
            quota_units_per_pull=7,
            requests=(),
        ),
        DataScope.FX_SPOT_USD_GHS: CatalogEntry(
            scope=DataScope.FX_SPOT_USD_GHS,
            supported=True,
            quota_units_per_pull=1,
            requests=(),
        ),
    }
    return Catalog(source_path="test", entries=entries)


def test_pulls_per_month_constants() -> None:
    assert PULLS_PER_MONTH_BY_FREQUENCY == {
        PullFrequency.ON_DEMAND: 1,
        PullFrequency.HOURLY: 176,  # 8 business-hour pulls x 22 business days
        PullFrequency.END_OF_DAY: 22,
        PullFrequency.WEEKLY: 4,
        PullFrequency.MONTHLY: 1,
    }


@pytest.mark.parametrize(
    ("frequency", "expected_monthly"),
    [
        (PullFrequency.ON_DEMAND, 8),
        (PullFrequency.HOURLY, 8 * 176),
        (PullFrequency.END_OF_DAY, 8 * 22),
        (PullFrequency.WEEKLY, 8 * 4),
        (PullFrequency.MONTHLY, 8),
    ],
)
def test_estimate_monthly_math(frequency: PullFrequency, expected_monthly: int) -> None:
    result = estimate(
        _catalog(),
        [DataScope.YIELD_CURVE_GHS, DataScope.FX_SPOT_USD_GHS],
        frequency,
        current_consumption=0,
        cap=None,
    )
    assert result.estimated_units_per_pull == 8
    assert result.estimated_monthly_units == expected_monthly
    assert result.within_cap is True


def test_estimate_without_cap_is_always_within_cap() -> None:
    result = estimate(
        _catalog(), [DataScope.YIELD_CURVE_GHS], PullFrequency.HOURLY, 10_000_000, cap=None
    )
    assert result.within_cap is True
    assert result.monthly_cap is None


def test_estimate_over_cap_warns_never_raises() -> None:
    # 7 units x 22 EOD pulls = 154 monthly; 100 already used against a 200 cap.
    result = estimate(
        _catalog(), [DataScope.YIELD_CURVE_GHS], PullFrequency.END_OF_DAY, 100, cap=200
    )
    assert result.estimated_monthly_units == 154
    assert result.within_cap is False
    assert result.current_monthly_consumption == 100
    assert result.monthly_cap == 200


def test_estimate_exactly_at_cap_is_within_cap() -> None:
    result = estimate(
        _catalog(), [DataScope.YIELD_CURVE_GHS], PullFrequency.END_OF_DAY, 46, cap=200
    )
    assert result.estimated_monthly_units == 154
    assert result.within_cap is True


def test_estimate_unknown_scope_contributes_zero() -> None:
    result = estimate(_catalog(), [DataScope.YIELD_CURVE_ZAR], PullFrequency.MONTHLY, 0, cap=10)
    assert result.estimated_units_per_pull == 0
    assert result.within_cap is True


def test_month_key_is_utc() -> None:
    late_pacific = datetime(2026, 7, 31, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert month_key(late_pacific) == "2026-08"
    assert month_key(datetime(2026, 7, 15, 12, 0, tzinfo=UTC)) == "2026-07"


def test_record_and_read_consumption_roundtrip(db_session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Quota Test Bank",
        short_name="QTB",
        license_type="universal",
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
        license_type="universal",
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
