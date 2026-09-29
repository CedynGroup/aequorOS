"""T24 domain catalog loading (mirrors ``market_data/scope_translator.py``).

Each connection mode (OFS, IRIS, Open API) ships a YAML catalog mapping every
:class:`CoreBankingDomain` to the concrete T24 request coordinates for that
mode and to the canonical output schema the extractor emits. The catalog is
ground truth for what a T24 pull fetches and how its columns land in the
canonical model — versioned alongside the adapter, overridable per bank.

Four honesty rails, the first two mirrored from the market-data catalog:

- **Never fake support.** A domain without ``supported: true`` is not offered.
  A typo'd domain name fails loudly at load, never silently drops coverage.
- **Enquiry/endpoint names are installation-specific.** T24 enquiries and
  service names differ per bank; the shipped values are sensible documented
  defaults that a bank overrides via ``catalog_overrides`` on its connection.
  The loader validates structure, not that a given enquiry exists in a core.
- **A supported reference domain must name a REGISTERED dataset kind.** Presence
  of a ``dataset_key`` was checked; membership was not, so ``LIMITS`` — whose
  ``limits`` is not a :data:`REFERENCE_DATASET_KINDS` member — could be flipped
  to supported here and then failed far away: ``default_t24_mapping_config``
  raises a raw Pydantic ``ValidationError`` that enumerates every internal
  dataset kind, at onboarding, and ``canonical_reference_rows`` has a CHECK
  constraint on the same list. Refuse at load, naming the kind.
- **A supported reference domain must map every field its register REQUIRES.**
  This is the structural form of the defect fixed in ``7277913a``: T24 published
  branches under the vendor's own spelling, the ``business_units`` register
  requires ``business_unit_id``/``business_unit_name``, and so every branch row
  was refused while the pull still reported success. That was fixed by editing
  the catalogs; nothing stopped it recurring, and the next register to enforce
  its schema would have reproduced it exactly. A register that declares no
  required fields (or none at all) imposes nothing — this rail checks what a
  register actually asks for, never a shape invented here.

The request coordinates stay opaque to everything but the transport and
extractor for that mode, so T24 field vocabulary never leaks past this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.adapters.temenos_t24.domains import (
    DOMAIN_TO_ENTITY_TYPE,
    CoreBankingDomain,
)
from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS

CATALOG_DIR = Path(__file__).parent / "catalogs"
CATALOG_FILES: dict[str, str] = {
    "OFS": "ofs_catalog.yaml",
    "IRIS": "iris_catalog.yaml",
    "OPEN_API": "open_api_catalog.yaml",
}


class CatalogError(ValueError):
    """The catalog file is malformed. Fails loudly at load time, never at pull time."""


@dataclass(frozen=True)
class DomainSource:
    """Where a domain's records come from, for one connection mode.

    Exactly which of these is used depends on the mode: ``application`` +
    ``enquiry`` for OFS, ``endpoint`` for IRIS / Open API. ``selection`` is the
    default criteria template (values may contain ``{company}`` / ``{as_of}``
    placeholders the transport fills). All are opaque to callers outside the
    mode's transport + extractor.
    """

    application: str | None
    enquiry: str | None
    endpoint: str | None
    selection: dict[str, Any]
    page_size: int


@dataclass(frozen=True)
class CatalogEntry:
    """One domain's mapping for one mode.

    ``field_map`` renames raw T24 fields to canonical output keys the extractor
    emits (``{t24_field: canonical_key}``). ``attribute_keys`` marks which
    canonical keys are copied verbatim into the position/account ``attributes``
    dict (vs typed identity columns like ``currency``/``balance``). ``lcy_fields``
    binds a canonical attribute to the T24 local-currency-equivalent field used
    to populate it (e.g. ``balance_ghs`` <- ``LCY.BALANCE``). ``enum_mappings``
    and ``constants`` finish the canonicalization the extractor can't infer.
    """

    domain: CoreBankingDomain
    entity_type: str
    supported: bool
    source: DomainSource
    id_field: str | None
    field_map: dict[str, str]
    attribute_keys: tuple[str, ...]
    lcy_fields: dict[str, str]
    enum_mappings: dict[str, dict[str, str]]
    constants: dict[str, str]
    dataset_key: str | None


@dataclass(frozen=True)
class Catalog:
    """A parsed, validated T24 catalog for one connection mode."""

    mode: str
    source_path: str
    entries: dict[CoreBankingDomain, CatalogEntry]

    def entry(self, domain: CoreBankingDomain) -> CatalogEntry:
        try:
            return self.entries[domain]
        except KeyError:
            msg = f"Catalog {self.source_path} has no entry for domain {domain.name!r}."
            raise LookupError(msg) from None


_SOURCE_KEYS = ("application", "enquiry", "endpoint", "selection", "page_size")
_META_KEYS = (
    "supported",
    "entity_type",
    "id_field",
    "field_map",
    "attribute_keys",
    "lcy_fields",
    "enum_mappings",
    "constants",
    "dataset_key",
    *_SOURCE_KEYS,
)


def _require_mapping(path: Path, domain_name: str, key: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"Catalog {path}: entry {domain_name!r} {key!r} must be a mapping."
        raise CatalogError(msg)
    return value


def _require_str_mapping(path: Path, domain_name: str, key: str, value: Any) -> dict[str, str]:
    mapping = _require_mapping(path, domain_name, key, value)
    for k, v in mapping.items():
        if not isinstance(v, str):
            msg = f"Catalog {path}: entry {domain_name!r} {key!r}[{k!r}] must be a string."
            raise CatalogError(msg)
    return {str(k): v for k, v in mapping.items()}


def _parse_entry(path: Path, domain: CoreBankingDomain, entry: dict[str, Any]) -> CatalogEntry:
    name = domain.name
    for unknown in set(entry) - set(_META_KEYS):
        msg = f"Catalog {path}: entry {name!r} has unknown key {unknown!r}."
        raise CatalogError(msg)

    supported = entry.get("supported", False)
    if not isinstance(supported, bool):
        msg = f"Catalog {path}: entry {name!r} 'supported' must be a boolean."
        raise CatalogError(msg)

    declared_entity = DOMAIN_TO_ENTITY_TYPE[domain]
    entity_type = entry.get("entity_type", declared_entity)
    if entity_type != declared_entity:
        msg = (
            f"Catalog {path}: entry {name!r} entity_type {entity_type!r} conflicts with "
            f"the taxonomy ({declared_entity!r}). Fix domains.py or the catalog, not one side."
        )
        raise CatalogError(msg)

    page_size = entry.get("page_size", 500)
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
        msg = f"Catalog {path}: entry {name!r} 'page_size' must be a positive integer."
        raise CatalogError(msg)

    field_map = _require_str_mapping(path, name, "field_map", entry.get("field_map"))
    lcy_fields = _require_str_mapping(path, name, "lcy_fields", entry.get("lcy_fields"))
    populated = {*field_map.values(), *entry.get("constants", {}), *lcy_fields}
    attribute_keys = tuple(entry.get("attribute_keys", ()) or ())
    for key in attribute_keys:
        if not isinstance(key, str):
            msg = f"Catalog {path}: entry {name!r} 'attribute_keys' must be strings."
            raise CatalogError(msg)
        if key not in populated:
            msg = (
                f"Catalog {path}: entry {name!r} attribute_key {key!r} is not a field_map "
                f"target, a constant, or an lcy_field — it would never be populated."
            )
            raise CatalogError(msg)

    raw_enums = _require_mapping(path, name, "enum_mappings", entry.get("enum_mappings"))
    enum_mappings: dict[str, dict[str, str]] = {}
    for field_name, mapping in raw_enums.items():
        enum_mappings[str(field_name)] = _require_str_mapping(
            path, name, f"enum_mappings.{field_name}", mapping
        )

    constants = _require_str_mapping(path, name, "constants", entry.get("constants"))

    dataset_key = entry.get("dataset_key")
    if dataset_key is not None and not isinstance(dataset_key, str):
        msg = f"Catalog {path}: entry {name!r} 'dataset_key' must be a string."
        raise CatalogError(msg)
    if entity_type == "reference" and supported:
        if not dataset_key:
            msg = f"Catalog {path}: reference entry {name!r} is supported but has no 'dataset_key'."
            raise CatalogError(msg)
        _check_reference_destination(path, name, dataset_key, field_map, lcy_fields, constants)

    selection = _require_mapping(path, name, "selection", entry.get("selection"))
    source = DomainSource(
        application=_opt_str(path, name, "application", entry.get("application")),
        enquiry=_opt_str(path, name, "enquiry", entry.get("enquiry")),
        endpoint=_opt_str(path, name, "endpoint", entry.get("endpoint")),
        selection=selection,
        page_size=page_size,
    )

    id_field = _opt_str(path, name, "id_field", entry.get("id_field"))

    return CatalogEntry(
        domain=domain,
        entity_type=entity_type,
        supported=supported,
        source=source,
        id_field=id_field,
        field_map=field_map,
        attribute_keys=attribute_keys,
        lcy_fields=lcy_fields,
        enum_mappings=enum_mappings,
        constants=constants,
        dataset_key=dataset_key,
    )


def _check_reference_destination(  # noqa: PLR0913 - one argument per populating source
    path: Path,
    domain_name: str,
    dataset_key: str,
    field_map: dict[str, str],
    lcy_fields: dict[str, str],
    constants: dict[str, str],
) -> None:
    """Refuse a supported reference entry whose destination cannot receive it.

    Two questions, both about the DESTINATION rather than the T24 side, because
    the T24 side is installation-specific and this loader deliberately does not
    judge it:

    1. Is ``dataset_key`` a reference dataset kind the platform actually has? An
       unknown kind is refused by ``ReferenceMapping`` (Pydantic) and by the
       ``canonical_reference_rows`` CHECK constraint, so allowing it here only
       moves the failure somewhere less legible.
    2. Does the entry map every field that kind's register REQUIRES? A register
       that declares no required fields asks nothing and passes; the check never
       invents a requirement.
    """
    if dataset_key not in REFERENCE_DATASET_KINDS:
        known = ", ".join(sorted(REFERENCE_DATASET_KINDS))
        msg = (
            f"Catalog {path}: reference entry {domain_name!r} is supported but its "
            f"dataset_key {dataset_key!r} is not a reference dataset kind. Known kinds: "
            f"{known}. A new destination needs its kind registered (and migrated) first."
        )
        raise CatalogError(msg)

    # Lazy: the schema package imports the ingestion contracts, and importing it
    # at module scope would pull that graph in behind every catalog load.
    from app.domain.ingestion.reference_schemas import schema_for  # noqa: PLC0415

    schema = schema_for(dataset_key)
    if schema is None:
        return
    populated = {*field_map.values(), *lcy_fields, *constants}
    missing = [name for name in schema.required if name not in populated]
    if missing:
        msg = (
            f"Catalog {path}: reference entry {domain_name!r} is supported but never populates "
            f"{missing!r}, which the {dataset_key!r} register requires — every row would be "
            f"refused while the pull reported success. Map them in 'field_map' (or supply them "
            f"as constants)."
        )
        raise CatalogError(msg)


def _opt_str(path: Path, domain_name: str, key: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"Catalog {path}: entry {domain_name!r} {key!r} must be a string."
        raise CatalogError(msg)
    return value


def load_catalog(path: Path | str, *, mode: str) -> Catalog:
    """Parse and validate a T24 catalog YAML file for one connection mode.

    Unknown domain names fail loudly. Entries missing ``supported`` default to
    unsupported. Every declared entity_type is cross-checked against the domain
    taxonomy so the two never drift.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = f"Catalog {path} must be a mapping of CoreBankingDomain names to entries."
        raise CatalogError(msg)

    entries: dict[CoreBankingDomain, CatalogEntry] = {}
    for domain_name, entry in raw.items():
        try:
            domain = CoreBankingDomain[str(domain_name)]
        except KeyError:
            known = ", ".join(sorted(member.name for member in CoreBankingDomain))
            msg = f"Catalog {path}: unknown domain name {domain_name!r}. Known domains: {known}."
            raise CatalogError(msg) from None
        if not isinstance(entry, dict):
            msg = f"Catalog {path}: entry for {domain_name!r} must be a mapping."
            raise CatalogError(msg)
        entries[domain] = _parse_entry(path, domain, entry)

    return Catalog(mode=mode, source_path=str(path), entries=entries)


