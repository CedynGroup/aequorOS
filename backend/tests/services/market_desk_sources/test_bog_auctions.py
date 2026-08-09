"""Auction-result parsing against the REAL captured tender pages and PDFs.

Spot-check values verified by hand against pdftotext-style extraction of
``gog_auction_result_tender_2019.pdf`` (Notice BG/FMD/2026/39) and
``bog_auction_result_tender_873_2026-08-05.pdf`` (Notice No. 873)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.market_desk.sources import bog_auctions
from app.services.market_desk.sources.core import ParseContext
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 9))


def _one(result, series_code: str):
    hits = [o for o in result.observations if o.series_code == series_code]
    assert len(hits) == 1, series_code
    return hits[0]


class TestGogAuctionPdf:
    def test_all_three_securities_with_exact_clearing_results(self) -> None:
        result = bog_auctions.parse_gog_auction_pdf(
            read_fixture("gog_auction_result_tender_2019.pdf"), context=CTX
        )
        assert result.errors == []
        assert result.warnings == []
        # Tender 2019 held 7TH AUGUST 2026; exactly 3 securities, 2 legs each.
        assert len(result.observations) == 6
        assert all(o.as_of_date == date(2026, 8, 7) for o in result.observations)

        d91 = _one(result, "GHS.AUCTION.GOG.91.DISCOUNT")
        assert d91.value == Decimal("5.5508")
        assert _one(result, "GHS.AUCTION.GOG.91.INTEREST").value == Decimal("5.6289")
        assert _one(result, "GHS.AUCTION.GOG.182.DISCOUNT").value == Decimal("7.2535")
        assert _one(result, "GHS.AUCTION.GOG.182.INTEREST").value == Decimal("7.5265")
        assert _one(result, "GHS.AUCTION.GOG.364.DISCOUNT").value == Decimal("11.4938")
        assert _one(result, "GHS.AUCTION.GOG.364.INTEREST").value == Decimal("12.9864")

        # GH¢-and-thousands amounts, per-ISIN.
        assert d91.attributes["tender"] == "2019"
        assert d91.attributes["isin"] == "GHGGOGI02089"
        assert d91.attributes["amount_tendered_m_ghs"] == "3701.66"
        assert d91.attributes["amount_accepted_m_ghs"] == "2362.26"
        assert d91.attributes["weekly_target_m_ghs"] == "6217.00"

    def test_inconsistent_range_spacing_both_bounds_parsed(self) -> None:
        # README quirk 4: '5.5000– 5.6795' (en-dash, missing space) and
        # '11.3868-12.0000' (bare hyphen) both live in this one PDF.
        result = bog_auctions.parse_gog_auction_pdf(
            read_fixture("gog_auction_result_tender_2019.pdf"), context=CTX
        )
        d91 = _one(result, "GHS.AUCTION.GOG.91.DISCOUNT")
        assert d91.attributes["bid_range"] == ["5.4254", "5.6976"]
        assert d91.attributes["allotted_interest_range"] == ["5.5000", "5.6795"]
        d364 = _one(result, "GHS.AUCTION.GOG.364.DISCOUNT")
        assert d364.attributes["bid_range"] == ["11.3868", "12.0000"]
        assert d364.attributes["allotted_discount_range"] == ["11.3868", "11.5044"]

    def test_prior_tender_summary_is_not_mistaken_for_results(self) -> None:
        # Section 2 summarizes tender 2018 (GH¢ 10,505.12M tendered); no
        # observation may carry those totals or a fourth security.
        result = bog_auctions.parse_gog_auction_pdf(
            read_fixture("gog_auction_result_tender_2019.pdf"), context=CTX
        )
        tenors = {o.series_code.split(".")[3] for o in result.observations}
        assert tenors == {"91", "182", "364"}
        for o in result.observations:
            assert o.attributes["amount_tendered_m_ghs"] != "10505.12"


class TestBogAuctionPdf:
    def test_exact_clearing_results_for_tender_873(self) -> None:
        result = bog_auctions.parse_bog_auction_pdf(
            read_fixture("bog_auction_result_tender_873_2026-08-05.pdf"), context=CTX
        )
        assert result.errors == []
        assert len(result.observations) == 2
        discount = _one(result, "GHS.AUCTION.BOG.14.DISCOUNT")
        interest = _one(result, "GHS.AUCTION.BOG.14.INTEREST")
        assert discount.value == Decimal("10.4557")
        assert interest.value == Decimal("10.4980")
        assert discount.as_of_date == date(2026, 8, 5)
        assert discount.attributes["tender"] == "873"
        assert discount.attributes["isin"] == "GHCBAGH01298"
        assert discount.attributes["bid_range"] == ["10.4000", "10.4578"]
        assert discount.attributes["total_sold_m_ghs"] == "8478.44"

    def test_tender_number_recurs_across_days_as_of_is_held_date(self) -> None:
        # README: BoG reuses tender numbers across days in one week — 873
        # ran 03 Aug AND 05 Aug 2026. The held date disambiguates.
        result = bog_auctions.parse_bog_auction_pdf(
            read_fixture("bog_auction_result_tender_873_2026-08-05.pdf"), context=CTX
        )
        assert all(o.as_of_date == date(2026, 8, 5) for o in result.observations)
        assert all(o.attributes["held_on"] == "2026-08-05" for o in result.observations)


class TestTenderPages:
    def test_bog_tender_page_metadata(self) -> None:
        page = bog_auctions.extract_tender_page(
            read_fixture("bog_tender_873_2026-08-05.html").decode(errors="replace")
        )
        assert page.tender_number == "873"
        assert page.held_on == date(2026, 8, 5)
        assert page.pdf_url is not None
        assert page.pdf_url.endswith("BOG-Auctresults-873-WED-5TH-AUGUST-26.pdf")

    def test_gog_tender_page_has_pdf_but_no_date_in_title(self) -> None:
        # GoG slugs are results-of-gog-tender-<N>/ where N is the tender
        # number (~2019), NOT a year, and the title carries no held date.
        page = bog_auctions.extract_tender_page(
            read_fixture("gog_tender_2019.html").decode(errors="replace")
        )
        assert page.tender_number == "2019"
        assert page.held_on is None
        assert page.pdf_url is not None
        assert page.pdf_url.endswith("Auctresult2019.pdf")

    def test_same_tender_number_different_days_yield_distinct_pdfs(self) -> None:
        page_03 = bog_auctions.extract_tender_page(
            read_fixture("bog_tender_873_2026-08-03.html").decode(errors="replace")
        )
        page_05 = bog_auctions.extract_tender_page(
            read_fixture("bog_tender_873_2026-08-05.html").decode(errors="replace")
        )
        assert page_03.tender_number == page_05.tender_number == "873"
        assert page_03.held_on == date(2026, 8, 3)
        assert page_05.held_on == date(2026, 8, 5)
        assert page_03.pdf_url != page_05.pdf_url

    def test_non_tender_page_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="tender-results"):
            bog_auctions.extract_tender_page("<html><title>Nope</title></html>")


class TestRangeTokenizer:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5.4254 – 5.6976", ("5.4254", "5.6976")),
            ("5.5000– 5.6795", ("5.5000", "5.6795")),
            ("11.3868-12.0000", ("11.3868", "12.0000")),
            ("7.2200 - 7.3000", ("7.2200", "7.3000")),
        ],
    )
    def test_tolerant_of_spacing_and_hyphen_variants(
        self, text: str, expected: tuple[str, str]
    ) -> None:
        match = bog_auctions.RATE_RANGE_RE.search(text)
        assert match is not None
        assert (match.group(1), match.group(2)) == expected
