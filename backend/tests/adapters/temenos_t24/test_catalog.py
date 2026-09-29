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
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS

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


# ---------------------------------------------------------------------------
# The mapping gaps (P5-C, 2026-09-28)
# ---------------------------------------------------------------------------

#: The three domains that stay unsupported, and the reason each one is NOT a
#: missing field name. Reviewed in full — see the foot of ofs_catalog.yaml and
#: .ai/bi_recon/p5c_t24_mapping_report.md. Pinned here so a later change cannot
#: flip one on without a reviewer being told which argument it has to answer.
UNSUPPORTED_DOMAINS = {
    CoreBankingDomain.LIMITS: "no registered destination and no module consumer",
    CoreBankingDomain.CASHFLOWS_SCHEDULED: "scheduled payments are not realised cash flows",
    CoreBankingDomain.HISTORICAL_BALANCES: "a monthly P&L needs classification, not a rename",
}
_MODES = ("OFS", "IRIS", "OPEN_API")
_UNSUPPORTED_ORDERED: tuple[CoreBankingDomain, ...] = tuple(
    sorted(UNSUPPORTED_DOMAINS, key=lambda domain: domain.name)
)


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize("domain", _UNSUPPORTED_ORDERED)
def test_reviewed_gaps_stay_unsupported(mode: str, domain: CoreBankingDomain) -> None:
    catalog = load_mode_catalog(mode)
    reason = UNSUPPORTED_DOMAINS[domain]
    assert not catalog.entries[domain].supported, (
        f"{domain.name} was flipped to supported in the {mode} catalog. The reviewed reason it "
        f"was left open is: {reason}. Answer that in the catalog comment before enabling it."
    )
    assert domain not in supported_domains(catalog)


@pytest.mark.parametrize("mode", _MODES)
def test_limits_destination_is_not_a_registered_dataset_kind(mode: str) -> None:
    """The specific reason LIMITS stays open, asserted rather than described.

    ``limits`` is not a reference dataset kind, so a LIMITS row has nowhere to
    land: ``ReferenceMapping`` refuses the kind and so does the
    ``canonical_reference_rows`` CHECK constraint. If someone registers the kind,
    this fails and they are pointed at the rest of the work (a module that reads
    it, and the LIMIT.REFERENCE field names the bank must supply).
    """
    entry = load_mode_catalog(mode).entries[CoreBankingDomain.LIMITS]
    assert entry.dataset_key == "limits"
    assert entry.dataset_key not in REFERENCE_DATASET_KINDS


@pytest.mark.parametrize("mode", _MODES)
def test_enabling_limits_is_refused_at_load(mode: str) -> None:
    """The refusal is ENFORCED, not recorded — including via a bank's overrides.

    Before this rail the override succeeded here and failed later, inside
    ``default_t24_mapping_config``, as a raw Pydantic error enumerating every
    internal dataset kind while building a bank's mapping config.
    """
    catalog = load_mode_catalog(mode)
    with pytest.raises(CatalogError, match="not a reference dataset kind"):
        apply_overrides(catalog, {"LIMITS": {"supported": True}})


@pytest.mark.parametrize("mode", _MODES)
def test_supported_reference_entry_must_populate_its_registers_required_fields(mode: str) -> None:
    """The structural form of the defect fixed in 7277913a.

    T24 published branches under the vendor's spelling, ``business_units``
    requires ``business_unit_id``/``business_unit_name``, so every branch row was
    refused while the pull reported success. That was fixed by editing the
    catalogs; this rail is what stops it recurring.
    """
    catalog = load_mode_catalog(mode)
    entry = catalog.entries[CoreBankingDomain.BUSINESS_UNITS]
    kept = {
        t24: canonical
        for t24, canonical in entry.field_map.items()
        if canonical != "business_unit_name"
    }
    assert len(kept) == len(entry.field_map) - 1, "the name mapping should have been dropped"
    with pytest.raises(CatalogError, match="business_unit_name"):
        apply_overrides(catalog, {"BUSINESS_UNITS": {"field_map": kept}})


@pytest.mark.parametrize("mode", _MODES)
@pytest.mark.parametrize(
    "domain",
    (
        CoreBankingDomain.POSITIONS_MM_PLACEMENTS,
        CoreBankingDomain.POSITIONS_MM_BORROWINGS,
        CoreBankingDomain.SECURITIES_HOLDINGS,
    ),
)
def test_treasury_positions_map_the_branch_field_they_already_select_on(
    mode: str, domain: CoreBankingDomain
) -> None:
    """The closed gap: these three dropped the branch field they receive.

    The identifier is not new — it is the entry's own selection key, and the
    lending/deposit/current-account entries already map it. Dropping it left
    interbank placements, interbank borrowings and securities holdings with no
    ``branch_id``, so the BI branch dimension could not place a T24 bank's
    treasury book at all.
    """
    catalog = load_mode_catalog(mode)
    entry = catalog.entries[domain]
    branch_fields = [t24 for t24, key in entry.field_map.items() if key == "branch_id"]
    assert len(branch_fields) == 1, f"{domain.name} does not map branch_id"
    assert "branch_id" in entry.attribute_keys, "branch_id must reach the position attributes"

    # Provenance, asserted: the same T24 field is the entry's selection key and
    # is mapped identically by the lending entry. Neither is a new field name.
    (branch_field,) = branch_fields
    assert branch_field in entry.source.selection
    loans = catalog.entries[CoreBankingDomain.POSITIONS_LOANS]
    assert loans.field_map.get(branch_field) == "branch_id"


@pytest.mark.parametrize("mode", _MODES)
def test_every_branch_bearing_domain_names_exactly_one_branch_field(mode: str) -> None:
    """One field per side, so the join has a single unambiguous source.

    The two sides are NOT the same T24 field name and must not be asserted to
    be: the register's ``business_unit_id`` comes off the COMPANY application's
    own key, while a position's ``branch_id`` is the company code carried on the
    contract. They are the same identifier SPACE, which is a claim about values
    and is proved end to end on real fixture data by the contract suite's
    ``test_treasury_branch_ids_resolve_in_the_business_unit_register``.
    """
    catalog = load_mode_catalog(mode)
    units = catalog.entries[CoreBankingDomain.BUSINESS_UNITS]
    unit_id_fields = [t24 for t24, key in units.field_map.items() if key == "business_unit_id"]
    assert len(unit_id_fields) == 1
    for domain in (
        CoreBankingDomain.POSITIONS_LOANS,
        CoreBankingDomain.POSITIONS_DEPOSITS,
        CoreBankingDomain.POSITIONS_CURRENT_ACCOUNTS,
        CoreBankingDomain.POSITIONS_MM_PLACEMENTS,
        CoreBankingDomain.POSITIONS_MM_BORROWINGS,
        CoreBankingDomain.SECURITIES_HOLDINGS,
    ):
        entry = catalog.entries[domain]
        branch_fields = [t24 for t24, key in entry.field_map.items() if key == "branch_id"]
        assert len(branch_fields) == 1, f"{domain.name} must name exactly one branch field"
