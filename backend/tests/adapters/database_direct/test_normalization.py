"""Value normalization: charset decode, timezone conversion, locale numbers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from app.adapters.database_direct.normalization import (
    NormalizationPolicy,
    normalize_row,
    normalize_value,
)


class TestCharset:
    def test_non_utf_bytes_decoded_with_source_encoding(self) -> None:
        policy = NormalizationPolicy(source_encoding="cp1252")
        # 0x92 is a curly apostrophe in Windows-1252 and an invalid UTF-8 byte.
        raw = b"Amma\x92s Shop"
        assert normalize_value(raw, policy) == "Amma’s Shop"

    def test_undecodable_bytes_replaced_not_fatal(self) -> None:
        policy = NormalizationPolicy(source_encoding="ascii")
        assert normalize_value(b"caf\xe9", policy)  # replacement char, no raise


class TestTimezone:
    def test_naive_timestamp_localized_then_converted_to_utc(self) -> None:
        # UTC+1 wall clock 09:30 -> 08:30Z.
        policy = NormalizationPolicy(source_utc_offset_minutes=60)
        result = normalize_value(datetime(2026, 6, 30, 9, 30), policy)
        assert result == "2026-06-30T08:30:00+00:00"

    def test_aware_timestamp_converted_to_utc(self) -> None:
        policy = NormalizationPolicy()
        aware = datetime(2026, 6, 30, 9, 30, tzinfo=timezone(_hours(-5)))
        assert normalize_value(aware, policy) == "2026-06-30T14:30:00+00:00"

    def test_naive_without_offset_stays_naive_iso(self) -> None:
        policy = NormalizationPolicy(source_utc_offset_minutes=None)
        assert normalize_value(datetime(2026, 6, 30, 9, 30), policy) == "2026-06-30T09:30:00"


class TestLocaleNumbers:
    def test_european_number_text_normalized_for_configured_columns(self) -> None:
        policy = NormalizationPolicy(
            decimal_separator=",",
            grouping_separator=".",
            locale_number_columns=("BAL",),
        )
        row = {"BAL": "1.234.567,89", "REF": "1.234-A"}
        out = normalize_row(row, policy)
        assert out["BAL"] == "1234567.89"
        # a non-number-text column is untouched even if it looks numeric-ish.
        assert out["REF"] == "1.234-A"

    def test_negative_and_plain_values(self) -> None:
        policy = NormalizationPolicy(
            decimal_separator=",", grouping_separator=".", locale_number_columns=("X",)
        )
        assert normalize_row({"X": "-42,50"}, policy)["X"] == "-42.50"
        assert normalize_row({"X": "1000"}, policy)["X"] == "1000"

    def test_non_numeric_text_is_left_alone(self) -> None:
        policy = NormalizationPolicy(locale_number_columns=("X",))
        assert normalize_row({"X": "GH-BRANCH-01"}, policy)["X"] == "GH-BRANCH-01"


class TestScalarPassthrough:
    def test_decimal_preserved_as_exact_string(self) -> None:
        assert normalize_value(Decimal("1200000.500000"), NormalizationPolicy()) == "1200000.500000"

    def test_date_iso(self) -> None:
        assert normalize_value(date(2026, 6, 30), NormalizationPolicy()) == "2026-06-30"

    def test_none_and_native_scalars(self) -> None:
        policy = NormalizationPolicy()
        assert normalize_value(None, policy) is None
        assert normalize_value(42, policy) == 42
        assert normalize_value(True, policy) is True

    def test_utc_aware_datetime_direct(self) -> None:
        policy = NormalizationPolicy()
        result = normalize_value(datetime(2026, 1, 1, tzinfo=UTC), policy)
        assert result == "2026-01-01T00:00:00+00:00"


def _hours(n: int) -> timedelta:
    return timedelta(hours=n)
