"""Catalog loading contract: fail loud on malformed input, never fake support,
cross-check entity types, and honor per-bank overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.temenos_t24.catalog import (
    CatalogError,
    apply_overrides,
    load_catalog,
    load_mode_catalog,
    supported_domains,
)
from app.adapters.temenos_t24.domains import CoreBankingDomain

OFS_SUPPORTED = {
    CoreBankingDomain.GL_BALANCES,
    CoreBankingDomain.POSITIONS_LOANS,
    CoreBankingDomain.POSITIONS_DEPOSITS,
    CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS,
    CoreBankingDomain.POSITIONS_MM_PLACEMENTS,
    CoreBankingDomain.POSITIONS_MM_BORROWINGS,
    CoreBankingDomain.POSITIONS_FX_DEALS,
    CoreBankingDomain.POSITIONS_SWAPS,
    CoreBankingDomain.SECURITIES_HOLDINGS,
    CoreBankingDomain.OFF_BALANCE_LC,
    CoreBankingDomain.OFF_BALANCE_GUARANTEES,
    CoreBankingDomain.OFF_BALANCE_COMMITMENTS,
    CoreBankingDomain.COUNTERPARTY_MASTER,
    CoreBankingDomain.PRODUCT_MASTER,
    CoreBankingDomain.BUSINESS_UNITS,
    CoreBankingDomain.INSTITUTION,
}


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_shipped_ofs_catalog_loads_and_supports_priority_domains() -> None:
    catalog = load_mode_catalog("OFS")
    assert set(supported_domains(catalog)) == OFS_SUPPORTED


def test_rest_catalogs_cover_the_same_canonical_domains_as_ofs() -> None:
    # IRIS and Open API mirror the OFS canonical coverage (same domains, REST
    # source vocabulary); their live transports are the portal-gated seam.
    for mode in ("IRIS", "OPEN_API"):
        catalog = load_mode_catalog(mode)
        assert set(supported_domains(catalog)) == OFS_SUPPORTED


def test_all_three_catalogs_cover_every_domain() -> None:
    for mode in ("OFS", "IRIS", "OPEN_API"):
        catalog = load_mode_catalog(mode)
        assert set(catalog.entries) == set(CoreBankingDomain)


def test_missing_supported_flag_defaults_to_unsupported(tmp_path: Path) -> None:
    path = _write(tmp_path, "GL_BALANCES:\n  application: GENERAL.LEDGER\n")
    catalog = load_catalog(path, mode="OFS")
    assert catalog.entries[CoreBankingDomain.GL_BALANCES].supported is False


def test_unknown_domain_name_fails_loud(tmp_path: Path) -> None:
    path = _write(tmp_path, "NOT_A_DOMAIN:\n  supported: true\n")
    with pytest.raises(CatalogError, match="unknown domain"):
        load_catalog(path, mode="OFS")


def test_entity_type_conflict_fails_loud(tmp_path: Path) -> None:
    path = _write(tmp_path, "GL_BALANCES:\n  supported: true\n  entity_type: position\n")
    with pytest.raises(CatalogError, match="conflicts with"):
        load_catalog(path, mode="OFS")


def test_attribute_key_without_source_fails_loud(tmp_path: Path) -> None:
    body = (
        "POSITIONS_LOANS:\n"
        "  supported: true\n"
        "  field_map:\n"
        "    AMOUNT: balance\n"
        "  attribute_keys:\n"
        "    - never_populated\n"
    )
    path = _write(tmp_path, body)
    with pytest.raises(CatalogError, match="never be populated"):
        load_catalog(path, mode="OFS")


def test_supported_reference_domain_requires_dataset_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "BUSINESS_UNITS:\n  supported: true\n")
    with pytest.raises(CatalogError, match="dataset_key"):
        load_catalog(path, mode="OFS")


def test_unknown_entry_key_fails_loud(tmp_path: Path) -> None:
    path = _write(tmp_path, "GL_BALANCES:\n  supported: true\n  typo_field: oops\n")
    with pytest.raises(CatalogError, match="unknown key"):
        load_catalog(path, mode="OFS")


def test_bad_page_size_fails_loud(tmp_path: Path) -> None:
    path = _write(tmp_path, "GL_BALANCES:\n  supported: true\n  page_size: 0\n")
    with pytest.raises(CatalogError, match="page_size"):
        load_catalog(path, mode="OFS")


def test_override_replaces_enquiry_name() -> None:
    catalog = load_mode_catalog("OFS")
    original = catalog.entries[CoreBankingDomain.GL_BALANCES].source.enquiry
    overridden = apply_overrides(catalog, {"GL_BALANCES": {"enquiry": "BANK.CUSTOM.GL"}})
    assert overridden.entries[CoreBankingDomain.GL_BALANCES].source.enquiry == "BANK.CUSTOM.GL"
    # original catalog is unmutated
    assert catalog.entries[CoreBankingDomain.GL_BALANCES].source.enquiry == original


def test_override_can_enable_a_reference_domain_with_dataset_key() -> None:
    catalog = load_mode_catalog("OFS")
    overridden = apply_overrides(
        catalog,
        {"HISTORICAL_BALANCES": {"supported": True, "dataset_key": "historical_financials"}},
    )
    assert CoreBankingDomain.HISTORICAL_BALANCES in supported_domains(overridden)


def test_bad_override_fails_loud_like_a_bad_catalog() -> None:
    catalog = load_mode_catalog("OFS")
    with pytest.raises(CatalogError, match="unknown domain"):
        apply_overrides(catalog, {"NOPE": {"supported": True}})


def test_unknown_mode_fails_loud() -> None:
    with pytest.raises(CatalogError, match="connection mode"):
        load_mode_catalog("SOAP")


def test_priority_ofs_entries_bind_balance_ghs_to_an_lcy_field() -> None:
    catalog = load_mode_catalog("OFS")
    for domain in (
        CoreBankingDomain.POSITIONS_LOANS,
        CoreBankingDomain.POSITIONS_DEPOSITS,
        CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS,
        CoreBankingDomain.GL_BALANCES,
    ):
        entry = catalog.entries[domain]
        assert "balance_ghs" in entry.lcy_fields
        assert entry.lcy_fields["balance_ghs"]  # non-empty T24 LCY field name
