"""Catalog-driven extraction: staged T24 payloads -> native RawRecords.

The extractor owns all T24 *structural* semantics — OFS marker parsing, LCY-
field selection, per-domain field renaming, position typing via catalog
constants — and emits flat :class:`RawRecord` objects whose keys are already the
canonical field/attribute names. Everything *mapping-level* (enum translation,
type coercion, attribute copying, product categorization) stays in the generic
``translate`` layer, driven by the versioned :class:`MappingConfig`, so a
translation is fully reproducible from the recorded mapping.

One generic engine, driven by the mode catalog, covers every domain: renames
``field_map`` T24 fields to canonical keys, pulls ``lcy_fields`` into the LCY-
equivalent attribute keys, and injects ``constants`` (position_type, CCF,
rating source). Enum-coded values are left RAW here on purpose — the mapping's
``enum_mappings`` resolve them downstream. Reference domains are preserved as
whole stringified rows under their ``dataset_kind``.

The engine is mode-agnostic: an OFS payload element is a raw OFS response
string (parsed via the codec); a REST payload element is already a field dict.
Both collapse to the same :class:`~app.adapters.temenos_t24.ofs.OfsRecord`
shape before field extraction.
"""

from __future__ import annotations

from typing import Any

from app.adapters.temenos_t24.catalog import Catalog, CatalogEntry
from app.adapters.temenos_t24.domains import CoreBankingDomain
from app.adapters.temenos_t24.ofs import OfsRecord, parse_ofs_response
from app.domain.ingestion.contracts import RawRecord, SourceTableSummary


class BundleError(ValueError):
    """The staged T24 bundle is unreadable or not envelope-shaped."""


def _as_ofs_records(element: Any) -> list[OfsRecord]:
    """Collapse one staged payload element into OfsRecords.

    - ``str`` -> an OFS response block, parsed by the codec (may hold several
      field-marker-separated records).
    - ``dict`` -> an already-structured record (REST modes); its keys are T24
      field names, values scalar/multivalue.
    """
    if isinstance(element, str):
        response = parse_ofs_response(element)
        if not response.ok:
            raise BundleError(f"OFS error block in staged bundle (status {response.error_code!r})")
        return list(response.records)
    if isinstance(element, dict):
        record_id = str(element.get("@ID", element.get("id", "")))
        fields = {str(k): v for k, v in element.items() if k not in ("@ID", "id")}
        return [OfsRecord(record_id=record_id, fields=fields)]
    raise BundleError(f"unsupported staged payload element of type {type(element).__name__}")


def _entity_record(entry: CatalogEntry, record: OfsRecord, source_locator: str) -> RawRecord:
    """Build one canonical-keyed entity RawRecord from an OfsRecord."""
    data: dict[str, Any] = {}
    if record.record_id:
        data["source_reference"] = record.record_id
    for t24_field, canonical_key in entry.field_map.items():
        value = record.scalar(t24_field)
        # The id field is often the OFS record id, not a separate assignment.
        if value is None and t24_field == entry.id_field:
            value = record.record_id or None
        if value is not None or (
            canonical_key == "origination_date" and t24_field in record.fields
        ):
            data[canonical_key] = value
    for canonical_key, t24_lcy in entry.lcy_fields.items():
        value = record.scalar(t24_lcy)
        if value is not None:
            data[canonical_key] = value
    for canonical_key, constant in entry.constants.items():
        data[canonical_key] = constant
    return RawRecord(
        entity_type=entry.entity_type,  # type: ignore[arg-type]
        source_locator=source_locator,
        data=data,
        source_table=entry.domain.name,
    )


