"""BoG wpDataTables parsers against the REAL captured admin-ajax pages,
plus one test per documented dirty-data quirk the first-page fixtures do
not themselves contain (empty dates, duplicates, conflicts)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import bog_wdt
from app.services.market_desk.sources.core import (
    QF_AS_OF_FROM_CAPTURE,
    QF_SOURCE_CONFLICT,
    ObservationDraft,
    ParseContext,
    dedupe_and_resolve_conflicts,
)
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 9))


def _by_code(result, series_code: str, as_of: date) -> ObservationDraft:
    hits = [
        o for o in result.observations if o.series_code == series_code and o.as_of_date == as_of
    ]
    assert len(hits) == 1, f"{series_code} {as_of}: {len(hits)} hits"
    return hits[0]


def _page(rows: list[list[str]]) -> bytes:
    return json.dumps(
        {"draw": 1, "recordsTotal": 38145, "recordsFiltered": len(rows), "data": rows}
    ).encode()


class TestTable2TbillRates:
    def test_exact_values_from_captured_page(self) -> None:
        # Fixture row: ['03 Aug 2026', '2018', '91 DAY BILL', '5.6800', '5.7618']
        result = bog_wdt.parse_table2_tbill_rates(
            read_fixture("bog_wdt_table2_tbill_rates_page.json"), context=CTX
        )
        assert result.errors == []
        as_of = date(2026, 8, 3)
        discount = _by_code(result, "GHS.TBILL.91.DISCOUNT", as_of)
        interest = _by_code(result, "GHS.TBILL.91.INTEREST", as_of)
        assert discount.value == Decimal("5.6800")
        assert interest.value == Decimal("5.7618")
        assert discount.attributes["tender"] == "2018"
        # INTEREST dual-writes YIELD for package optional series / cross-check.
        assert _by_code(result, "GHS.TBILL.91.YIELD", as_of).value == Decimal("5.7618")
        assert _by_code(result, "GHS.TBILL.182.DISCOUNT", as_of).value == Decimal("7.3597")
        assert _by_code(result, "GHS.TBILL.182.INTEREST", as_of).value == Decimal("7.6409")
        assert _by_code(result, "GHS.TBILL.364.DISCOUNT", as_of).value == Decimal("11.4904")
        assert _by_code(result, "GHS.TBILL.364.INTEREST", as_of).value == Decimal("12.9821")
        # 25 rows x (discount + interest + yield alias), all bills on this page.
        assert len(result.observations) == 75
        assert all(o.unit == "pct" for o in result.observations)

    def test_notes_and_bonds_get_gog_series_codes(self) -> None:
        # The full view includes notes/bonds (series CSV: '7 YR FXR BOND').
        raw = _page([["26 Aug 2013", "1342", "7 YR FXR BOND", "17.5000", "17.5000"]])
        result = bog_wdt.parse_table2_tbill_rates(raw, context=CTX)
        codes = {o.series_code for o in result.observations}
        assert codes == {"GHS.GOG.7_YR_FXR_BOND.DISCOUNT", "GHS.GOG.7_YR_FXR_BOND.INTEREST"}

    def test_exact_duplicate_rows_are_dropped_and_counted(self) -> None:
        # README: table 2 has 4 exact server-side duplicate rows.
        row = ["03 Aug 2026", "2018", "91 DAY BILL", "5.6800", "5.7618"]
        result = bog_wdt.parse_table2_tbill_rates(_page([row, row]), context=CTX)
        result.observations = dedupe_and_resolve_conflicts(result.observations, result)
        # DISCOUNT + INTEREST + YIELD alias (dual-written from INTEREST)
        assert len(result.observations) == 3
        assert any("duplicate" in w for w in result.warnings)


class TestTable3BogBillRates:
    def test_exact_values_from_captured_page(self) -> None:
        result = bog_wdt.parse_table3_bog_bill_rates(
            read_fixture("bog_wdt_table3_bog_bill_rates_page.json"), context=CTX
        )
        as_of = date(2026, 3, 16)
        assert _by_code(result, "GHS.BOGBILL.14.DISCOUNT", as_of).value == Decimal("11.9395")
        assert _by_code(result, "GHS.BOGBILL.14.INTEREST", as_of).value == Decimal("11.9946")
        assert _by_code(result, "GHS.BOGBILL.14.DISCOUNT", as_of).attributes["tender"] == "853"


class TestTable69InterbankDaily:
    def test_exact_values_and_thousands_separated_ids(self) -> None:
        result = bog_wdt.parse_table69_interbank_daily(
            read_fixture("bog_wdt_table69_interbank_daily_page.json"), context=CTX
        )
        newest = _by_code(result, "GHS.INTERBANK.ON", date(2026, 8, 7))
        assert newest.value == Decimal("10.23")
        # README: ID columns carry thousands separators ('55,496').
        assert newest.attributes["daily_interest_rate_id"] == "55496"
        assert len(result.observations) == 25

    def test_same_date_conflict_keeps_last_and_flags(self) -> None:
        # README: 2020-02-25 appears twice with conflicting 16.14 vs 16.12.
        raw = _page(
            [["27,441", "25 Feb 2020", "16.14"], ["27,442", "25 Feb 2020", "16.12"]]
        )
        result = bog_wdt.parse_table69_interbank_daily(raw, context=CTX)
        result.observations = dedupe_and_resolve_conflicts(result.observations, result)
        assert len(result.observations) == 1
        kept = result.observations[0]
        assert kept.value == Decimal("16.12")
        assert QF_SOURCE_CONFLICT in kept.quality_flags
        assert kept.attributes["conflicting_values"] == ["16.14", "16.12"]


class TestTable70InterbankWeekly:
    def test_exact_values_week_ending(self) -> None:
        result = bog_wdt.parse_table70_interbank_weekly(
            read_fixture("bog_wdt_table70_interbank_weekly_avg_page.json"), context=CTX
        )
        newest = _by_code(result, "GHS.INTERBANK.WAVG", date(2026, 8, 7))
        assert newest.value == Decimal("10.23")
        assert newest.attributes["period"] == "week_ending"


class TestTable62Mpr:
    def test_exact_values_from_captured_page(self) -> None:
        result = bog_wdt.parse_table62_mpr(
            read_fixture("bog_wdt_table62_mpc_rate_page.json"), context=CTX
        )
        assert _by_code(result, "GHS.MPR", date(2026, 7, 22)).value == Decimal("15.00")
        assert _by_code(result, "GHS.MPR", date(2026, 1, 28)).value == Decimal("16.50")
        assert len(result.observations) == 25

    def test_empty_date_rows_are_skipped_with_warning(self) -> None:
        # README: table 62 has two rows with '' Effective Date (IDs 13,509
        # and 13,430 per the manifest).
        raw = _page([["13,509", "", "12.50"], ["55,261", "22 Jul 2026", "15.00"]])
        result = bog_wdt.parse_table62_mpr(raw, context=CTX)
        assert len(result.observations) == 1
        assert any("empty" in w for w in result.warnings)


class TestFxTables:
    def test_table31_latest_day_all_pairs(self) -> None:
        result = bog_wdt.parse_fx_pairs(
            read_fixture("bog_wdt_table31_daily_fx_latest_day_page.json"), context=CTX
        )
        as_of = date(2026, 8, 7)
        assert _by_code(result, "GHS.FX.USDGHS.BUY", as_of).value == Decimal("11.7556")
        assert _by_code(result, "GHS.FX.USDGHS.SELL", as_of).value == Decimal("11.7674")
        assert _by_code(result, "GHS.FX.USDGHS.MID", as_of).value == Decimal("11.7615")
        # Dual-write for rates package required series.
        assert _by_code(result, "GHS.USDGHS.MID", as_of).value == Decimal("11.7615")
        assert _by_code(result, "GHS.FX.GBPGHS.MID", as_of).value == Decimal("15.8775")
        # 19 pairs x 3 legs + 1 USDGHS.MID alias, all unit 'rate'.
        assert len(result.observations) == 58
        assert all(o.unit == "rate" for o in result.observations)

    def test_table40_usdghs_column_search_page(self) -> None:
        result = bog_wdt.parse_fx_pairs(
            read_fixture("bog_wdt_table40_historical_fx_usdghs_colsearch_page.json"),
            context=CTX,
        )
        assert _by_code(result, "GHS.FX.USDGHS.MID", date(2026, 8, 6)).value == Decimal(
            "11.7586"
        )
        assert _by_code(result, "GHS.FX.USDGHS.MID", date(2026, 8, 5)).value == Decimal(
            "11.7400"
        )
        assert _by_code(result, "GHS.USDGHS.MID", date(2026, 8, 6)).value == Decimal(
            "11.7586"
        )
        # FX legs use GHS.FX.USDGHS.*; alias is GHS.USDGHS.MID (no pair segment).
        codes = {o.series_code for o in result.observations}
        assert "GHS.USDGHS.MID" in codes
        assert all(
            c.startswith("GHS.FX.USDGHS.") or c == "GHS.USDGHS.MID" for c in codes
        )


    def test_table31_and_table40_agree_exactly_on_the_shared_day(self) -> None:
        """The nightly FX read may use table 31 because table 31 carries the
        SAME six columns table 40 does.

        Both captured pages (2026-08-09 harvest) carry 07 Aug 2026, and the
        one parser reads both: for that day the two tables yield an identical
        observation set — same codes, dates, values, units, attributes and
        flags — including the Leone row, which table 40 prints with thousands
        separators ("2,006.8666") and table 31 without. So a daily job has no
        reason to page 145 pages of archive to learn one day's rates; table
        40 is the BACKFILL path, and this is why swapping to table 31 for the
        nightly read cannot move a number.
        """
        shared_day = date(2026, 8, 7)

        def fingerprint(fixture: str) -> set[tuple[object, ...]]:
            result = bog_wdt.parse_fx_pairs(read_fixture(fixture), context=CTX)
            return {
                (
                    o.series_code,
                    o.as_of_date,
                    str(o.value),
                    o.unit,
                    tuple(sorted(o.attributes.items())),
                    tuple(o.quality_flags),
                )
                for o in result.observations
                if o.as_of_date == shared_day
            }

        latest_day = fingerprint("bog_wdt_table31_daily_fx_latest_day_page.json")
        archive = fingerprint("bog_wdt_table40_historical_fx_page.json")
        assert latest_day == archive
        assert len(latest_day) == 58  # 19 pairs x 3 legs + the USDGHS.MID alias


class TestTable32ReferenceBanner:
    def test_banner_parsed_with_regex_and_capture_as_of(self) -> None:
        # README: 'Day's Weighted Median Rate:   11.7615' — a text banner,
        # not a table; it carries no date of its own.
        result = bog_wdt.parse_table32_fx_reference(
            read_fixture("bog_wdt_table32_fx_weighted_median_page.json"), context=CTX
        )
        assert len(result.observations) == 1
        ref = result.observations[0]
        assert ref.series_code == "GHS.FX.USDGHS.REF"
        assert ref.value == Decimal("11.7615")
        assert ref.as_of_date == CTX.as_of_date
        assert QF_AS_OF_FROM_CAPTURE in ref.quality_flags

    def test_missing_banner_is_an_error(self) -> None:
        result = bog_wdt.parse_table32_fx_reference(_page([["no banner here"]]), context=CTX)
        assert result.observations == []
        assert result.errors


class TestTable21MonthlyMatrix:
    def test_zero_is_missing_never_a_value(self) -> None:
        # README: table 21 uses 0.00 as its missing-month placeholder — zero
        # is never a real value for these rates. 2023 rows carry data only
        # through April.
        result = bog_wdt.parse_table21_monthly_matrix(
            read_fixture("bog_wdt_table21_econ_interest_rates_page.json"), context=CTX
        )
        lending = [
            o
            for o in result.observations
            if o.series_code == "GHS.ECONDATA.LENDING_AVG" and o.as_of_date.year == 2023
        ]
        assert {o.as_of_date.month for o in lending} == {1, 2, 3, 4}
        by_month = {o.as_of_date.month: o.value for o in lending}
        assert by_month[1] == Decimal("35.85")
        assert by_month[4] == Decimal("31.66")
        assert any("0.00-as-missing" in w for w in result.warnings)

    def test_grr_dual_written_to_canonical_series(self) -> None:
        """BoG monthly matrix dual-writes GHS.GRR so the rates package is not
        stuck on multi-year-stale GSS prints when table 21 has fresher months."""
        result = bog_wdt.parse_table21_monthly_matrix(
            read_fixture("bog_wdt_table21_econ_interest_rates_page.json"), context=CTX
        )
        grr = [o for o in result.observations if o.series_code == "GHS.GRR"]
        econ = [o for o in result.observations if o.series_code == "GHS.ECONDATA.GRR"]
        assert grr
        assert len(grr) == len(econ)
        latest = max(grr, key=lambda o: o.as_of_date)
        assert latest.as_of_date == date(2023, 4, 1)
        assert latest.value == Decimal("25.76")

    def test_junk_year_zero_group_is_skipped(self) -> None:
        raw = _page([["0", "Ghana Reference Rate (%)"] + ["1.00"] * 12])
        result = bog_wdt.parse_table21_monthly_matrix(raw, context=CTX)
        assert result.observations == []
        assert any("junk year" in w for w in result.warnings)
