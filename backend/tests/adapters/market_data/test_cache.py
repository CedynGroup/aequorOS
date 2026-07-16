from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.adapters.market_data.cache import (
    FRESHNESS_BY_CATEGORY,
    StalenessTag,
    cache_location,
    fresh_until,
    is_fresh,
    next_business_day,
    read_cache_entry,
    staleness_tag,
    write_cache_entry,
)
from app.adapters.market_data.scope_taxonomy import DataScope, ScopeCategory
from tests.storage.inmemory import InMemoryStorageClient

BANK = "sbl-gh-001"


def test_freshness_table_covers_every_category() -> None:
    assert set(FRESHNESS_BY_CATEGORY) == set(ScopeCategory)


def test_freshness_windows_match_spec() -> None:
    # market_data_adapter.md §11.4.
    assert FRESHNESS_BY_CATEGORY[ScopeCategory.FX_SPOT] == timedelta(hours=1)
    assert FRESHNESS_BY_CATEGORY[ScopeCategory.SECURITY_MASTER] == timedelta(days=7)
    assert FRESHNESS_BY_CATEGORY[ScopeCategory.CREDIT_RATING] == timedelta(days=1)
    assert FRESHNESS_BY_CATEGORY[ScopeCategory.MACRO_FORECAST] == timedelta(days=30)


def test_next_business_day_skips_weekend() -> None:
    friday = date(2026, 7, 10)
    assert friday.weekday() == 4
    assert next_business_day(friday) == date(2026, 7, 13)  # Monday
    assert next_business_day(date(2026, 7, 13)) == date(2026, 7, 14)  # Mon -> Tue
    assert next_business_day(date(2026, 7, 11)) == date(2026, 7, 13)  # Sat -> Mon


def test_curve_fresh_until_end_of_next_business_day() -> None:
    tuesday_pull = datetime(2026, 6, 30, 17, 5, tzinfo=UTC)
    bound = fresh_until(ScopeCategory.YIELD_CURVE, tuesday_pull)
    assert bound == datetime(2026, 7, 1, 23, 59, 59, tzinfo=UTC)


def test_curve_business_day_rollover_across_weekend() -> None:
    friday_pull = datetime(2026, 7, 10, 17, 5, tzinfo=UTC)
    bound = fresh_until(ScopeCategory.YIELD_CURVE, friday_pull)
    # Next business day after Friday is Monday; curve stays fresh through Monday.
    assert bound == datetime(2026, 7, 13, 23, 59, 59, tzinfo=UTC)
    saturday = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    monday_noon = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    tuesday_morning = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    assert is_fresh(friday_pull, ScopeCategory.YIELD_CURVE, saturday)
    assert is_fresh(friday_pull, ScopeCategory.YIELD_CURVE, monday_noon)
    assert not is_fresh(friday_pull, ScopeCategory.YIELD_CURVE, tuesday_morning)


def test_fx_forward_uses_business_day_rule_like_curves() -> None:
    friday_pull = datetime(2026, 7, 10, 17, 5, tzinfo=UTC)
    assert fresh_until(ScopeCategory.FX_FORWARD, friday_pull) == fresh_until(
        ScopeCategory.YIELD_CURVE, friday_pull
    )


def test_fx_spot_stale_after_one_hour() -> None:
    pulled = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    assert is_fresh(pulled, ScopeCategory.FX_SPOT, pulled + timedelta(minutes=59))
    assert is_fresh(pulled, ScopeCategory.FX_SPOT, pulled + timedelta(hours=1))
    assert not is_fresh(pulled, ScopeCategory.FX_SPOT, pulled + timedelta(hours=1, seconds=1))


def test_staleness_tag_attribution() -> None:
    pulled = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    now = pulled + timedelta(hours=3)
    tag = staleness_tag(pulled, ScopeCategory.FX_SPOT, now, source_batch_id="b-001")
    assert tag == StalenessTag(stale=True, age=timedelta(hours=3), source_batch_id="b-001")
    fresh_tag = staleness_tag(pulled, ScopeCategory.MACRO_FORECAST, now, source_batch_id="b-001")
    assert not fresh_tag.stale


def test_cache_location_is_well_known_canonical_path() -> None:
    location = cache_location(BANK, DataScope.YIELD_CURVE_GHS)
    assert location.institution_slug == BANK
    assert location.tier == "canonical"
    assert location.object_path == "market_data/cache/YIELD_CURVE_GHS.json"


def test_write_and_read_cache_entry_roundtrip() -> None:
    client = InMemoryStorageClient()
    pulled_at = datetime(2026, 7, 14, 17, 0, tzinfo=UTC)
    write_cache_entry(
        BANK,
        DataScope.FX_SPOT_USD_GHS,
        as_of_date=date(2026, 7, 14),
        values={"mid": "12.85"},
        pulled_at=pulled_at,
        source_batch_id="b-2026-07-14-eod-001",
        vendor="bloomberg",
        client=client,
    )
    payload = read_cache_entry(BANK, DataScope.FX_SPOT_USD_GHS, client=client)
    assert payload is not None
    assert payload["scope"] == "FX_SPOT_USD_GHS"
    assert payload["as_of_date"] == "2026-07-14"
    assert payload["values"] == {"mid": "12.85"}
    assert payload["pulled_at"] == pulled_at.isoformat()
    assert payload["fresh_until"] == (pulled_at + timedelta(hours=1)).isoformat()
    assert payload["source_batch_id"] == "b-2026-07-14-eod-001"
    assert payload["vendor"] == "bloomberg"


def test_write_cache_entry_supersedes_previous_value() -> None:
    client = InMemoryStorageClient()
    for value, batch in (("12.80", "b-001"), ("12.95", "b-002")):
        write_cache_entry(
            BANK,
            DataScope.FX_SPOT_USD_GHS,
            as_of_date=date(2026, 7, 14),
            values={"mid": value},
            pulled_at=datetime(2026, 7, 14, 17, 0, tzinfo=UTC),
            source_batch_id=batch,
            vendor="bloomberg",
            client=client,
        )
    payload = read_cache_entry(BANK, DataScope.FX_SPOT_USD_GHS, client=client)
    assert payload is not None
    assert payload["values"] == {"mid": "12.95"}
    assert payload["source_batch_id"] == "b-002"


def test_read_cache_entry_missing_returns_none() -> None:
    client = InMemoryStorageClient()
    assert read_cache_entry(BANK, DataScope.YIELD_CURVE_USD, client=client) is None


def test_cache_entry_metadata_carries_lineage_fields() -> None:
    client = InMemoryStorageClient()
    write_cache_entry(
        BANK,
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
        as_of_date=date(2026, 7, 14),
        values={"sp": "CCC+"},
        pulled_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        source_batch_id="b-003",
        vendor="refinitiv",
        client=client,
    )
    metadata = client.get_metadata(cache_location(BANK, DataScope.CREDIT_RATING_GHANA_SOVEREIGN))
    assert metadata.source_system == "REFINITIV"
    assert metadata.source_reference == "CREDIT_RATING_GHANA_SOVEREIGN"
    assert metadata.ingestion_batch_id == "b-003"
    assert metadata.as_of_date == "2026-07-14"
