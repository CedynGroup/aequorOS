"""Temenos T24 adapter operational state: one core-banking connection per bank.

Canonical T24-sourced records (positions, GL, counterparties, ...) live in
``app.models.canonical`` with full ingestion provenance; this module owns the
operational record describing *how* the core connection is configured — its
transport mode, endpoint, enabled domains, pull schedule, and encrypted service
credentials. It is an operational table, not a canonical record, so it carries
no ingestion-provenance metadata.

Credentials are never stored in plaintext: ``credential_ciphertext`` is an
opaque AES-256-GCM blob and ``vault_path`` is the logical locator
(``vault://institutions/{bank}/core_credentials/{mode}/default``). Mirrors
``MarketDataConnection`` and reuses the same credential crypto.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin
from app.domain.ingestion.constants import (
    TEMENOS_CONNECTION_MODES,
    TEMENOS_CONNECTION_STATUSES,
    TEMENOS_CORE_SYSTEMS,
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class TemenosConnection(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One configured Temenos core-banking connection for a bank.

    ``status`` tracks the credential lifecycle (mirrors the market-data state
    machine): connections start in TESTING and move through ACTIVE /
    EXPIRING_SOON / EXPIRED / REVOKED / INVALID / REPLACED_PENDING_DELETION /
    DISABLED. Every transition is audited by the connection service.
    """

    __tablename__ = "temenos_connections"
    __table_args__ = (
        CheckConstraint(
            f"core_system IN ({_values(TEMENOS_CORE_SYSTEMS)})",
            name="ck_temenos_connections_core_system",
        ),
        CheckConstraint(
            f"connection_mode IN ({_values(TEMENOS_CONNECTION_MODES)})",
            name="ck_temenos_connections_mode",
        ),
        CheckConstraint(
            f"status IN ({_values(TEMENOS_CONNECTION_STATUSES)})",
            name="ck_temenos_connections_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_temenos_connections_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "display_name",
            name="uq_temenos_connections_scope_name",
        ),
        Index("ix_temenos_connections_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    core_system: Mapped[str] = mapped_column(
        String(20), default="T24", server_default=sql_text("'T24'"), nullable=False
    )
    connection_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="TESTING", server_default=sql_text("'TESTING'"), nullable=False
    )
    # Encrypted opaque credential blob (OFS service user password / API keys /
    # client secrets). NULL after revocation.
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Logical credential locator,
    # vault://institutions/{bank}/core_credentials/{mode}/default
    vault_path: Mapped[str] = mapped_column(String(255), nullable=False)
    # T24 companies/entities to pull (multi-company banks); first is the default.
    companies: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    default_currency: Mapped[str] = mapped_column(
        String(3), default="GHS", server_default=sql_text("'GHS'"), nullable=False
    )
    # CoreBankingDomain names this connection is authorized/enabled to pull.
    domains: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    # Domain-category -> PullCadence, e.g. {"POSITIONS": "END_OF_DAY"}.
    schedule: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # Per-bank catalog overrides (real enquiry names / endpoints / field maps),
    # merged over the shipped mode catalog at pull time.
    catalog_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    credential_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pull_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
