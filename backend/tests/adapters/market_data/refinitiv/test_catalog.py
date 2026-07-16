"""The §7.2 RIC catalog: exactly the documented entries, nothing invented."""

from __future__ import annotations

from app.adapters.market_data.refinitiv.adapter import CATALOG_PATH
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import (
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)

SPEC_SUPPORTED = {
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
}

# §7.2 documents these seven GHS tenors; 36M and 84M are intentionally absent.
SPEC_GHS_CURVE = [
    ("GH1M=", 1),
    ("GH3M=", 3),
    ("GH6M=", 6),
    ("GH1Y=", 12),
    ("GH2Y=", 24),
    ("GH5Y=", 60),
    ("GH10Y=", 120),
]

SPEC_RATING_FIELDS = [
    "TR.MoodysIssuerRating",
    "TR.SPIssuerRating",
    "TR.FitchIssuerRating",
]


def test_loader_accepts_the_catalog() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert catalog.entries


def test_every_data_scope_has_an_entry() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert set(catalog.entries) == set(DataScope)


def test_only_the_spec_documented_scopes_are_supported() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert set(supported_scopes(catalog)) == SPEC_SUPPORTED
    for scope, entry in catalog.entries.items():
        if scope not in SPEC_SUPPORTED:
            assert not entry.supported, f"{scope.value} must not fake support (§16.9)"
            assert not entry.requests, f"{scope.value} must carry no invented RICs (§16.4)"


def test_ghs_curve_entry_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.YIELD_CURVE_GHS)
    assert [(r["ric"], r["tenor_months"]) for r in requests] == SPEC_GHS_CURVE
    assert all(r["field"] == "TR.MidYield" for r in requests)
    assert catalog.entries[DataScope.YIELD_CURVE_GHS].quota_units_per_pull == 7
    # The spec catalog omits the 36M/84M standard tenors — do not add them.
    tenors = {r["tenor_months"] for r in requests}
    assert 36 not in tenors
    assert 84 not in tenors


def test_fx_spot_entry_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.FX_SPOT_USD_GHS)
    assert requests == [{"ric": "USDGHS=R", "field": "TR.MidPrice"}]
    assert catalog.entries[DataScope.FX_SPOT_USD_GHS].quota_units_per_pull == 1


def test_ghana_rating_entry_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.CREDIT_RATING_GHANA_SOVEREIGN)
    assert [r["field"] for r in requests] == SPEC_RATING_FIELDS
    assert all(r["ric"] == "GH=" for r in requests)
    assert catalog.entries[DataScope.CREDIT_RATING_GHANA_SOVEREIGN].quota_units_per_pull == 3


def test_quota_units_for_full_supported_pull() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert quota_units(catalog, sorted(SPEC_SUPPORTED, key=lambda s: s.value)) == 11