def load_mode_catalog(mode: str) -> Catalog:
    """Load the shipped catalog for a connection mode (OFS / IRIS / OPEN_API)."""
    try:
        filename = CATALOG_FILES[mode]
    except KeyError:
        known = ", ".join(sorted(CATALOG_FILES))
        msg = f"No catalog for connection mode {mode!r}. Known modes: {known}."
        raise CatalogError(msg) from None
    return load_catalog(CATALOG_DIR / filename, mode=mode)


def supported_domains(catalog: Catalog) -> list[CoreBankingDomain]:
    """Domains the catalog marks supported, in stable name order. Coverage is
    defined by catalogs at runtime, not by documentation tables."""
    return sorted(
        (domain for domain, entry in catalog.entries.items() if entry.supported),
        key=lambda domain: domain.name,
    )


def apply_overrides(catalog: Catalog, overrides: dict[str, Any] | None) -> Catalog:
    """Return a catalog with per-bank overrides merged in.

    ``overrides`` is a ``{DOMAIN_NAME: {key: value}}`` mapping from a bank's
    connection (e.g. its real enquiry names). Overrides re-run full validation,
    so a bad override fails loudly exactly like a bad shipped catalog.
    """
    if not overrides:
        return catalog

    merged: dict[CoreBankingDomain, CatalogEntry] = dict(catalog.entries)
    path = Path(catalog.source_path)
    for domain_name, patch in overrides.items():
        try:
            domain = CoreBankingDomain[str(domain_name)]
        except KeyError:
            known = ", ".join(sorted(member.name for member in CoreBankingDomain))
            msg = f"Override for unknown domain {domain_name!r}. Known domains: {known}."
            raise CatalogError(msg) from None
        if not isinstance(patch, dict):
            msg = f"Override for {domain_name!r} must be a mapping."
            raise CatalogError(msg)
        base = _entry_to_raw(merged.get(domain))
        base.update(patch)
        merged[domain] = _parse_entry(path, domain, base)

    return Catalog(mode=catalog.mode, source_path=catalog.source_path, entries=merged)


def _entry_to_raw(entry: CatalogEntry | None) -> dict[str, Any]:
    """Reconstruct the raw dict form of an entry so an override can patch it."""
    if entry is None:
        return {}
    return {
        "supported": entry.supported,
        "entity_type": entry.entity_type,
        "application": entry.source.application,
        "enquiry": entry.source.enquiry,
        "endpoint": entry.source.endpoint,
        "selection": dict(entry.source.selection),
        "page_size": entry.source.page_size,
        "id_field": entry.id_field,
        "field_map": dict(entry.field_map),
        "attribute_keys": list(entry.attribute_keys),
        "lcy_fields": dict(entry.lcy_fields),
        "enum_mappings": {k: dict(v) for k, v in entry.enum_mappings.items()},
        "constants": dict(entry.constants),
        "dataset_key": entry.dataset_key,
    }
