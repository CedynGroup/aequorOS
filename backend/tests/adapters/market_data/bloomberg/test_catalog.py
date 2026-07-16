"""The shipped field catalog is exactly the §6.2 documented coverage."""

from __future__ import annotations

from app.adapters.market_data.bloomberg import CATALOG_PATH
from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import (
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)

EXPECTED_SUPPORTED = {
    DataScope.YIELD_CURVE_GHS,
    DataScope.FX_SPOT_USD_GHS,
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
}

# §6.2 exactly: seven securities, seven tenors — no 36/84-month tenors, which
# would require inventing securities the spec does not document (§16.4).
EXPECTED_CURVE_REQUESTS = (
    ("GHGGB1M Index", 1),
    ("GHGGB3M Index", 3),
    ("GHGGB6M Index", 6),
    ("GHGGB12M Index", 12),
    ("GHGGB2Y Index", 24),
    ("GHGGB5Y Index", 60),
    ("GHGGB10Y Index", 120),
)

EXPECTED_RATING_FIELDS = (
    "RTG_MDY_LT_LC_ISSUER_CREDIT",
    "RTG_SP_LT_LC_ISSUER_CREDIT",
    "RTG_FITCH_LT_LC_ISSUER_CREDIT",
)


def test_loader_accepts_shipped_catalog() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert catalog.entries


def test_every_data_scope_has_an_entry() -> None:
    catalog = load_catalog(CATALOG_PATH)
    missing = set(DataScope) - set(catalog.entries)
    assert not missing, f"catalog must declare coverage for every scope; missing {missing}"


def test_only_documented_scopes_are_supported() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert set(supported_scopes(catalog)) == EXPECTED_SUPPORTED


def test_unsupported_scopes_have_no_request_specs() -> None:
    catalog = load_catalog(CATALOG_PATH)
    for scope, entry in catalog.entries.items():
        if scope in EXPECTED_SUPPORTED:
            continue
        assert not entry.supported
        assert entry.requests == (), f"{scope.value} must not carry invented vendor references"


def test_yield_curve_ghs_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.YIELD_CURVE_GHS)
    assert [(r["security"], r["tenor_months"]) for r in requests] == list(EXPECTED_CURVE_REQUESTS)
    assert all(r["field"] == "PX_LAST" for r in requests)
    assert all(r["data_source"] == "BVAL" for r in requests)
    assert quota_units(catalog, [DataScope.YIELD_CURVE_GHS]) == 7


def test_fx_spot_usd_ghs_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.FX_SPOT_USD_GHS)
    assert requests == [{"security": "GHSUSD Curncy", "field": "PX_LAST"}]
    assert quota_units(catalog, [DataScope.FX_SPOT_USD_GHS]) == 1


def test_credit_rating_ghana_matches_spec_exactly() -> None:
    catalog = load_catalog(CATALOG_PATH)
    requests = requests_for(catalog, DataScope.CREDIT_RATING_GHANA_SOVEREIGN)
    assert [r["field"] for r in requests] == list(EXPECTED_RATING_FIELDS)
    assert all(r["security"] == "GHANA Govt" for r in requests)
    assert quota_units(catalog, [DataScope.CREDIT_RATING_GHANA_SOVEREIGN]) == 3


def test_total_quota_for_all_supported_scopes() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert quota_units(catalog, sorted(EXPECTED_SUPPORTED, key=lambda s: s.value)) == 11
    assert quota_units(catalog, list(DataScope)) == 11  # unsupported contribute zero