def _reference_record(entry: CatalogEntry, record: OfsRecord, source_locator: str) -> RawRecord:
    """A reference-domain row under its kind, with its ``field_map`` applied.

    The payload is the vendor's own keys, EXCEPT where the catalog names a
    canonical one. Applying ``field_map`` here was missing, and it stopped being
    cosmetic the moment a register began enforcing its own schema: T24 publishes
    branches as ``COMPANY.CODE`` / ``BRANCH`` / ``REGION``, the
    ``business_units`` register requires ``business_unit_id`` and
    ``business_unit_name``, and so every branch row a T24 bank pulled was refused
    and the dataset simply never appeared. Nothing raised — the pull succeeded and
    the register stayed empty, which meant no branch names in the BI branch
    dimension and a Branch Network dashboard with nothing in it.

    ``field_map`` is documented as "renames raw T24 fields to canonical output
    keys", which is exactly this, and the entity path has always done it. The raw
    vendor record is unchanged in the staged bundle, so lineage keeps the original
    spelling; what reaches the register is the canonical shape a register is
    entitled to expect from an ADAPTER. A pushed register is different and is
    still preserved verbatim: a bank that sends the documented spelling must not
    have it renamed at the boundary, which is why the alias seam resolves on READ.
    """
    data: dict[str, Any] = {name: record.scalar(name) for name in record.fields}
    for t24_field, canonical_key in entry.field_map.items():
        value = data.pop(t24_field, None)
        # The id field is often the record id rather than a separate assignment,
        # exactly as on the entity path.
        if value is None and t24_field == entry.id_field:
            value = record.record_id or None
        if value is not None:
            data[canonical_key] = value
    if entry.id_field and entry.id_field not in data and record.record_id:
        data[entry.id_field] = record.record_id
    return RawRecord(
        entity_type="reference",
        source_locator=source_locator,
        data=data,
        dataset_kind=entry.dataset_key,
        source_table=entry.domain.name,
    )


def extract_domain(
    entry: CatalogEntry,
    payload_records: list[Any],
    *,
    source_name: str,
) -> tuple[list[RawRecord], list[str]]:
    """Extract one domain's staged payloads into RawRecords.

    Returns the records plus any per-element warnings (a malformed element
    warns and is skipped — one bad record never fails the whole domain).
    """
    records: list[RawRecord] = []
    warnings: list[str] = []
    is_reference = entry.entity_type == "reference"
    counter = 0
    for element in payload_records:
        try:
            ofs_records = _as_ofs_records(element)
        except (BundleError, ValueError) as exc:
            warnings.append(f"{entry.domain.name}: skipped unparseable record ({exc})")
            continue
        for record in ofs_records:
            counter += 1
            locator = f"{source_name}#{entry.domain.name}!R{counter}"
            if is_reference:
                records.append(_reference_record(entry, record, locator))
            else:
                records.append(_entity_record(entry, record, locator))
    return records, warnings


def extract_bundle(
    bundle: dict[str, Any],
    catalog: Catalog,
    *,
    source_name: str,
) -> tuple[list[RawRecord], list[str], list[SourceTableSummary]]:
    """Extract every domain block in a staged bundle.

    ``bundle`` is the staged document ``{"mode", "as_of_date", "company",
    "domains": [{"domain", "source", "records"}]}``. Unknown or unsupported
    domains warn and are skipped rather than failing the batch.
    """
    domains = bundle.get("domains")
    if not isinstance(domains, list):
        raise BundleError("staged bundle 'domains' must be a list.")

    records: list[RawRecord] = []
    warnings: list[str] = []
    summaries: list[SourceTableSummary] = []
    for block in domains:
        if not isinstance(block, dict):
            raise BundleError("each staged bundle domain must be a mapping.")
        domain_name = str(block.get("domain", ""))
        try:
            domain = CoreBankingDomain[domain_name]
        except KeyError:
            warnings.append(f"unknown domain {domain_name!r} in staged bundle; skipped.")
            continue
        entry = catalog.entries.get(domain)
        if entry is None or not entry.supported:
            warnings.append(f"domain {domain_name!r} is not supported by the catalog; skipped.")
            continue
        payload_records = block.get("records", [])
        if not isinstance(payload_records, list):
            raise BundleError(f"domain {domain_name!r} 'records' must be a list.")
        domain_records, domain_warnings = extract_domain(
            entry, payload_records, source_name=source_name
        )
        records.extend(domain_records)
        warnings.extend(domain_warnings)
        summaries.append(SourceTableSummary(name=domain_name, row_count=len(domain_records)))
    return records, warnings, summaries
