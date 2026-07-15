"""Typed contracts every source adapter produces and consumes.

These models are the boundary between Layer 1 (adapters) and everything
downstream. Adapters emit *canonical record data* — plain values keyed by
source references — never ORM objects: resolving references to database ids
and persisting rows is orchestration's job, which is what keeps adapters
ignorant of the analytical store.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.ingestion.constants import (
    CounterpartyType,
    ExtractionMode,
    GlAccountClass,
    PositionType,
    RateType,
    SourceSystem,
)

EntityType = Literal["gl_account", "counterparty", "product", "position"]
ENTITY_TYPES: tuple[EntityType, ...] = ("gl_account", "counterparty", "product", "position")


class AdapterIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    source_system: SourceSystem


class ConnectionStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    detail: str = ""


class HealthStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    healthy: bool
    detail: str = ""


class SourceColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    sample_values: tuple[str, ...] = ()


class SourceTable(BaseModel):
    """One tabular unit in the source: a sheet, a table, or a CSV file."""

    model_config = ConfigDict(frozen=True)

    name: str
    columns: tuple[SourceColumn, ...]
    row_count: int | None = None


class SourceSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: tuple[SourceTable, ...]


class AdapterConfig(BaseModel):
    """Connection-level configuration for one extraction.

    ``location`` is adapter-specific: a filesystem path or object-storage key
    for file adapters, a DSN reference for database adapters. Secrets never
    live here; adapters receive resolved handles, not credentials.
    """

    model_config = ConfigDict(frozen=True)

    location: str
    options: dict[str, Any] = Field(default_factory=dict)


class EntityMapping(BaseModel):
    """How one canonical entity is populated from one source table."""

    source_table: str
    # canonical field name -> source column header
    fields: dict[str, str]


class MappingConfig(BaseModel):
    """Per-institution source-to-canonical translation rules.

    This is the onboarding deliverable. It is stored versioned in
    ``mapping_configs`` and passed to ``SourceAdapter.translate`` verbatim, so
    every translation is reproducible from the config version recorded on the
    batch.
    """

    field_mappings: dict[EntityType, EntityMapping] = Field(default_factory=dict)
    # canonical field -> {source value -> canonical enum value}
    enum_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    # institution product code -> canonical regulatory category
    product_mappings: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class RawRecord(BaseModel):
    """One source record exactly as extracted, before any translation."""

    entity_type: EntityType
    # Where in the source this came from, e.g. "positions.xlsx#Loans!R14".
    source_locator: str
    data: dict[str, Any]


class ExtractionResult(BaseModel):
    identity: AdapterIdentity
    as_of_date: date
    extraction_mode: ExtractionMode
    # SHA-256 over the source content; drives batch idempotency.
    content_hash: str | None = None
    records: list[RawRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GlAccountData(BaseModel):
    source_reference: str
    source_locator: str
    account_code: str
    name: str
    account_class: GlAccountClass
    parent_account_code: str | None = None
    currency: str | None = None
    balance: Decimal | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CounterpartyData(BaseModel):
    source_reference: str
    source_locator: str
    name: str
    counterparty_type: CounterpartyType
    country_code: str | None = None
    rating: str | None = None
    rating_source: str | None = None
    group_reference: str | None = None
    external_identifiers: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProductData(BaseModel):
    source_reference: str
    source_locator: str
    product_code: str
    name: str
    regulatory_category: str | None = None
    risk_weight_code: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PositionData(BaseModel):
    """Identity and as-of measures for one position, in source-reference terms.

    ``counterparty_reference``, ``product_code``, and ``gl_account_code`` are
    references into the same batch (or previously ingested state); the
    orchestrator resolves them to canonical ids at persistence time.
    """

    source_reference: str
    source_locator: str
    position_type: PositionType
    currency: str
    balance: Decimal
    notional: Decimal | None = None
    counterparty_reference: str | None = None
    product_code: str | None = None
    gl_account_code: str | None = None
    origination_date: date | None = None
    contractual_maturity: date | None = None
    next_repricing_date: date | None = None
    interest_rate: Decimal | None = None
    rate_type: RateType | None = None
    rate_index: str | None = None
    rate_spread: Decimal | None = None
    ifrs9_stage: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TranslationFailureData(BaseModel):
    entity_type: EntityType
    source_locator: str
    raw_record: dict[str, Any]
    error_code: str
    error_message: str


class CanonicalRecords(BaseModel):
    """Everything one translation pass produced, not yet validated."""

    gl_accounts: list[GlAccountData] = Field(default_factory=list)
    counterparties: list[CounterpartyData] = Field(default_factory=list)
    products: list[ProductData] = Field(default_factory=list)
    positions: list[PositionData] = Field(default_factory=list)
    failures: list[TranslationFailureData] = Field(default_factory=list)

    @property
    def record_count(self) -> int:
        return (
            len(self.gl_accounts)
            + len(self.counterparties)
            + len(self.products)
            + len(self.positions)
        )
