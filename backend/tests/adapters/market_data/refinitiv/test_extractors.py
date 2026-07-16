"""Extractors against recorded RDP fixtures: parsing + error classification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.refinitiv.adapter import CATALOG_PATH
from app.adapters.market_data.refinitiv.extractors import (
    extract_curve,
    extract_fx,
    extract_ratings,
)
from app.adapters.market_data.refinitiv.transport import (
    FixtureTransport,
    UnconfiguredTransport,
)
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import load_catalog, requests_for
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER
from tests.adapters.market_data.refinitiv.conftest import FIXTURES_DIR

TOKEN = "sim-rdp-token"

_CATALOG = load_catalog(CATALOG_PATH)


def _specs(scope: DataScope) -> list[dict]:
    return requests_for(_CATALOG, scope)


def test_curve_extractor_parses_all_seven_tenors() -> None:
    transport = FixtureTransport(FIXTURES_DIR)
    raw, observations = extract_curve(
        transport, TOKEN, DataScope.YIELD_CURVE_GHS, _specs(DataScope.YIELD_CURVE_GHS)
    )
    assert len(observations) == 7
    assert [o.tenor_months for o in observations] == [1, 3, 6, 12, 24, 60, 120]
    assert all(o.field == "TR.MidYield" for o in observations)
    by_ric = {o.ric: o.value_percent for o in observations}
    assert by_ric["GH3M="] == Decimal("15.8")
    assert by_ric["GH10Y="] == Decimal("23.6")
    # Values stay in vendor units (percent) at the extractor boundary.
    assert all(Decimal("15") <= o.value_percent <= Decimal("30") for o in observations)
    # The raw payload is preserved verbatim for raw-tier audit storage.
    assert raw["vendor_internal"]["debug"] == VENDOR_INTERNAL_MARKER


def test_fx_extractor_parses_the_spot_price() -> None:
    transport = FixtureTransport(FIXTURES_DIR)
    raw, observations = extract_fx(
        transport, TOKEN, DataScope.FX_SPOT_USD_GHS, _specs(DataScope.FX_SPOT_USD_GHS)
    )
    assert len(observations) == 1
    assert observations[0].ric == "USDGHS=R"
    assert observations[0].value == Decimal("12.85")
    assert "vendor_internal" in raw


def test_ratings_extractor_parses_agency_watch_and_date() -> None:
    transport = FixtureTransport(FIXTURES_DIR)
    scope = DataScope.CREDIT_RATING_GHANA_SOVEREIGN
    _, observations = extract_ratings(transport, TOKEN, scope, _specs(scope))
    assert len(observations) == 3
    by_field = {o.field: o for o in observations}
    moodys = by_field["TR.MoodysIssuerRating"]
    assert (moodys.rating, moodys.watch_status) == ("Caa1", "stable")
    assert moodys.rating_date == date(2026, 3, 27)
    sp = by_field["TR.SPIssuerRating"]
    assert (sp.rating, sp.watch_status) == ("CCC+", "positive")
    fitch = by_field["TR.FitchIssuerRating"]
    assert (fitch.rating, fitch.watch_status) == ("CCC", "stable")


def test_error_fixture_classifies_as_unknown_instrument_without_leaking() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={DataScope.YIELD_CURVE_GHS.value: "unknown_instrument.json"}
    )
    with pytest.raises(MarketDataError) as excinfo:
        extract_curve(
            transport, TOKEN, DataScope.YIELD_CURVE_GHS, _specs(DataScope.YIELD_CURVE_GHS)
        )
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT
    assert VENDOR_INTERNAL_MARKER in error.internal_detail
    assert VENDOR_INTERNAL_MARKER not in error.bank_facing.message
    assert VENDOR_INTERNAL_MARKER not in str(error)


def test_missing_ric_row_classifies_as_unknown_instrument() -> None:
    # The FX fixture has no rating rows: pointing the rating extractor at it
    # simulates a vendor response that dropped the requested instrument.
    transport = FixtureTransport(
        FIXTURES_DIR,
        filenames={DataScope.CREDIT_RATING_GHANA_SOVEREIGN.value: "usd_ghs_spot.json"},
    )
    scope = DataScope.CREDIT_RATING_GHANA_SOVEREIGN
    with pytest.raises(MarketDataError) as excinfo:
        extract_ratings(transport, TOKEN, scope, _specs(scope))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT


def test_missing_fixture_classifies_as_vendor_unavailable() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={DataScope.YIELD_CURVE_GHS.value: "does_not_exist.json"}
    )
    with pytest.raises(MarketDataError) as excinfo:
        extract_curve(
            transport, TOKEN, DataScope.YIELD_CURVE_GHS, _specs(DataScope.YIELD_CURVE_GHS)
        )
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE


def test_default_transport_is_unavailable_until_phase_2() -> None:
    with pytest.raises(MarketDataError) as excinfo:
        extract_fx(
            UnconfiguredTransport(),
            TOKEN,
            DataScope.FX_SPOT_USD_GHS,
            _specs(DataScope.FX_SPOT_USD_GHS),
        )
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE
    assert "live RDP transport not configured (Phase 2)" in error.internal_detail
