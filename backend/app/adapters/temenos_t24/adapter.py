"""Temenos T24 source adapter.

T24 is an ingestion :class:`SourceAdapter`, not a live-query engine. The network
fetch lives in the transport layer (``transport.py`` / ``pull.py``): a pull
signs on, fetches each enabled domain, and stages the raw payloads as one JSON
bundle to the bank's temp tier. This adapter's ``extract`` then reads that
staged bundle OFFLINE and parses it into native records; ``translate`` maps
those to canonical record data via a generic, versioned :class:`MappingConfig`.

That "stage-then-ingest" split is deliberate: every T24 semantic (OFS marker
parsing, LCY selection, field renaming, position typing) is pure, offline, and
reproducible from the recorded bundle + mapping version, and the live
connection is confined to a swappable transport that fixtures replace wholesale.

Design constraint (data engine spec §9): the adapter never invents T24
endpoints, table structures, or authentication mechanisms beyond documented,
standard T24 vocabulary; installation-specific enquiry names live in the mode
catalog and are overridable per bank.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.adapters.temenos_t24.catalog import (
    Catalog,
    CatalogError,
    apply_overrides,
    load_mode_catalog,
)
from app.adapters.temenos_t24.extractors import BundleError, _as_ofs_records, extract_bundle
from app.adapters.temenos_t24.translate import translate as translate_records
from app.domain.ingestion.adapter import SourceAdapter, register_adapter
from app.domain.ingestion.contracts import (
    AdapterConfig,
    AdapterIdentity,
    CanonicalRecords,
    ConnectionStatus,
    EntityType,
    ExtractionResult,
    HealthStatus,
    MappingConfig,
    SourceColumn,
    SourceSchema,
    SourceTable,
    SourceTableSummary,
)

ADAPTER_NAME = "temenos_t24"
ADAPTER_VERSION = "1.0"

# Table names referenced in AequorOS product documentation (spec §9.2): the
# extraction surface the catalog draws its applications from.
KNOWN_TABLES: tuple[str, ...] = (
    "AA.ARRANGEMENT",
    "AA.ARRANGEMENT.ACTIVITY",
    "AA.PRODUCT.DESIGNER",
    "AA.INTEREST",
    "AA.PRODUCT",
    "ACCOUNT",
    "ACCOUNT.RESTRICTION",
    "SECURITY.POSITION",
    "SECURITY.MASTER",
    "MM.MONEY.MARKET",
    "LIMIT.REFERENCE",
    "LETTER.OF.CREDIT",
    "PAYMENT.STOP",
    "TELLER",
    "SWAP.AGREEMENT",
    "FOREX",
    "OPTIONS",
    "CUSTOMER",
    "COLLATERAL",
    "DEPT.ACCT.OFFICER",
    "GENERAL.LEDGER",
)


def _read_bundle(path: Path) -> dict[str, Any]:
    """Read the staged T24 bundle document ``{mode, as_of_date, company,
    domains: [...]}``."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(f"cannot read staged T24 bundle {path.name}: {exc}") from exc
    if not isinstance(document, dict):
        raise BundleError("staged T24 bundle must be a JSON object.")
    if "domains" not in document:
        raise BundleError("staged T24 bundle has no 'domains'.")
    return document


def _bundle_catalog(bundle: dict[str, Any]) -> Catalog:
    """Resolve the mode catalog for a bundle, applying its per-bank overrides."""
    mode = str(bundle.get("mode", "OFS"))
    catalog = load_mode_catalog(mode)
    overrides = bundle.get("catalog_overrides")
    if isinstance(overrides, dict) and overrides:
        catalog = apply_overrides(catalog, overrides)
    return catalog


class TemenosT24Adapter(SourceAdapter):
    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="T24")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        path = Path(config.location)
        if not path.is_file():
            return ConnectionStatus(ok=False, detail=f"Staged T24 bundle {path} does not exist.")
        try:
            bundle = _read_bundle(path)
            catalog = _bundle_catalog(bundle)
        except (BundleError, CatalogError) as exc:
            return ConnectionStatus(ok=False, detail=str(exc))
        domains = bundle.get("domains") or []
        if not isinstance(domains, list) or not domains:
            return ConnectionStatus(ok=False, detail="Staged T24 bundle contains no domains.")
        _ = catalog
        return ConnectionStatus(ok=True)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        bundle = _read_bundle(Path(config.location))
        tables: list[SourceTable] = []
        for block in bundle.get("domains", []):
            if not isinstance(block, dict):
                continue
            name = str(block.get("domain", ""))
            columns: dict[str, list[str]] = {}
            for element in block.get("records", []):
                try:
                    ofs_records = _as_ofs_records(element)
                except (BundleError, ValueError):
                    continue
                for record in ofs_records:
                    for column in record.fields:
                        samples = columns.setdefault(column, [])
                        value = record.scalar(column)
                        if value is not None and len(samples) < 3:
                            samples.append(value)
            tables.append(
                SourceTable(
                    name=name,
                    columns=tuple(
                        SourceColumn(name=column, sample_values=tuple(samples))
                        for column, samples in columns.items()
                    )
                    or (SourceColumn(name="@ID"),),
                    row_count=len(block.get("records", [])),
                )
            )
        return SourceSchema(tables=tuple(tables))

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        path = Path(config.location)
        content = path.read_bytes()
        bundle = _read_bundle(path)
        catalog = _bundle_catalog(bundle)
        source_name = path.name
        records, warnings, summaries = extract_bundle(bundle, catalog, source_name=source_name)

        wanted = set(entity_types)
        kept = [
            record
            for record in records
            if record.entity_type == "reference" or record.entity_type in wanted
        ]
        if len(kept) != len(records):
            summaries = _resummarize(kept)

        return ExtractionResult(
            identity=self.identify(),
            as_of_date=as_of_date,
            extraction_mode="full",
            content_hash=hashlib.sha256(content).hexdigest(),
            records=kept,
            warnings=warnings,
            source_tables=summaries,
        )

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        return translate_records(raw_records, mapping_config)

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


def _resummarize(records: list[Any]) -> list[SourceTableSummary]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.source_table or "?"] = counts.get(record.source_table or "?", 0) + 1
    return [SourceTableSummary(name=name, row_count=count) for name, count in counts.items()]


register_adapter("T24", TemenosT24Adapter)
