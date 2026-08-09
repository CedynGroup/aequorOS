"""Market Data Adapter operational state: vendor connections, quota usage,
and per-bank overlays.

Canonical market data entities (curves, FX rates, indices, ratings) live in
``app.models.canonical``; this module owns the records that describe *how*
market data arrives: one configured vendor connection per bank (credential
lifecycle per market_data_adapter.md §10) and the per-month quota ledger the
pull framework enforces (§11). These are operational tables, not canonical
records — they carry no ingestion provenance metadata.

``market_data_overlays`` is the tenant-side half of the two-layer market
data architecture (AequorOS_Market_Data_and_Curve_Platform.md §2, §9): a
bank's private, effective-dated spread adjustments composed onto the shared
golden copy at read time. Overlays are never written back to golden data
and never visible to another tenant (RLS-FORCED, migration 202608090044).

Credentials are never stored in plaintext: ``credential_ciphertext`` is an
opaque encrypted blob and ``vault_path`` is the logical Vault locator
(``vault://institutions/{bank}/vendor_credentials/{vendor}/default``).
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

# Overlay vocabulary (spec §9): what golden object the adjustment attaches
# to, how it adjusts, and which FTP-style component it decomposes into.
OVERLAY_BASE_REF_KINDS: tuple[str, ...] = ("curve", "fx", "index")
OVERLAY_ADJUSTMENT_TYPES: tuple[str, ...] = ("additive_bps", "fixed", "multiplicative")
OVERLAY_COMPONENT_TAGS: tuple[str, ...] = (
    "liquidity_premium",
    "term_liquidity_premium",
    "funding_spread",
    "credit_spread",
    "other",
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

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
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


class MarketDataOverlay(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One effective-dated, component-tagged adjustment on a golden object.

    Spec §9: default to additive basis-point spreads per tenor (``value`` in
    bps); ``fixed`` (value in rate decimal-fraction units) and
    ``multiplicative`` (value a factor) are secondary. ``tenor_months`` NULL
    means flat — applies to every tenor of the referenced curve. Rows are
    APPEND-ONLY: an edit creates a new row and stamps the old row's
    ``superseded_by``; ending an overlay sets ``effective_to``. Composition
    happens at read time in the views feature — golden data is never touched.
    """

    __tablename__ = "market_data_overlays"
    __table_args__ = (
        CheckConstraint(
            f"base_ref_kind IN ({_values(OVERLAY_BASE_REF_KINDS)})",
            name="ck_market_data_overlays_base_ref_kind",
        ),
        CheckConstraint(
            f"adjustment_type IN ({_values(OVERLAY_ADJUSTMENT_TYPES)})",
            name="ck_market_data_overlays_adjustment_type",
        ),
        CheckConstraint(
            f"component_tag IN ({_values(OVERLAY_COMPONENT_TAGS)})",
            name="ck_market_data_overlays_component_tag",
        ),
        # A curve overlay must name its curve; other kinds must not.
        CheckConstraint(
            "(base_ref_kind = 'curve') = (base_curve_name IS NOT NULL)",
            name="ck_market_data_overlays_curve_name",
        ),
        CheckConstraint(
            "tenor_months IS NULL OR tenor_months > 0",
            name="ck_market_data_overlays_tenor_months",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_market_data_overlays_effective_window",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_market_data_overlays_id_org"),
        Index(
            "ix_market_data_overlays_scope",
            "organization_id",
            "bank_id",
            "base_ref_kind",
            "base_curve_name",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    base_ref_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # Golden curve name the adjustment attaches to (e.g. AEQ.GHS.SOV.ZERO);
    # NULL for fx/index kinds.
    base_curve_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # NULL = flat spread across all tenors; set = applies to that tenor only.
    tenor_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjustment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # additive_bps: basis points; fixed: rate decimal fraction; multiplicative: factor.
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    component_tag: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Set when a newer version replaces this row (append-only edits).
    superseded_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


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

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
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
