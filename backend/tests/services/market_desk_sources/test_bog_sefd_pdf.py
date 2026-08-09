"""SEFD (July 2026 edition) against the REAL captured PDF.

Hand-verified spot values from section 3a/3b of the fixture:
MPR 2026:06 = 14.00, GRR 2026:06 = 10.02, 91-day (interest equivalent)
2026:06 = 5.27, interbank weighted average 2025:06 = 27.02; post-DDEP
15-Year Bond 2026:06 = 14.11. Watermark-injection cases: the 6-Year Bond
2025:10 cell prints as '15L.56' and the 11-Year 2025:08 cell as 'B16.87'."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import bog_sefd_pdf
from app.services.market_desk.sources.core import QF_WATERMARK_CLEANED, ParseContext
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 9))


def _result():
    return bog_sefd_pdf.parse_sefd_pdf(
        read_fixture("bog_summary_econ_fin_data_2026-07.pdf"), context=CTX
    )


def _one(result, series_code: str, as_of: date):
    hits = [
        o for o in result.observations if o.series_code == series_code and o.as_of_date == as_of
    ]
    assert len(hits) == 1, f"{series_code} {as_of}"
    return hits[0]


class TestSection3a:
    def test_exact_interest_rate_values(self) -> None:
        result = _result()
        assert result.errors == []
        assert _one(result, "GHS.SEFD.MPR", date(2026, 6, 1)).value == Decimal("14.00")
        assert _one(result, "GHS.SEFD.GRR", date(2026, 6, 1)).value == Decimal("10.02")
        assert _one(result, "GHS.SEFD.TBILL_91", date(2026, 6, 1)).value == Decimal("5.27")
        assert _one(result, "GHS.SEFD.INTERBANK_WAVG", date(2025, 6, 1)).value == Decimal(
            "27.02"
        )
        assert _one(result, "GHS.SEFD.LENDING_AVG", date(2026, 6, 1)).value == Decimal("15.64")

    def test_all_eleven_3a_rows_cover_thirteen_months(self) -> None:
        result = _result()
        rows_3a = {
            o.series_code for o in result.observations if o.attributes.get("section") == "3a"
        }
        assert len(rows_3a) == 11
        months = {
            o.as_of_date for o in result.observations if o.series_code == "GHS.SEFD.MPR"
        }
        assert len(months) == 13  # 2025:06 .. 2026:06
        assert min(months) == date(2025, 6, 1)
        assert max(months) == date(2026, 6, 1)


class TestSection3b:
    def test_post_ddep_bond_curve_rows(self) -> None:
        result = _result()
        bond_codes = {
            o.series_code
            for o in result.observations
            if o.series_code.startswith("GHS.SEFD.BOND.")
        }
        assert bond_codes == {f"GHS.SEFD.BOND.{n}Y" for n in range(4, 16)}
        assert _one(result, "GHS.SEFD.BOND.15Y", date(2026, 6, 1)).value == Decimal("14.11")
        assert _one(result, "GHS.SEFD.BOND.4Y", date(2025, 6, 1)).value == Decimal("19.36")

    def test_watermark_letter_inside_number_is_cleaned(self) -> None:
        # 'B16.87' (11-Year, 2025:08): the vertical PUBLIC watermark drops a
        # letter INTO the token — cleaned, flagged, value intact.
        result = _result()
        eleven = _one(result, "GHS.SEFD.BOND.11Y", date(2025, 8, 1))
        assert eleven.value == Decimal("16.87")
        assert QF_WATERMARK_CLEANED in eleven.quality_flags
        # '15L.56' (6-Year, 2025:10): letter lands as its own glyph run and
        # is dropped by tokenization — value must still be exact.
        six = _one(result, "GHS.SEFD.BOND.6Y", date(2025, 10, 1))
        assert six.value == Decimal("15.56")


class TestHonestCoverage:
    def test_unsupported_sections_are_reported_not_half_parsed(self) -> None:
        result = _result()
        unsupported = [w for w in result.warnings if "unsupported" in w]
        assert len(unsupported) == len(bog_sefd_pdf.UNSUPPORTED_SECTIONS)
        # Nothing outside the two interest-rate sections may be emitted.
        assert all(
            o.series_code.startswith("GHS.SEFD.") for o in result.observations
        )
        sections = {o.attributes["section"] for o in result.observations}
        assert sections == {"3a", "3b"}
