"""Temenos T24 adapter — SKELETON ONLY, pending Temenos developer portal access.

Design constraint (data engine spec §9): do NOT invent T24 API endpoints,
table structures beyond what AequorOS product docs already reference, or
authentication mechanisms. Every TODO below names the portal document needed
to complete it, so the Temenos-approved engineer can finish the adapter
without re-discovering requirements.

Planned integration modes (per-bank configuration):
- Mode A: real-time API via TAFJ web services (REST/SOAP), incremental sync
- Mode B: post-COB batch file drop to SFTP, full refresh

Until portal access lands, the adapter reports itself unavailable through
``validate_connection`` (so an attempted T24 ingestion persists a failed
batch with an actionable message) and raises on extraction paths.
"""

from __future__ import annotations

from datetime import date

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
    SourceSchema,
)

ADAPTER_NAME = "temenos_t24"
ADAPTER_VERSION = "0.1-skeleton"

# Table names referenced in AequorOS product documentation (spec §9.2). These
# are the extraction surface the full adapter will cover; column-level detail
# requires the Temenos data dictionary from the developer portal.
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

_PENDING = (
    "T24 adapter is a skeleton pending Temenos Solution Provider approval and "
    "developer portal access; use the EXCEL_CSV adapter for T24 banks until then."
)


class TemenosT24Adapter(SourceAdapter):
    def identify(self) -> AdapterIdentity:
        return AdapterIdentity(name=ADAPTER_NAME, version=ADAPTER_VERSION, source_system="T24")

    def validate_connection(self, config: AdapterConfig) -> ConnectionStatus:
        # TODO(portal): implement TAFJ web-service connectivity check.
        # Needs: Temenos developer portal "TAFJ REST services" reference for
        # endpoint shape, auth mechanism, and health/ping semantics.
        return ConnectionStatus(ok=False, detail=_PENDING)

    def discover_schema(self, config: AdapterConfig) -> SourceSchema:
        # TODO(portal): return KNOWN_TABLES with real column metadata.
        # Needs: Temenos data dictionary for the tables in KNOWN_TABLES.
        raise NotImplementedError(_PENDING)

    def extract(
        self,
        config: AdapterConfig,
        as_of_date: date,
        entity_types: list[EntityType],
    ) -> ExtractionResult:
        # TODO(portal): Mode A — TAFJ API incremental extraction with
        # exponential backoff and dead-letter queue; Mode B — post-COB SFTP
        # batch file parsing (OFS message format).
        # Needs: TAFJ API reference; OFS message format specification;
        # COB extract file layouts.
        raise NotImplementedError(_PENDING)

    def translate(
        self,
        raw_records: ExtractionResult,
        mapping_config: MappingConfig,
    ) -> CanonicalRecords:
        # TODO(portal): AA.ARRANGEMENT -> position, CUSTOMER -> counterparty,
        # AA.PRODUCT -> product, GENERAL.LEDGER -> gl_account translations.
        # Needs: field-level semantics from the Temenos data dictionary.
        raise NotImplementedError(_PENDING)

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=False, detail=_PENDING)


register_adapter("T24", TemenosT24Adapter)
