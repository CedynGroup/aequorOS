"""Source-layer core: registry contract, validation layer, write policy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import SOURCE_REGISTRY, FetchMethod
from app.services.market_desk.sources.core import (
    QF_OUT_OF_RANGE,
    QF_SOURCE_CONFLICT,
    QF_STALE_SOURCE,
    RECONCILIATION_RULES,
    ObservationDraft,
    ParseResult,
    apply_range_bounds,
    apply_staleness,
    dedupe_and_resolve_conflicts,
    quantize_value,
    slugify,
)

# Spec §3 Tier-1: every must-have Ghana source is registered.
EXPECTED_SOURCE_KEYS = {
    "bog_tbill_rates",
    "bog_bill_rates",
    "bog_interbank_daily",
    "bog_interbank_weekly",
    "bog_mpr",
    "bog_fx_daily",
    "bog_fx_historical",
    "bog_fx_reference",
    "bog_econ_interest_monthly",
    "bog_gog_auction_pdf",
    "bog_bog_auction_pdf",
    "bog_apr_pdf",
    "bog_sefd_pdf",
    "gfim_daily_xlsx",
    "gfim_monthly_status",
    "gss_interest_px",
}


def _draft(
    series: str = "GHS.TEST",
    as_of: date = date(2026, 8, 7),
    value: str = "10.00",
    unit: str = "pct",
) -> ObservationDraft:
    return ObservationDraft(
        series_code=series, as_of_date=as_of, value=Decimal(value), unit=unit
    )


class TestSourceRegistry:
    def test_every_tier1_source_is_registered(self) -> None:
        assert set(SOURCE_REGISTRY) == EXPECTED_SOURCE_KEYS

    def test_every_source_declares_manual_fallback_and_owned_series(self) -> None:
        # Spec §3: "a manual-entry fallback for every source" — the UI
        # renders entry forms from series_codes, so both must be present.
        for spec in SOURCE_REGISTRY.values():
            assert spec.manual_fallback is True, spec.source_key
            assert spec.series_codes, spec.source_key

    def test_registry_keys_match_specs_and_parsers_are_bound(self) -> None:
        for key, spec in SOURCE_REGISTRY.items():
            assert spec.source_key == key
            assert spec.parser is not None, key
            assert spec.parser_version
            assert spec.cadence in {"daily", "weekly", "monthly", "per_event"}
            assert isinstance(spec.fetch_method, FetchMethod)

    def test_derived_table63_is_documented_as_unsupported_not_parsed(self) -> None:
        # README: table 63 is table 62 minus exactly 200bps — a derived
        # corridor-floor display. It must not exist as an independent source.
        assert not any("63" in key for key in SOURCE_REGISTRY)
        assert any("table 63" in note for note in SOURCE_REGISTRY["bog_mpr"].unsupported)


class TestDedupeAndConflicts:
    def test_exact_duplicates_dropped_and_counted(self) -> None:
        result = ParseResult()
        kept = dedupe_and_resolve_conflicts([_draft(), _draft()], result)
        assert len(kept) == 1
        assert result.warnings == ["dropped 1 exact duplicate row(s)"]

    def test_same_date_conflict_keeps_last_and_flags_both_values(self) -> None:
        # The table-69 2020-02-25 case: 16.14 vs 16.12 on one date.
        result = ParseResult()
        kept = dedupe_and_resolve_conflicts(
            [_draft(value="16.14"), _draft(value="16.12")], result
        )
        assert len(kept) == 1
        assert kept[0].value == Decimal("16.12")
        assert QF_SOURCE_CONFLICT in kept[0].quality_flags
        assert kept[0].attributes["conflicting_values"] == ["16.14", "16.12"]

    def test_distinct_dates_pass_through_untouched(self) -> None:
        result = ParseResult()
        kept = dedupe_and_resolve_conflicts(
            [_draft(as_of=date(2026, 8, 6)), _draft(as_of=date(2026, 8, 7))], result
        )
        assert len(kept) == 2
        assert result.warnings == []


class TestRangeBounds:
    def test_rate_outside_0_60_is_flagged_never_dropped(self) -> None:
        result = ParseResult()
        drafts = [_draft(value="61.00"), _draft(value="59.99", as_of=date(2026, 8, 6))]
        apply_range_bounds(drafts, result)
        assert QF_OUT_OF_RANGE in drafts[0].quality_flags
        assert QF_OUT_OF_RANGE not in drafts[1].quality_flags
        assert len(drafts) == 2  # kept

    def test_fx_bounds_are_wider(self) -> None:
        result = ParseResult()
        ok = _draft(series="GHS.FX.USDGHS.MID", value="11.7615", unit="rate")
        bad = _draft(
            series="GHS.FX.USDGHS.MID", value="0.01", unit="rate", as_of=date(2026, 8, 6)
        )
        apply_range_bounds([ok, bad], result)
        assert QF_OUT_OF_RANGE not in ok.quality_flags
        assert QF_OUT_OF_RANGE in bad.quality_flags

    def test_volumes_and_counts_have_no_rate_bounds(self) -> None:
        result = ParseResult()
        volume = _draft(series="GHS.GFIM.X.VOLUME", value="2920624351", unit="ghs")
        apply_range_bounds([volume], result)
        assert volume.quality_flags == []


class TestStaleness:
    def test_stale_monthly_source_is_flagged_on_newest_generation(self) -> None:
        # The GSS statsbank case: table ends 2024M07, captured 2026-08-09.
        result = ParseResult()
        newest = _draft(as_of=date(2024, 7, 1))
        older = _draft(as_of=date(2024, 6, 1))
        apply_staleness(
            [older, newest], result, cadence="monthly", as_of=date(2026, 8, 9)
        )
        assert QF_STALE_SOURCE in newest.quality_flags
        assert QF_STALE_SOURCE not in older.quality_flags
        assert newest.attributes["staleness_gap_days"] == (date(2026, 8, 9) - date(2024, 7, 1)).days
        assert any("stale" in w for w in result.warnings)

    def test_fresh_daily_source_is_not_flagged(self) -> None:
        result = ParseResult()
        draft = _draft(as_of=date(2026, 8, 7))
        apply_staleness([draft], result, cadence="daily", as_of=date(2026, 8, 9))
        assert draft.quality_flags == []
        assert result.warnings == []


class TestHelpers:
    def test_slugify(self) -> None:
        assert slugify("7 YR FXR BOND") == "7_YR_FXR_BOND"
        assert slugify("Average Time Deposits Rate: 3-Month ( %)") == (
            "AVERAGE_TIME_DEPOSITS_RATE_3_MONTH"
        )

    def test_quantize_fits_numeric_28_10(self) -> None:
        # GFIM publishes yields to 15dp; storage scale is 10.
        value = quantize_value(Decimal("9.987077471264369"))
        assert value == Decimal("9.9870774713")
        exponent = value.as_tuple().exponent
        assert isinstance(exponent, int)
        assert exponent >= -10

    def test_reconciliation_rules_pair_distinct_series(self) -> None:
        for rule in RECONCILIATION_RULES:
            assert rule.series_a != rule.series_b
            assert rule.tolerance > 0
