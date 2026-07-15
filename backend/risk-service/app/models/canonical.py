"""Data Engine canonical model: source-agnostic institution balance-sheet state.

Only adapters know source systems; everything downstream of ingestion reads
these tables. The model is a generalized regulated-financial-institution
balance sheet; ``banks`` is its first specialization, so canonical records are
scoped by ``organization_id`` (tenant, RLS boundary) plus ``bank_id`` (the
reporting entity). When non-bank institution categories arrive, ``banks``
generalizes; the canonical tables do not change shape.

Immutability: accepted records are never updated in place. A correction or a
re-ingestion for the same ``as_of_date`` writes new rows and stamps the old
ones' ``superseded_by``. Partial unique indexes enforce natural-key uniqueness
among the *current* (non-superseded) generation only.

Monetary amounts are ``NUMERIC(28, 6)`` (report at 2 dp, calculate at 6).
Rates are decimals, never percentages: ``0.245``, not ``24.5``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now
from app.domain.ingestion.constants import (
    COUNTERPARTY_TYPES,
    GL_ACCOUNT_CLASSES,
    POSITION_TYPES,
    RATE_TYPES,
    SOURCE_SYSTEMS,
    VALIDATION_STATUSES,
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class CanonicalMetadataMixin(UuidV7PrimaryKeyMixin, TimestampMixin):
    """Mandatory provenance metadata carried by every canonical entity.

    No canonical record exists without provenance: ``lineage_id`` and
    ``ingestion_batch_id`` are non-nullable by design, including for manual
    entry, which runs through the same batch machinery as any adapter.
    """

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    # The source system's own identifier for this record (T24 arrangement id,
    # workbook row locator, ...). Uniqueness is enforced per concrete entity.
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    ingestion_batch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    lineage_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    superseded_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


def canonical_constraints(table_name: str) -> tuple:
    """Constraints and indexes every canonical entity shares.

    Concrete classes splice these into ``__table_args__`` after their own
    entity-specific constraints.
    """
    return (
        CheckConstraint(
            f"validation_status IN ({_values(VALIDATION_STATUSES)})",
            name=f"ck_{table_name}_validation_status",
        ),
        CheckConstraint(
            f"source_system IN ({_values(SOURCE_SYSTEMS)})",
            name=f"ck_{table_name}_source_system",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["ingestion_batch_id", "organization_id"],
            ["ingestion_batches.id", "ingestion_batches.organization_id"],
        ),
        ForeignKeyConstraint(
            ["lineage_id", "organization_id"],
            ["lineage_records.id", "lineage_records.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name=f"uq_{table_name}_id_org"),
        Index(f"ix_{table_name}_org_bank_as_of", "organization_id", "bank_id", "as_of_date"),
        Index(f"ix_{table_name}_org_batch", "organization_id", "ingestion_batch_id"),
    )


def _current_generation_unique(table_name: str, *columns: str) -> Index:
    """Natural-key uniqueness among non-superseded rows only."""
    return Index(
        f"uq_{table_name}_current",
        *columns,
        unique=True,
        postgresql_where=sql_text("superseded_by IS NULL"),
        sqlite_where=sql_text("superseded_by IS NULL"),
    )


class CanonicalGlAccount(CanonicalMetadataMixin, Base):
    """A general-ledger account with arbitrary-depth hierarchy."""

    __tablename__ = "canonical_gl_accounts"
    __table_args__ = (
        CheckConstraint(
            f"account_class IN ({_values(GL_ACCOUNT_CLASSES)})",
            name="ck_canonical_gl_accounts_account_class",
        ),
        ForeignKeyConstraint(
            ["parent_account_id", "organization_id"],
            ["canonical_gl_accounts.id", "canonical_gl_accounts.organization_id"],
        ),
        _current_generation_unique(
            "canonical_gl_accounts", "organization_id", "bank_id", "account_code", "as_of_date"
        ),
        *canonical_constraints("canonical_gl_accounts"),
    )

    account_code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_class: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class CanonicalCounterparty(CanonicalMetadataMixin, Base):
    """The entity on the other side of any position.

    ``external_identifiers`` carries jurisdiction-specific ids (GhanaCard,
    BIC, registrar numbers) without schema churn.
    """

    __tablename__ = "canonical_counterparties"
    __table_args__ = (
        CheckConstraint(
            f"counterparty_type IN ({_values(COUNTERPARTY_TYPES)})",
            name="ck_canonical_counterparties_counterparty_type",
        ),
        _current_generation_unique(
            "canonical_counterparties",
            "organization_id",
            "bank_id",
            "source_system",
            "source_reference",
            "as_of_date",
        ),
        *canonical_constraints("canonical_counterparties"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    counterparty_type: Mapped[str] = mapped_column(String(32), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    rating: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rating_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    group_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_identifiers: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class CanonicalProduct(CanonicalMetadataMixin, Base):
    """A product definition with its regulatory-category mapping.

    ``regulatory_category`` is nullable because it is an enrichment output
    (product-to-regulatory mapping), not raw source data; validation flags
    positions whose product lacks one before calculations run.
    """

    __tablename__ = "canonical_products"
    __table_args__ = (
        _current_generation_unique(
            "canonical_products", "organization_id", "bank_id", "product_code", "as_of_date"
        ),
        *canonical_constraints("canonical_products"),
    )

    product_code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    regulatory_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    risk_weight_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class CanonicalPosition(CanonicalMetadataMixin, Base):
    """Stable identity for anything on or off the balance sheet.

    Identity holds what never changes about a position (its type, currency,
    and source identity); everything time-varying lives on the snapshot.
    ``as_of_date`` here is the business date the position was first observed.
    """

    __tablename__ = "canonical_positions"
    __table_args__ = (
        CheckConstraint(
            f"position_type IN ({_values(POSITION_TYPES)})",
            name="ck_canonical_positions_position_type",
        ),
        _current_generation_unique(
            "canonical_positions",
            "organization_id",
            "bank_id",
            "source_system",
            "source_reference",
        ),
        *canonical_constraints("canonical_positions"),
    )

    position_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    origination_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class CanonicalPositionSnapshot(CanonicalMetadataMixin, Base):
    """The immutable state of a position as of a business date.

    All calculations read snapshots, never live positions. Restatements write
    a new snapshot for the same ``as_of_date`` and supersede the old one, so
    any historical report remains reproducible exactly as filed.
    """

    __tablename__ = "canonical_position_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"rate_type IN ({_values(RATE_TYPES)}) OR rate_type IS NULL",
            name="ck_canonical_position_snapshots_rate_type",
        ),
        CheckConstraint(
            "ifrs9_stage IN (1, 2, 3) OR ifrs9_stage IS NULL",
            name="ck_canonical_position_snapshots_ifrs9_stage",
        ),
        ForeignKeyConstraint(
            ["position_id", "organization_id"],
            ["canonical_positions.id", "canonical_positions.organization_id"],
        ),
        ForeignKeyConstraint(
            ["counterparty_id", "organization_id"],
            ["canonical_counterparties.id", "canonical_counterparties.organization_id"],
        ),
        ForeignKeyConstraint(
            ["product_id", "organization_id"],
            ["canonical_products.id", "canonical_products.organization_id"],
        ),
        ForeignKeyConstraint(
            ["gl_account_id", "organization_id"],
            ["canonical_gl_accounts.id", "canonical_gl_accounts.organization_id"],
        ),
        _current_generation_unique(
            "canonical_position_snapshots", "organization_id", "position_id", "as_of_date"
        ),
        Index(
            "ix_canonical_position_snapshots_org_position",
            "organization_id",
            "position_id",
        ),
        *canonical_constraints("canonical_position_snapshots"),
    )

    position_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    counterparty_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    product_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    gl_account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(28, 6), nullable=False)
    notional: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    rate_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rate_index: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rate_spread: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    contractual_maturity: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_repricing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ifrs9_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Behavioral overlays are enrichment outputs, nullable on raw ingestion;
    # provenance for each enriched field lives in enrichment_provenance.
    behavioral_maturity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enrichment_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
