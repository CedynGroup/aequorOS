"""Extractors against recorded fixtures: shapes, classification, no leaks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.adapters.market_data.bloomberg import (
    CATALOG_PATH,
    BloombergSession,
    FixtureTransport,
    UnavailableTransport,
)
from app.adapters.market_data.bloomberg.extractors.credit_data import extract_ratings
from app.adapters.market_data.bloomberg.extractors.curves import extract_curve
from app.adapters.market_data.bloomberg.extractors.fx import extract_fx_spot
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import load_catalog, requests_for
from tests.adapters.market_data.bloomberg.conftest import FIXTURE_FILENAMES, FIXTURES_DIR
from tests.adapters.market_data.contract import VENDOR_INTERNAL_MARKER

CATALOG = load_catalog(CATALOG_PATH)


@pytest.fixture
def session() -> BloombergSession:
    return BloombergSession(
        application_identifier="AEQUOROS_MKTDATA_PROD",
        serial_number="889900",
        authentication_endpoint=None,
        subscription_tier="b-pipe-enterprise",
    )


@pytest.fixture
def transport() -> FixtureTransport:
    return FixtureTransport(FIXTURES_DIR, filenames=FIXTURE_FILENAMES)


def test_extract_curve_returns_seven_tenor_observations(
    session: BloombergSession, transport: FixtureTransport
) -> None:
    scope = DataScope.YIELD_CURVE_GHS
    extraction = extract_curve(session, transport, scope, requests_for(CATALOG, scope))
    assert [obs.tenor_months for obs in extraction.observations] == [1, 3, 6, 12, 24, 60, 120]
    assert extraction.observations[0].security == "GHGGB1M Index"
    assert extraction.observations[0].field == "PX_LAST"
    assert extraction.observations[0].value == Decimal("15.8")
    assert extraction.observations[-1].value == Decimal("21.4")
    # The raw response is preserved untouched for raw-tier persistence.
    assert extraction.raw_response["vendor_internal"]["debug"] == VENDOR_INTERNAL_MARKER


def test_extract_fx_spot_returns_single_price(
    session: BloombergSession, transport: FixtureTransport
) -> None:
    scope = DataScope.FX_SPOT_USD_GHS
    extraction = extract_fx_spot(session, transport, scope, requests_for(CATALOG, scope))
    assert extraction.observation.security == "USDGHS Curncy"
    assert extraction.observation.value == Decimal("12.85")


def test_extract_ratings_returns_all_three_agencies(
    session: BloombergSession, transport: FixtureTransport
) -> None:
    scope = DataScope.CREDIT_RATING_GHANA_SOVEREIGN
    extraction = extract_ratings(session, transport, scope, requests_for(CATALOG, scope))
    assert [(obs.field, obs.rating_text) for obs in extraction.observations] == [
        ("RTG_MDY_LT_LC_ISSUER_CREDIT", "Caa1"),
        ("RTG_SP_LT_LC_ISSUER_CREDIT", "CCC+"),
        ("RTG_FITCH_LT_LC_ISSUER_CREDIT", "CCC"),
    ]
    assert all(obs.security == "GHANA Govt" for obs in extraction.observations)


def test_security_error_classifies_as_unknown_instrument(session: BloombergSession) -> None:
    scope = DataScope.YIELD_CURVE_GHS
    transport = FixtureTransport(FIXTURES_DIR, filenames={scope.value: "unknown_instrument.json"})
    with pytest.raises(MarketDataError) as excinfo:
        extract_curve(session, transport, scope, requests_for(CATALOG, scope))
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT
    assert VENDOR_INTERNAL_MARKER not in error.bank_facing.message
    assert VENDOR_INTERNAL_MARKER not in str(error)
    # The raw vendor securityError is preserved for engineering diagnostics.
    assert VENDOR_INTERNAL_MARKER in error.internal_detail
    assert "BAD_SEC" in error.internal_detail


def test_missing_fixture_classifies_as_vendor_unavailable(session: BloombergSession) -> None:
    scope = DataScope.YIELD_CURVE_GHS
    transport = FixtureTransport(FIXTURES_DIR, filenames={scope.value: "never_recorded.json"})
    with pytest.raises(MarketDataError) as excinfo:
        extract_curve(session, transport, scope, requests_for(CATALOG, scope))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE


def test_default_transport_is_unavailable(session: BloombergSession) -> None:
    scope = DataScope.FX_SPOT_USD_GHS
    with pytest.raises(MarketDataError) as excinfo:
        extract_fx_spot(session, UnavailableTransport(), scope, requests_for(CATALOG, scope))
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE
    assert "Phase 2" in error.internal_detail
    assert "Phase 2" not in error.bank_facing.message


def test_not_permitted_session_classifies_before_any_request(
    transport: FixtureTransport,
) -> None:
    session = BloombergSession(
        application_identifier="APP",
        serial_number="1",
        authentication_endpoint=None,
        subscription_tier=None,
        scopes_permitted=False,
    )
    scope = DataScope.CREDIT_RATING_GHANA_SOVEREIGN
    with pytest.raises(MarketDataError) as excinfo:
        extract_ratings(session, transport, scope, requests_for(CATALOG, scope))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.SCOPE_NOT_PERMITTED


class _StubTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def request(self, session: BloombergSession, request_spec: dict[str, Any]) -> dict[str, Any]:
        return self._response


def test_malformed_response_classifies_as_vendor_unavailable(
    session: BloombergSession,
) -> None:
    scope = DataScope.YIELD_CURVE_GHS
    with pytest.raises(MarketDataError) as excinfo:
        extract_curve(session, _StubTransport({}), scope, requests_for(CATALOG, scope))
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE
    assert "securityData" in error.internal_detail


def test_missing_field_classifies_as_unknown_instrument(session: BloombergSession) -> None:
    scope = DataScope.FX_SPOT_USD_GHS
    response = {"securityData": [{"security": "USDGHS Curncy", "fieldData": {}}]}
    with pytest.raises(MarketDataError) as excinfo:
        extract_fx_spot(session, _StubTransport(response), scope, requests_for(CATALOG, scope))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT


def test_non_numeric_value_classifies_as_unknown_instrument(
    session: BloombergSession,
) -> None:
    scope = DataScope.FX_SPOT_USD_GHS
    response = {"securityData": [{"security": "USDGHS Curncy", "fieldData": {"PX_LAST": "N.A."}}]}
    with pytest.raises(MarketDataError) as excinfo:
        extract_fx_spot(session, _StubTransport(response), scope, requests_for(CATALOG, scope))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.UNKNOWN_INSTRUMENT
