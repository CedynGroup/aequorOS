"""Data Engine ingestion machinery: batches, lineage, mapping configs, failures.

Canonical entities live in ``app.models.canonical``; this module owns the
records that describe *how* canonical data arrived: one row per ingestion
attempt, the per-institution mapping configuration that drove translation,
the lineage graph every canonical record points into, and raw source records
that could not be translated.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    TimestampMixin,
    UuidV4PrimaryKeyMixin,
    UuidV7PrimaryKeyMixin,
    utc_now,
)
from app.domain.ingestion.constants import (
    BATCH_STATUSES,
    EXTRACTION_MODES,
    LINEAGE_OPERATION_TYPES,
    MAPPING_CONFIG_STATUSES,
    SOURCE_SYSTEMS,
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class IngestionBatch(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One ingestion attempt for a bank as of a business date.

    Batches are the idempotency and audit boundary: re-running the same source
    content for the same business date is detected via ``content_hash`` and
    returns the previously accepted batch instead of duplicating canonical
    state. Terminal batches are immutable history, including failures.
    """

    __tablename__ = "ingestion_batches"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(BATCH_STATUSES)})",
            name="ck_ingestion_batches_status",
        ),
        CheckConstraint(
            f"source_system IN ({_values(SOURCE_SYSTEMS)})",
            name="ck_ingestion_batches_source_system",
        ),
        CheckConstraint(
            f"extraction_mode IN ({_values(EXTRACTION_MODES)})",
            name="ck_ingestion_batches_extraction_mode",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_ingestion_batches_id_org"),
        Index(
            "ix_ingestion_batches_org_bank_as_of",
            "organization_id",
            "bank_id",
            "as_of_date",
        ),
        Index("ix_ingestion_batches_org_content_hash", "organization_id", "content_hash"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    extraction_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    # SHA-256 of the source content; null for manual entry, which has no file.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stored_object_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("stored_objects.id"), nullable=True
    )
    mapping_config_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("mapping_configs.id"), nullable=True
    )
    records_extracted: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    records_translated: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    records_accepted: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    records_warning: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    records_error: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    records_blocked: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    validation_report: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Object paths in the institution's storage buckets (storage.md §1.3):
    # the raw source file in the `raw` tier and the validation report in
    # `outputs`. Null for batches that failed before the artifact existed.
    raw_artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    report_artifact_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class LineageRecord(UuidV7PrimaryKeyMixin, Base):
    """One node in the transformation graph from source to canonical state.

    ``input_lineage_ids`` holds the parent node ids, forming an append-only
    DAG: extract -> translate -> validate -> enrich -> override. Every
    canonical record's ``lineage_id`` points at the newest node that produced
    its current values, and the graph is walked backwards from there.
    """

    __tablename__ = "lineage_records"
    __table_args__ = (
        CheckConstraint(
            f"operation_type IN ({_values(LINEAGE_OPERATION_TYPES)})",
            name="ck_lineage_records_operation_type",
        ),
        ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_lineage_records_id_org"),
        Index("ix_lineage_records_org_batch", "organization_id", "ingestion_batch_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_batch_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # Human-readable operation identity, e.g. "excel_csv_v1.0/positions".
    operation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    input_lineage_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class MappingConfigRecord(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Versioned per-institution source-to-canonical mapping configuration.

    The config payload is the onboarding deliverable: field mappings, enum
    mappings, and product mappings for one source system at one bank. Versions
    are monotonic per (bank, source system); at most one version is active.
    """

    __tablename__ = "mapping_configs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(MAPPING_CONFIG_STATUSES)})",
            name="ck_mapping_configs_status",
        ),
        CheckConstraint(
            f"source_system IN ({_values(SOURCE_SYSTEMS)})",
            name="ck_mapping_configs_source_system",
        ),
        CheckConstraint("version >= 1", name="ck_mapping_configs_version_positive"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_mapping_configs_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "source_system",
            "version",
            name="uq_mapping_configs_scope_version",
        ),
        Index(
            "uq_mapping_configs_single_active",
            "organization_id",
            "bank_id",
            "source_system",
            unique=True,
            postgresql_where=sql_text("status = 'active'"),
            sqlite_where=sql_text("status = 'active'"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class TranslationFailure(UuidV7PrimaryKeyMixin, Base):
    """A raw source record that could not be translated to canonical form.

    The raw record is preserved verbatim so onboarding can refine the mapping
    configuration against real failures instead of re-requesting files.
    """

    __tablename__ = "translation_failures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_translation_failures_org_batch", "organization_id", "ingestion_batch_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Where in the source the record came from, e.g. "positions.xlsx#Sheet1!R14".
    source_locator: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_record: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str] = mapped_column(String(120), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
