"""Bloomberg hardening: cert auth, fenced live seams, FX forwards, macro, catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.adapters.market_data.base import CredentialSet
from app.adapters.market_data.bloomberg import (
    CATALOG_PATH,
    BloombergSession,
    LiveBloombergSessionProvider,
    SimulatedSessionProvider,
    certificate_is_valid_pem,
)
from app.adapters.market_data.bloomberg.extractors.fx import extract_fx_forward
from app.adapters.market_data.bloomberg.extractors.macro_series import extract_macro
from app.adapters.market_data.bloomberg.translators.fx_to_canonical import fx_forward_bundle
from app.adapters.market_data.bloomberg.translators.macro_to_canonical import macro_bundle
from app.adapters.market_data.bloomberg.transport import FixtureTransport, LiveBlpTransport
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import load_catalog, requests_for
from tests.adapters.market_data.bloomberg.conftest import VALID_CREDENTIAL_PAYLOAD

FIXTURES_DIR = Path(__file__).with_name("fixtures")
_SPEC_SUPPORTED = {
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
}


def _credentials(payload: dict[str, Any]) -> CredentialSet:
    return CredentialSet(
        institution_id="test-bank",
        vendor="bloomberg",
        credentials=payload,
        issued_at=datetime(2026, 1, 5, tzinfo=UTC),
        expires_at=None,
    )


def _session() -> BloombergSession:
    return BloombergSession(
        application_identifier="APP",
        serial_number="1",
        authentication_endpoint=None,
        subscription_tier=None,
    )


# -- Certificate-based auth (§6.1) -------------------------------------------


@pytest.mark.parametrize(
    ("cert", "valid"),
    [
        ("-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----", True),
        ("-----BEGIN CERTIFICATE-----\n\n-----END CERTIFICATE-----", False),
        ("not a pem", False),
        ("-----BEGIN CERTIFICATE-----QUJD", False),
    ],
)
def test_certificate_pem_validation(cert: str, valid: bool) -> None:
    assert certificate_is_valid_pem(cert) is valid


def test_malformed_certificate_is_credential_invalid() -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    payload["certificate"] = "garbage-not-a-pem"
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedSessionProvider().open_session(_credentials(payload))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.CREDENTIAL_INVALID
    assert "certificate" in excinfo.value.internal_detail


def test_expired_certificate_is_credential_expired() -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    payload["certificate_not_after"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with pytest.raises(MarketDataError) as excinfo:
        SimulatedSessionProvider().open_session(_credentials(payload))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.CREDENTIAL_EXPIRED


def test_future_dated_certificate_opens_session() -> None:
    payload = dict(VALID_CREDENTIAL_PAYLOAD)
    payload["certificate_not_after"] = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    session = SimulatedSessionProvider().open_session(_credentials(payload))
    assert session.application_identifier == "AEQUOROS_MKTDATA_PROD"


def test_live_session_provider_is_fenced() -> None:
    # blpapi is not installed in CI; the provider classifies as unavailable
    # rather than importing or dialing the vendor (Q03 fence).
    with pytest.raises(MarketDataError) as excinfo:
        LiveBloombergSessionProvider().open_session(_credentials(dict(VALID_CREDENTIAL_PAYLOAD)))
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE


# -- Live transport fenced + rate-limit wiring -------------------------------


def test_live_transport_is_fenced_after_rate_limit() -> None:
    transport = LiveBlpTransport()
    with pytest.raises(MarketDataError) as excinfo:
        transport.request(_session(), {"scope": "YIELD_CURVE_GHS", "data_source": "BVAL"})
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE
    assert "fenced" in error.internal_detail
    # The rate limiter is wired and configured on the live transport.
    assert transport.rate_limiter.capacity > 0
    assert transport.retry_policy.max_attempts >= 1


# -- FX forwards (§5.2) ------------------------------------------------------


def test_fx_forward_extract_and_translate() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"FX_FORWARD_USD_GHS_3M": "usdghs_3m_forward.json"}
    )
    requests = [{"security": "USDGHS3M FWD Curncy", "field": "PX_LAST"}]
    extraction = extract_fx_forward(
        _session(), transport, DataScope.FX_FORWARD_USD_GHS_3M, requests
    )
    bundle = fx_forward_bundle(DataScope.FX_FORWARD_USD_GHS_3M, extraction.observation)
    assert len(bundle.fx_rates) == 1
    fx = bundle.fx_rates[0]
    assert fx.base_currency == "USD"
    assert fx.quote_currency == "GHS"
    assert fx.rate_type == "forward"
    assert fx.tenor_months == 3
    assert fx.rate == Decimal("13.42000000")
    assert fx.source_reference == "USDGHS3M FWD Curncy"
    assert bundle.sample_values == {"USD/GHS 3M forward": "13.4200"}


def test_fx_forward_bundle_rejects_non_forward_scope() -> None:
    session = _session()
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"FX_FORWARD_USD_GHS_3M": "usdghs_3m_forward.json"}
    )
    extraction = extract_fx_forward(
        session,
        transport,
        DataScope.FX_FORWARD_USD_GHS_3M,
        [{"security": "USDGHS3M FWD Curncy", "field": "PX_LAST"}],
    )
    with pytest.raises(ValueError, match="not an FX forward scope"):
        fx_forward_bundle(DataScope.FX_SPOT_USD_GHS, extraction.observation)


# -- Macro forecasts (§5.2) --------------------------------------------------


def test_macro_extract_and_translate() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"MACRO_GHANA_GDP_FORECAST": "ghana_gdp_forecast.json"}
    )
    requests = [
        {
            "security": "GH_GDP_FCST Index",
            "field": "ECO_FORECAST",
            "scenario": "base",
            "horizon_months": 12,
        }
    ]
    raw, observations = extract_macro(
        _session(), transport, DataScope.MACRO_GHANA_GDP_FORECAST, requests
    )
    assert raw["securityData"]
    bundle = macro_bundle(DataScope.MACRO_GHANA_GDP_FORECAST, observations)
    assert len(bundle.indices) == 1
    index = bundle.indices[0]
    assert index.index_code == "GHANA_GDP_FORECAST"
    assert index.value == Decimal("4.2")
    assert index.scenario == "base"
    assert index.horizon_months == 12
    assert index.source_reference == "GH_GDP_FCST Index/ECO_FORECAST"
    assert bundle.sample_values == {"GHANA_GDP_FORECAST (base, 1Y)": "4.2"}


# -- Catalog: complete coverage with verification markers --------------------


def _raw_catalog() -> dict[str, Any]:
    return yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8"))


def test_every_scope_is_either_supported_or_verification_required() -> None:
    raw = _raw_catalog()
    for scope in DataScope:
        entry = raw[scope.name]
        if scope in _SPEC_SUPPORTED:
            assert entry.get("supported") is True
            assert "verification_required" not in entry
        else:
            assert entry.get("supported", False) is False
            assert entry.get("verification_required") is True, (
                f"{scope.name} must be flagged verification_required"
            )
            assert "verification" in entry, f"{scope.name} must carry a verification block"
            assert entry["verification"].get("doc"), (
                f"{scope.name} verification block must name the vendor doc to consult"
            )


def test_no_unconfirmed_scope_carries_an_invented_security() -> None:
    catalog = load_catalog(CATALOG_PATH)
    for scope, entry in catalog.entries.items():
        if scope in _SPEC_SUPPORTED:
            continue
        # No invented Bloomberg security reaches parsed request specs.
        assert entry.requests == ()
        assert requests_for(catalog, scope) == []
