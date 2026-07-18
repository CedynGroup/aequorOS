from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.market_data.scope_taxonomy import DataScope
from app.adapters.market_data.scope_translator import (
    CatalogError,
    load_catalog,
    quota_units,
    requests_for,
    supported_scopes,
)

SAMPLE_CATALOG = """
YIELD_CURVE_GHS:
  data_source: BVAL
  fields:
    - security: "GHGGB3M Index"
      field: "PX_LAST"
      tenor_months: 3
    - security: "GHGGB1Y Index"
      field: "PX_LAST"
      tenor_months: 12
  quota_units_per_pull: 2
  supported: true

FX_SPOT_USD_GHS:
  security: "USDGHS Curncy"
  field: "PX_LAST"
  quota_units_per_pull: 1
  supported: true

CREDIT_RATING_GHANA_SOVEREIGN:
  security: "GHANA Govt"
  fields:
    - "RTG_MDY_LT_LC_ISSUER_CREDIT"
    - "RTG_SP_LT_LC_ISSUER_CREDIT"
  quota_units_per_pull: 2
  supported: true

MACRO_GHANA_GDP_FORECAST:
  quota_units_per_pull: 4
"""

REFINITIV_SHAPE = """
YIELD_CURVE_GHS:
  rics:
    - ric: "GH3M="
      field: "TR.MidYield"
      tenor_months: 3
  quota_units_per_pull: 1
  supported: true

FX_SPOT_USD_GHS:
  ric: "USDGHS=R"
  field: "TR.MidPrice"
  quota_units_per_pull: 1
  supported: true
"""


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    path = tmp_path / "field_catalog.yaml"
    path.write_text(SAMPLE_CATALOG, encoding="utf-8")
    return path


def test_load_catalog_parses_entries(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    assert set(catalog.entries) == {
        DataScope.YIELD_CURVE_GHS,
        DataScope.FX_SPOT_USD_GHS,
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
        DataScope.MACRO_GHANA_GDP_FORECAST,
    }


def test_supported_scopes_excludes_entries_without_supported_flag(catalog_path: Path) -> None:
    # §16.9: entries missing the supported flag default to unsupported.
    catalog = load_catalog(catalog_path)
    assert supported_scopes(catalog) == [
        DataScope.CREDIT_RATING_GHANA_SOVEREIGN,
        DataScope.FX_SPOT_USD_GHS,
        DataScope.YIELD_CURVE_GHS,
    ]
    assert not catalog.entries[DataScope.MACRO_GHANA_GDP_FORECAST].supported


def test_unknown_scope_name_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("YIELD_CURVE_XYZ:\n  supported: true\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="unknown scope name 'YIELD_CURVE_XYZ'"):
        load_catalog(path)


def test_requests_for_curve_normalizes_field_list(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    requests = requests_for(catalog, DataScope.YIELD_CURVE_GHS)
    assert len(requests) == 2
    assert requests[0]["security"] == "GHGGB3M Index"
    assert requests[0]["field"] == "PX_LAST"
    assert requests[0]["tenor_months"] == 3
    # Entry-level context (data_source) is merged into every request.
    assert all(request["data_source"] == "BVAL" for request in requests)


def test_requests_for_single_instrument_shape(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    requests = requests_for(catalog, DataScope.FX_SPOT_USD_GHS)
    assert len(requests) == 1
    assert requests[0]["security"] == "USDGHS Curncy"
    assert requests[0]["field"] == "PX_LAST"


def test_requests_for_bare_field_strings_shape(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    requests = requests_for(catalog, DataScope.CREDIT_RATING_GHANA_SOVEREIGN)
    assert [request["field"] for request in requests] == [
        "RTG_MDY_LT_LC_ISSUER_CREDIT",
        "RTG_SP_LT_LC_ISSUER_CREDIT",
    ]
    assert all(request["security"] == "GHANA Govt" for request in requests)


def test_refinitiv_ric_shapes_normalize(tmp_path: Path) -> None:
    path = tmp_path / "ric_catalog.yaml"
    path.write_text(REFINITIV_SHAPE, encoding="utf-8")
    catalog = load_catalog(path)
    curve_requests = requests_for(catalog, DataScope.YIELD_CURVE_GHS)
    assert curve_requests == [{"ric": "GH3M=", "field": "TR.MidYield", "tenor_months": 3}]
    fx_requests = requests_for(catalog, DataScope.FX_SPOT_USD_GHS)
    assert fx_requests == [{"ric": "USDGHS=R", "field": "TR.MidPrice"}]


def test_requests_for_unknown_scope_raises(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    with pytest.raises(LookupError, match="no entry for scope"):
        requests_for(catalog, DataScope.YIELD_CURVE_ZAR)


def test_quota_units_sums_selected_scopes(catalog_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    assert quota_units(catalog, [DataScope.YIELD_CURVE_GHS]) == 2
    assert quota_units(catalog, [DataScope.YIELD_CURVE_GHS, DataScope.FX_SPOT_USD_GHS]) == 3
    # Scopes missing from the catalog contribute zero, never raise.
    assert quota_units(catalog, [DataScope.YIELD_CURVE_ZAR]) == 0


def test_negative_quota_units_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_units.yaml"
    path.write_text(
        "FX_SPOT_USD_GHS:\n  supported: true\n  quota_units_per_pull: -1\n", encoding="utf-8"
    )
    with pytest.raises(CatalogError, match="non-negative integer"):
        load_catalog(path)


def test_non_mapping_catalog_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- FX_SPOT_USD_GHS\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="must be a mapping"):
        load_catalog(path)
