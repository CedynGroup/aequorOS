"""APR-of-banks PDF against the REAL May-2026 notice (11 pages, 9 tables).

Hand-verified spot values (read off the fixture's reconstructed rows):
Table 1 (Household 1y): Absa 18.32, GCB 26.74, Stanbic 11.62;
Table 3 (Household 5y, a per-glyph-positioned page): OmniBSIC 5.03 with a
NEGATIVE spread (-5.03), UBA 32.97, Zenith 16.27 — corroborated by the
notice's own KEY DEVELOPMENTS page (lowest reported APR 5.03 = OmniBSIC,
highest 3-yr household 39.27 = Universal Merchant)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import bog_apr_pdf
from app.services.market_desk.sources.core import ParseContext
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 9))

EXPECTED_CATEGORIES = {
    "HOUSEHOLD_1Y",
    "HOUSEHOLD_3Y",
    "HOUSEHOLD_5Y",
    "SME_1Y",
    "SME_3Y",
    "SME_5Y",
    "CORPORATE_1Y",
    "CORPORATE_3Y",
    "CORPORATE_5Y",
}


def _result():
    return bog_apr_pdf.parse_apr_pdf(read_fixture("bog_apr_of_banks_2026-05.pdf"), context=CTX)


def _one(result, series_code: str):
    hits = [o for o in result.observations if o.series_code == series_code]
    assert len(hits) == 1, series_code
    return hits[0]


class TestAprPdf:
    def test_all_nine_categories_with_at_least_20_banks_overall(self) -> None:
        result = _result()
        assert result.errors == []
        categories = {o.series_code.rsplit(".", 1)[1] for o in result.observations}
        assert categories == EXPECTED_CATEGORIES
        banks = {o.series_code.split(".")[2] for o in result.observations}
        assert len(banks) >= 20  # 23 licensed banks in the notice
        # Every published APR is a plausible lending rate.
        assert all(Decimal("0") < o.value < Decimal("60") for o in result.observations)
        assert all(o.unit == "pct" for o in result.observations)
        assert all(o.as_of_date == date(2026, 5, 1) for o in result.observations)

    def test_exact_spot_values_household_1y(self) -> None:
        result = _result()
        absa = _one(result, "GHS.APR.ABSA_BANK_GHANA.HOUSEHOLD_1Y")
        assert absa.value == Decimal("18.32")
        assert absa.attributes["grr"] == "10.03"
        assert absa.attributes["spread"] == "6.90"
        assert absa.attributes["avg_lending_rate"] == "16.93"
        assert _one(result, "GHS.APR.GCB_BANK.HOUSEHOLD_1Y").value == Decimal("26.74")
        assert _one(result, "GHS.APR.STANBIC_BANK_GHANA.HOUSEHOLD_1Y").value == Decimal("11.62")

    def test_per_glyph_positioned_page_parses_household_5y(self) -> None:
        # Tables 3/5/7 render every glyph individually; pypdf and layout-mode
        # extraction shatter them — char-geometry reconstruction must not.
        result = _result()
        omni = _one(result, "GHS.APR.OMNIBSIC_BANK_GHANA.HOUSEHOLD_5Y")
        assert omni.value == Decimal("5.03")
        assert omni.attributes["spread"] == "-5.03"  # negative spread, real
        assert _one(result, "GHS.APR.UNITED_BANK_FOR_AFRICA_GHANA.HOUSEHOLD_5Y").value == (
            Decimal("32.97")
        )
        assert _one(result, "GHS.APR.ZENITH_BANK_GHANA.HOUSEHOLD_5Y").value == Decimal("16.27")

    def test_wrapped_bank_names_are_stitched_and_slugs_stable(self) -> None:
        # 'Agricultural Development Bank / Limited' wraps around its data row
        # on the clean pages; the inline pages drop the suffix — one slug.
        result = _result()
        adb = _one(result, "GHS.APR.AGRICULTURAL_DEVELOPMENT_BANK.HOUSEHOLD_1Y")
        assert adb.value == Decimal("28.13")
        # No suffix-forked sibling series may exist.
        assert not any(
            o.series_code.startswith("GHS.APR.AGRICULTURAL_DEVELOPMENT_BANK_LIMITED.")
            for o in result.observations
        )

    def test_nl_no_loan_rows_produce_no_observation_but_are_counted(self) -> None:
        # Table 1: Guaranty Trust and Standard Chartered reported NL.
        result = _result()
        h1y = [o for o in result.observations if o.series_code.endswith(".HOUSEHOLD_1Y")]
        assert len(h1y) == 21  # 23 banks minus 2 NL
        assert not any(
            o.series_code == "GHS.APR.GUARANTY_TRUST_BANK_GHANA.HOUSEHOLD_1Y" for o in h1y
        )
        assert any("HOUSEHOLD_1Y: 2 bank(s) reported NL" in w for w in result.warnings)

    def test_report_month_comes_from_the_notice_footnotes(self) -> None:
        result = _result()
        assert all(o.attributes["report_month"] == "2026-05" for o in result.observations)
