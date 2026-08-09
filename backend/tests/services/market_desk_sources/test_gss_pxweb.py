"""GSS statsbank PxWeb parser against the REAL interest.px data response.

Hand-verified spot values from the fixture (2024M07, the newest month):
Average lending rate 30.7, Ghana reference rate 29.4, Interbank weighted
average 28.8. The fixture holds 3858 cells of which 2235 are non-missing —
exactly the manifest's count."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.services.market_desk.sources import gss_pxweb
from app.services.market_desk.sources.core import ParseContext
from tests.services.market_desk_sources.conftest import read_fixture

CTX = ParseContext(as_of_date=date(2026, 8, 9))


def _result():
    return gss_pxweb.parse_interest_px(
        read_fixture("gss_pxweb_interest_px_data_response.json"), context=CTX
    )


def _one(result, series_code: str, as_of: date):
    hits = [
        o for o in result.observations if o.series_code == series_code and o.as_of_date == as_of
    ]
    assert len(hits) == 1
    return hits[0]


class TestInterestPx:
    def test_exact_values_and_month_key_mapping(self) -> None:
        result = _result()
        assert result.errors == []
        # YYYYMmm -> first-of-month ISO dates.
        assert _one(result, "GHS.GRR", date(2024, 7, 1)).value == Decimal("29.4")
        assert _one(result, "GHS.IWAL", date(2024, 7, 1)).value == Decimal("28.8")
        assert _one(result, "GHS.LENDING.AVG", date(2024, 7, 1)).value == Decimal("30.7")

    def test_exactly_the_manifests_2235_non_missing_cells(self) -> None:
        result = _result()
        assert len(result.observations) == 2235
        assert any("1623 missing" in w for w in result.warnings)

    def test_grr_starts_2018_04_it_did_not_exist_before(self) -> None:
        grr = [o for o in _result().observations if o.series_code == "GHS.GRR"]
        assert len(grr) == 76
        assert min(o.as_of_date for o in grr) == date(2018, 4, 1)

    def test_corroboration_series_use_gss_codes_not_bog_owned_codes(self) -> None:
        # MPR / 91-day / savings belong to BoG tables; the GSS copies get
        # GHS.GSS.* so reconciliation compares instead of superseding.
        codes = {o.series_code for o in _result().observations}
        assert "GHS.GSS.MPR" in codes
        assert "GHS.GSS.TBILL91" in codes
        assert "GHS.GSS.SAVINGS" in codes
        assert "GHS.MPR" not in codes

    def test_utf8_bom_prefixed_response_parses(self) -> None:
        # README: the live response body starts with a UTF-8 BOM.
        raw = b"\xef\xbb\xbf" + read_fixture("gss_pxweb_interest_px_data_response.json")
        result = gss_pxweb.parse_interest_px(raw, context=CTX)
        assert result.errors == []
        assert len(result.observations) == 2235

    def test_missing_cells_are_skipped_not_zeroed(self) -> None:
        raw = json.dumps(
            {
                "columns": [],
                "data": [
                    {"key": ["1971M01", "Savings deposits rate"], "values": [".."]},
                    {"key": ["1971M01", "Monetary policy rate"], "values": ["8.0"]},
                ],
            }
        ).encode()
        result = gss_pxweb.parse_interest_px(raw, context=CTX)
        assert len(result.observations) == 1
        assert result.observations[0].value == Decimal("8.0")
        assert any("missing" in w for w in result.warnings)
