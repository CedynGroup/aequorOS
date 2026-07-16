"""Market Data Adapter operational state: vendor connections and quota usage.

Canonical market data entities (curves, FX rates, indices, ratings) live in
``app.models.canonical``; this module owns the records that describe *how*
market data arrives: one configured vendor connection per bank (credential
lifecycle per market_data_adapter.md §10) and the per-month quota ledger the
pull framework enforces (§11). These are operational tables, not canonical
records — they carry no ingestion provenance metadata.

Credentials are never stored in plaintext: ``credential_ciphertext`` is an
opaque encrypted blob and ``vault_path`` is the logical Vault locator
(``vault://institutions/{bank}/vendor_credentials/{vendor}/default``).
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
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV7PrimaryKeyMixin
from app.domain.ingestion.constants import (
    MARKET_DATA_CONNECTION_STATUSES,
    MARKET_DATA_VENDORS,
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class MarketDataConnection(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One configured market-data vendor connection for a bank.

    ``status`` tracks the credential lifecycle (market_data_adapter.md §10.2):
    connections start in TESTING and move through ACTIVE / EXPIRING_SOON /
    EXPIRED / REVOKED / INVALID / REPLACED_PENDING_DELETION / DISABLED. Every
    transition is audited by the credential manager, not this table.
    """

    __tablename__ = "market_data_connections"
    __table_args__ = (
        CheckConstraint(
            f"vendor IN ({_values(MARKET_DATA_VENDORS)})",
            name="ck_market_data_connections_vendor",
        ),
        CheckConstraint(
            f"status IN ({_values(MARKET_DATA_CONNECTION_STATUSES)})",
            name="ck_market_data_connections_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_market_data_connections_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "vendor",
            "display_name",
            name="uq_market_data_connections_scope_name",
        ),
        Index("ix_market_data_connections_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    vendor: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="TESTING", server_default=sql_text("'TESTING'"), nullable=False
    )
    # Encrypted opaque credential blob; NULL for manual_upload connections,
    # which authenticate nothing.
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Logical Vault locator, e.g.
    # vault://institutions/{bank}/vendor_credentials/{vendor}/default
    vault_path: Mapped[str] = mapped_column(String(255), nullable=False)
    # DataScope strings this connection is authorized to pull.
    scopes: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    # Scope-category -> PullFrequency, e.g. {"YIELD_CURVE": "END_OF_DAY"}.
    schedule: Mapped[dict[str, Any]] = mapped_column(
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


class MarketDataQuotaUsage(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """Per-month vendor quota ledger for one bank (market_data_adapter.md §11.1).

    One row per (bank, vendor, month); the pull framework increments
    ``units_consumed`` and ``pull_count`` after every pull and enforces
    ``monthly_cap`` pre-flight via ``estimate_quota_cost``.
    """

    __tablename__ = "market_data_quota_usage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_market_data_quota_usage_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "vendor",
            "month",
            name="uq_market_data_quota_usage_scope_month",
        ),
        Index("ix_market_data_quota_usage_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    vendor: Mapped[str] = mapped_column(String(20), nullable=False)
    # Calendar month the ledger row covers, ISO "YYYY-MM".
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    units_consumed: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    monthly_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pull_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
