"""Refinitiv hardening: token caching, fenced live seams, forwards, macro, catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.refinitiv import (
    CachingTokenProvider,
    RdpTokenProvider,
)
from app.adapters.market_data.refinitiv.adapter import CATALOG_PATH
from app.adapters.market_data.refinitiv.extractors.fx import extract_fx
from app.adapters.market_data.refinitiv.extractors.macro_series import extract_macro
from app.adapters.market_data.refinitiv.translators.fx_to_canonical import fx_forward_to_bundle
from app.adapters.market_data.refinitiv.translators.macro_to_canonical import macro_to_bundle
from app.adapters.market_data.refinitiv.transport import FixtureTransport, LiveRdpTransport
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import load_catalog

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SPEC_SUPPORTED = {
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
}


# -- Token caching / refresh (§7.1) ------------------------------------------


class _CountingProvider:
    def __init__(self, expiry: datetime) -> None:
        self.calls = 0
        self._expiry = expiry

    def acquire(self, credentials: dict[str, Any]) -> tuple[str, datetime]:
        self.calls += 1
        return f"tok-{self.calls}", self._expiry


def test_caching_token_provider_reuses_within_lifetime() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    expiry = t0 + timedelta(hours=4)
    now = [t0]
    inner = _CountingProvider(expiry)
    cache = CachingTokenProvider(inner, clock=lambda: now[0])
    creds = {"client_id": "app-1", "client_secret": "s"}

    token1, _ = cache.acquire(creds)
    now[0] = t0 + timedelta(hours=1)
    token2, _ = cache.acquire(creds)
    assert token1 == token2 == "tok-1"
    assert inner.calls == 1


def test_caching_token_provider_refreshes_near_expiry() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    expiry = t0 + timedelta(hours=4)
    now = [t0]
    inner = _CountingProvider(expiry)
    cache = CachingTokenProvider(inner, clock=lambda: now[0])
    creds = {"client_id": "app-1", "client_secret": "s"}

    cache.acquire(creds)
    # Inside the 5-minute refresh skew before expiry -> re-acquire.
    now[0] = expiry - timedelta(minutes=1)
    token2, _ = cache.acquire(creds)
    assert token2 == "tok-2"
    assert inner.calls == 2


def test_caching_token_provider_is_keyed_by_client_id() -> None:
    expiry = datetime(2026, 1, 1, 16, 0, tzinfo=UTC)
    inner = _CountingProvider(expiry)
    cache = CachingTokenProvider(inner, clock=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    cache.acquire({"client_id": "app-1", "client_secret": "s"})
    cache.acquire({"client_id": "app-2", "client_secret": "s"})
    assert inner.calls == 2  # distinct apps never share a token


def test_rdp_token_provider_is_fenced() -> None:
    with pytest.raises(MarketDataError) as excinfo:
        RdpTokenProvider().acquire({"client_id": "app-1", "client_secret": "s"})
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE


# -- Live transport fenced + rate-limit wiring -------------------------------


def test_live_transport_is_fenced_after_rate_limit() -> None:
    transport = LiveRdpTransport()
    with pytest.raises(MarketDataError) as excinfo:
        transport.fetch("session-token", {"scope": "YIELD_CURVE_GHS"})
    error = excinfo.value
    assert error.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE
    assert "fenced" in error.internal_detail
    assert transport.rate_limiter.capacity > 0


# -- FX forwards (§5.2) ------------------------------------------------------


def test_fx_forward_extract_and_translate() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"FX_FORWARD_USD_GHS_3M": "usd_ghs_3m_forward.json"}
    )
    specs = [{"ric": "USDGHS3M=", "field": "TR.MidPrice"}]
    _, observations = extract_fx(transport, "tok", DataScope.FX_FORWARD_USD_GHS_3M, specs)
    bundle = fx_forward_to_bundle(DataScope.FX_FORWARD_USD_GHS_3M, observations)
    assert len(bundle.fx_rates) == 1
    fx = bundle.fx_rates[0]
    assert fx.base_currency == "USD"
    assert fx.quote_currency == "GHS"
    assert fx.rate_type == "forward"
    assert fx.tenor_months == 3
    assert fx.rate == Decimal("13.42")
    assert fx.source_reference == "USDGHS3M="
    assert bundle.sample_values == {"USD/GHS 3M": "13.42"}


def test_fx_forward_bundle_rejects_non_forward_scope() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"FX_FORWARD_USD_GHS_3M": "usd_ghs_3m_forward.json"}
    )
    _, observations = extract_fx(
        transport,
        "tok",
        DataScope.FX_FORWARD_USD_GHS_3M,
        [{"ric": "USDGHS3M=", "field": "TR.MidPrice"}],
    )
    with pytest.raises(ValueError, match="only translates FX_FORWARD"):
        fx_forward_to_bundle(DataScope.FX_SPOT_USD_GHS, observations)


# -- Macro forecasts (§5.2) --------------------------------------------------


def test_macro_extract_and_translate() -> None:
    transport = FixtureTransport(
        FIXTURES_DIR, filenames={"MACRO_GHANA_GDP_FORECAST": "ghana_gdp_forecast.json"}
    )
    specs = [
        {
            "ric": "GHGDP=ECI",
            "field": "TR.EconForecast",
            "scenario": "base",
            "horizon_months": 12,
        }
    ]
    _, observations = extract_macro(transport, "tok", DataScope.MACRO_GHANA_GDP_FORECAST, specs)
    bundle = macro_to_bundle(DataScope.MACRO_GHANA_GDP_FORECAST, observations)
    assert len(bundle.indices) == 1
    index = bundle.indices[0]
    assert index.index_code == "GHANA_GDP_FORECAST"
    assert index.value == Decimal("4.2")
    assert index.scenario == "base"
    assert index.horizon_months == 12
    assert index.source_reference == "GHGDP=ECI"
    assert bundle.sample_values == {"GHANA_GDP_FORECAST (base, 1Y)": "4.2"}


# -- Catalog: complete coverage with verification markers --------------------


def test_every_scope_is_either_supported_or_verification_required() -> None:
    raw = yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8"))
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
            assert "verification" in entry
            assert entry["verification"].get("doc"), (
                f"{scope.name} verification block must name the vendor doc to consult"
            )


def test_no_unconfirmed_scope_carries_an_invented_ric() -> None:
    catalog = load_catalog(CATALOG_PATH)
    for scope, entry in catalog.entries.items():
        if scope in _SPEC_SUPPORTED:
            continue
        assert entry.requests == ()
