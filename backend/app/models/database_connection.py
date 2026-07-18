"""Database-Direct adapter operational state: one core-database connection per bank.

Canonical records pulled from a bank's core database (positions, GL, obligations,
...) live in ``app.models.canonical`` with full ingestion provenance; this module
owns the operational record describing *how* the direct pipe into that core is
configured — its backend (Oracle / SQL Server / JDBC / ODBC), endpoints, TLS and
read-replica policy, the per-bank :class:`~app.adapters.database_direct.config.
ExtractionSpec`, and the sealed read-only service credential. It is an operational
table, not a canonical record, so it carries no ingestion-provenance metadata.

Credentials are never stored in plaintext: ``credential_ciphertext`` is an opaque
AES-256-GCM blob (sealed through the adapter's credential vault) and ``vault_path``
is the logical locator (``vault://institutions/{bank}/db_direct/{backend}/default``).
Mirrors ``TemenosConnection``/``MarketDataConnection`` and reuses the same crypto.
Secrets are WRITE-ONLY: no response ever carries the ciphertext or the password —
only the status, the SHA-256 fingerprint, and the expiry cross that boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
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

# The four backends the database-direct driver abstraction serves (mirrors
# ``app.adapters.database_direct.config.BACKENDS``). Kept here as a local
# constant so this operational model does not depend on shared ingestion
# constants — the adapter package owns the authoritative literal.
DATABASE_DIRECT_BACKENDS: tuple[str, ...] = (
    "oracle",
    "sqlserver",
    "jdbc",
    "odbc",
    "snowflake",
)

# Credential lifecycle states, identical to the market-data / Temenos state
# machine: connections start in TESTING and move through ACTIVE / EXPIRING_SOON /
# EXPIRED / REVOKED / INVALID / REPLACED_PENDING_DELETION / DISABLED. Every
# transition is audited by the connection service, not this table.
DATABASE_DIRECT_CONNECTION_STATUSES: tuple[str, ...] = (
    "TESTING",
    "ACTIVE",
    "EXPIRING_SOON",
    "EXPIRED",
    "REVOKED",
    "INVALID",
    "REPLACED_PENDING_DELETION",
    "DISABLED",
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DatabaseDirectConnection(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One configured direct core-database connection for a bank.

    ``backend`` selects the driver family; ``host``/``port``/``database``/
    ``service_name``/``schemas`` and the JDBC/ODBC option block in
    ``connection_options`` describe *where* to reach the core, while
    ``extraction_spec`` (a serialized :class:`ExtractionSpec`) describes *what*
    to read. TLS is required by default and read replicas are preferred so a
    pull never touches the primary write path.
    """

    __tablename__ = "database_direct_connections"
    __table_args__ = (
        CheckConstraint(
            f"backend IN ({_values(DATABASE_DIRECT_BACKENDS)})",
            name="ck_database_direct_connections_backend",
        ),
        CheckConstraint(
            f"status IN ({_values(DATABASE_DIRECT_CONNECTION_STATUSES)})",
            name="ck_database_direct_connections_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_database_direct_connections_id_org"),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "display_name",
            name="uq_database_direct_connections_scope_name",
        ),
        Index("ix_database_direct_connections_org_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    backend: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="TESTING", server_default=sql_text("'TESTING'"), nullable=False
    )
    # --- Where and how to reach the core (secrets excluded) -----------------
    host: Mapped[str] = mapped_column(
        String(255), default="", server_default=sql_text("''"), nullable=False
    )
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Oracle service name / SQL Server & generic database catalog name.
    database: Mapped[str] = mapped_column(
        String(255), default="", server_default=sql_text("''"), nullable=False
    )
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Schemas to introspect / qualify tables with; empty = driver default.
    schemas: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    # Read-replica endpoints ("host" or "host:port"); always preferred over the
    # primary so a pull never issues a write to the source of record.
    read_replicas: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    prefer_read_replica: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("1"), nullable=False
    )
    tls_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("1"), nullable=False
    )
    tls_verify_server_certificate: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("1"), nullable=False
    )
    query_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=300, server_default=sql_text("300"), nullable=False
    )
    # Backend-specific connection block (JDBC driver_class/url_template/jar_paths,
    # ODBC driver_name/dsn, TLS ca_cert_path, ...), merged into the runtime
    # ConnectionConfig at pull time. Never carries secret material.
    connection_options: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # The per-bank ExtractionSpec (table -> canonical record kind, columns,
    # incremental cursor, equality filters), consumed by the query builder.
    extraction_spec: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # --- Sealed credential material (write-only across the API) -------------
    # Encrypted opaque credential blob (read-only service username/password plus
    # any wallet/extra secret material). NULL after revocation.
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Logical credential locator,
    # vault://institutions/{bank}/db_direct/{backend}/default
    vault_path: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
