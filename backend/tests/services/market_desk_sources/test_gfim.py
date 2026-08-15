"""GFIM parsers against the REAL daily trading workbooks and monthly status
report text extract.

Hand-verified spot values from TRADING-REPORT-FOR-GFIM-07082026.xlsx:
NEW GOG NOTES AND BONDS row 1: GHGGOGI01503 closing yield 12.37, closing
price 100.552835, no volume (indicative mark); TREASURY BILLS row 1:
GHGGOGI01669 closing yield 9.987077471264369, volume 156742, 8 trades.
June 2026 status report section B: volume 44,894,161,584, value (GHS)
41,105,780,892.85, 18,325 trades."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import gfim
from app.services.market_desk.sources.core import QF_NO_TRADES, ParseContext
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 7))


def _one(result, series_code: str):
    hits = [o for o in result.observations if o.series_code == series_code]
    assert len(hits) == 1, series_code
    return hits[0]


class TestDailyWorkbook:
    def test_exact_values_per_sheet(self) -> None:
        result = gfim.parse_gfim_daily_xlsx(
            read_fixture("gfim_daily_trading_report_2026-08-07.xlsx"), context=CTX
        )
        assert result.errors == []
        # NEW GOG NOTES AND BONDS: an indicative mark with no trades.
        bond_yield = _one(result, "GHS.GFIM.GHGGOGI01503.YIELD")
        assert bond_yield.value == Decimal("12.37")
        assert bond_yield.unit == "pct"
        assert QF_NO_TRADES in bond_yield.quality_flags
        assert _one(result, "GHS.GFIM.GHGGOGI01503.PRICE").value == Decimal("100.552835")
        # DDEP BONDS.
        ddep = _one(result, "GHS.GFIM.GHGGOG069873.YIELD")
        assert ddep.value == Decimal("11.71")
        assert ddep.attributes["security_type"] == "ddep_bond"
        assert ddep.attributes["maturity_date"] == "2027-08-17"
        # TREASURY BILLS: an actually-traded line.
        bill_yield = _one(result, "GHS.GFIM.GHGGOGI01669.YIELD")
        assert bill_yield.value == Decimal("9.987077471264369")
        assert bill_yield.quality_flags == []
        assert _one(result, "GHS.GFIM.GHGGOGI01669.VOLUME").value == Decimal("156742")
        assert _one(result, "GHS.GFIM.GHGGOGI01669.TRADES").value == Decimal("8")

    def test_sheet_date_wins_over_capture_as_of(self) -> None:
        # Sheet header: 'Date: Friday, 07 August, 2026'.
        stale_ctx = ParseContext(as_of_date=date(2026, 8, 9))
        result = gfim.parse_gfim_daily_xlsx(
            read_fixture("gfim_daily_trading_report_2026-08-07.xlsx"), context=stale_ctx
        )
        assert {o.as_of_date for o in result.observations} == {date(2026, 8, 7)}

    def test_trailing_space_sheet_names_and_all_security_types(self) -> None:
        # README: sheet names carry trailing spaces ('OLD GOG NOTES AND
        # BONDS ', 'CORPORATE  ') — matching must strip, never miss.
        result = gfim.parse_gfim_daily_xlsx(
            read_fixture("gfim_daily_trading_report_2026-08-07.xlsx"), context=CTX
        )
        types = {o.attributes["security_type"] for o in result.observations}
        assert types == {
            "new_gog_bond",
            "ddep_bond",
            "old_gog_bond",
            "corporate",
            "bill",
            "sell_buy_back",
        }
        assert not any("unrecognized sheet" in w for w in result.warnings)

    def test_sell_buy_backs_never_collide_with_outright_series(self) -> None:
        # The same ISIN trades outright AND as a repo on the same day; the
        # SBB stem keeps the series distinct (one current row per code+date).
        result = gfim.parse_gfim_daily_xlsx(
            read_fixture("gfim_daily_trading_report_2026-08-07.xlsx"), context=CTX
        )
        sbb = _one(result, "GHS.GFIM.SBB.GHGGOGI01503.YIELD")
        assert sbb.value == Decimal("12.2")
        assert sbb.attributes["security_type"] == "sell_buy_back"
        outright = _one(result, "GHS.GFIM.GHGGOGI01503.YIELD")
        assert outright.series_code != sbb.series_code

    def test_older_workbooks_parse_too(self) -> None:
        for name, day in (
            ("gfim_daily_trading_report_2026-08-06.xlsx", date(2026, 8, 6)),
            ("gfim_daily_trading_report_2026-08-05.xlsx", date(2026, 8, 5)),
        ):
            result = gfim.parse_gfim_daily_xlsx(
                read_fixture(name), context=ParseContext(as_of_date=day)
            )
            assert result.errors == []
            assert result.observations
            assert {o.as_of_date for o in result.observations} == {day}


class TestMonthlyStatusReport:
    def test_june_2026_summary_series(self) -> None:
        result = gfim.parse_gfim_status_report(
            read_fixture("gfim_status_report_2026-06.txt"), context=CTX
        )
        assert result.errors == []
        volume = _one(result, "GHS.GFIM.MONTHLY.VOLUME")
        assert volume.value == Decimal("44894161584")
        assert volume.as_of_date == date(2026, 6, 1)
        assert volume.attributes["period"] == "2026-06"
        assert _one(result, "GHS.GFIM.MONTHLY.VALUE_GHS").value == Decimal("41105780892.85")
        assert _one(result, "GHS.GFIM.MONTHLY.TRADES").value == Decimal("18325")

    def test_may_2026_report_parses_with_its_own_month(self) -> None:
        result = gfim.parse_gfim_status_report(
            read_fixture("gfim_status_report_2026-05.txt"), context=CTX
        )
        assert result.errors == []
        assert {o.as_of_date for o in result.observations} == {date(2026, 5, 1)}

    def test_unsupported_sections_are_reported(self) -> None:
        result = gfim.parse_gfim_status_report(
            read_fixture("gfim_status_report_2026-06.txt"), context=CTX
        )
        assert any("unsupported" in w for w in result.warnings)

    def test_month_resolves_by_majority_vote_over_comparison_years(self) -> None:
        # The reconstructed banner collapses the drop-cap ('JULY2026'), and a
        # lone prior-year comparison ('AS AT JULY 2025') sits beside the report
        # month — the majority vote must land on the report month regardless.
        text = (
            "JULY2026\n"
            "AS AT JULY 2025 AS AT JULY 2026\n"
            "OTHER MARKET STATISTICS FOR JULY 2026 Vs 2025\n"
            "L.    SECURITIES TRADED FOR JULY 2026\n"
            "B.  SUMMARY OF TRADE STATISTICS\n"
            "Volume Traded 1,000\n"
            "Value Traded (GHS) 2,000\n"
            "No. of Trades 5\n"
        )
        result = gfim.parse_gfim_status_report(text.encode(), context=CTX)
        assert result.errors == []
        assert {o.as_of_date for o in result.observations} == {date(2026, 7, 1)}

    def test_month_falls_back_to_source_filename(self) -> None:
        # A layout change that shatters the in-document banner still resolves
        # from the FileBird upload name carried on the capture.
        text = (
            "B.  SUMMARY OF TRADE STATISTICS\n"
            "Volume Traded 1,000\n"
            "Value Traded (GHS) 2,000\n"
            "No. of Trades 5\n"
        )
        ctx = ParseContext(
            as_of_date=date(2026, 8, 14),
            source_url=(
                "https://gfim.com.gh/wp-content/uploads/2026/08/"
                "GFIM-Status-Report-July-2026.pdf"
            ),
        )
        result = gfim.parse_gfim_status_report(text.encode(), context=ctx)
        assert result.errors == []
        assert {o.as_of_date for o in result.observations} == {date(2026, 7, 1)}

    def test_no_month_anywhere_is_a_clean_error(self) -> None:
        result = gfim.parse_gfim_status_report(b"no month, no filename", context=CTX)
        assert result.observations == []
        assert len(result.errors) == 1
        assert "report month not found" in result.errors[0]
