"""API schemas for Data Engine ingestion: mapping configs, batches, lineage."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.ingestion.constants import (
    BatchStatus,
    ExtractionMode,
    MappingConfigStatus,
    SourceSystem,
    ValidationStatus,
)
from app.domain.ingestion.contracts import MappingConfig


class MappingConfigCreate(BaseModel):
    source_system: SourceSystem
    name: str = Field(min_length=1, max_length=255)
    config: MappingConfig
    activate: bool = False
    reason: str = Field(min_length=1)


class MappingConfigRead(BaseModel):
    id: UUID
    bank_id: UUID
    source_system: SourceSystem
    version: int
    status: MappingConfigStatus
    name: str
    config: dict[str, Any]
    created_at: datetime


class MappingConfigListRead(BaseModel):
    bank_id: UUID
    configs: list[MappingConfigRead]


class IngestionUploadRead(BaseModel):
    """A source file staged in the bank's temp tier, ready to ingest."""

    object_path: str
    filename: str
    byte_size: int
    checksum_sha256: str
    # Pass this verbatim as IngestionBatchCreate.location.
    location: str


class IngestionBatchCreate(BaseModel):
    source_system: SourceSystem
    as_of_date: date
    # Source location: a server file path, or "temp://{object_path}" for a
    # file previously staged via the upload endpoint.
    location: str = Field(min_length=1)
    mapping_config_id: UUID | None = None
    adapter_options: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)


class IngestionBatchRead(BaseModel):
    id: UUID
    bank_id: UUID
    source_system: SourceSystem
    adapter_version: str
    extraction_mode: ExtractionMode
    status: BatchStatus
    as_of_date: date
    content_hash: str | None
    mapping_config_id: UUID | None
    records_extracted: int
    records_translated: int
    records_accepted: int
    records_warning: int
    records_error: int
    records_blocked: int
    validation_report: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    raw_artifact_path: str | None
    report_artifact_path: str | None
    created_at: datetime


class IngestionBatchStartRead(BaseModel):
    batch: IngestionBatchRead
    # True when an identical source for the same business date was already
    # accepted and the existing batch is returned instead of a duplicate.
    reused: bool


class IngestionBatchListRead(BaseModel):
    bank_id: UUID
    batches: list[IngestionBatchRead]


class TranslationFailureRead(BaseModel):
    id: UUID
    entity_type: str
    source_locator: str
    raw_record: dict[str, Any]
    error_code: str
    error_message: str


class TranslationFailureListRead(BaseModel):
    batch_id: UUID
    failures: list[TranslationFailureRead]


class CanonicalPositionRead(BaseModel):
    id: UUID
    source_system: SourceSystem
    source_reference: str
    position_type: str
    currency: str
    validation_status: ValidationStatus
    as_of_date: date
    snapshot_id: UUID | None
    balance: Decimal | None
    interest_rate: Decimal | None
    rate_type: str | None
    contractual_maturity: date | None
    lineage_id: UUID


class CanonicalPositionListRead(BaseModel):
    bank_id: UUID
    as_of_date: date | None
    positions: list[CanonicalPositionRead]


OverridableSnapshotField = Literal[
    "balance", "interest_rate", "ifrs9_stage", "behavioral_maturity_months"
]


class PositionSnapshotOverrideCreate(BaseModel):
    field: OverridableSnapshotField
    value: str | int | float | None
    reason: str = Field(min_length=1)


class PositionSnapshotRead(BaseModel):
    id: UUID
    position_id: UUID
    as_of_date: date
    validation_status: ValidationStatus
    balance: Decimal
    interest_rate: Decimal | None
    ifrs9_stage: int | None
    behavioral_maturity_months: int | None
    enrichment_provenance: dict[str, Any]
    lineage_id: UUID
    superseded_snapshot_id: UUID | None = None


class LineageNodeRead(BaseModel):
    id: UUID
    operation_type: str
    operation_ref: str
    ingestion_batch_id: UUID | None
    input_lineage_ids: list[UUID]
    details: dict[str, Any]
    occurred_at: datetime


class LineageWalkRead(BaseModel):
    """The transformation chain for one lineage node, newest first."""

    nodes: list[LineageNodeRead]
