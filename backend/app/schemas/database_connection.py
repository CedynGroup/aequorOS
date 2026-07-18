"""Schemas for Database-Direct core-database connection management.

Credentials are WRITE-ONLY at this API: request bodies may carry a credential
dict (a read-only service account ``username``/``password`` plus optional
``extra`` secret material such as an Oracle wallet password), but no response
model ever contains credential material — only the lifecycle status, the SHA-256
fingerprint, and the expiry timestamp. Mirrors the Temenos / market-data
connection schemas.

The ``connection_options`` and ``extraction_spec`` blocks are validated against
the adapter's own typed models (:class:`ConnectionConfig` / :class:`ExtractionSpec`)
by the service before persistence, so a malformed onboarding payload is rejected
with a bank-safe 400 rather than surfacing at pull time.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type Backend = Literal["oracle", "sqlserver", "jdbc", "odbc"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatabaseConnectionCreate(ClosedModel):
    """Onboard one direct core-database connection.

    ``credentials`` is required (``username`` plus, for password auth,
    ``password``; ``extra`` carries backend-specific secret material). TLS is
    enabled by default; disabling it is only honored for an endpoint that
    terminates TLS out of band. ``extraction_spec`` and ``connection_options``
    are the serialized adapter config blocks; both are validated on create.
    """

    backend: Backend
    display_name: str = Field(min_length=1, max_length=120)
    host: str = Field(default="", max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(default="", max_length=255)
    service_name: str | None = Field(default=None, max_length=255)
    schemas: list[str] = Field(default_factory=list, title="Connection Schemas")
    read_replicas: list[str] = Field(default_factory=list, title="Connection Read Replicas")
    prefer_read_replica: bool = True
    tls_enabled: bool = True
    tls_verify_server_certificate: bool = True
    query_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    connection_options: dict[str, Any] = Field(
        default_factory=dict, title="Connection Options"
    )
    extraction_spec: dict[str, Any] = Field(
        default_factory=dict, title="Connection Extraction Spec"
    )
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )


class DatabaseConnectionUpdate(ClosedModel):
    """Post-onboarding management and credential rotation.

    When ``credentials`` is present the new set is validated FIRST; only on
    success are the stored ciphertext, fingerprint, and expiry swapped in one
    transaction. On failure nothing changes and the error is returned as a 422.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=255)
    service_name: str | None = Field(default=None, max_length=255)
    schemas: list[str] | None = Field(default=None, title="Connection Schemas Update")
    read_replicas: list[str] | None = Field(default=None, title="Connection Read Replicas Update")
    prefer_read_replica: bool | None = None
    tls_enabled: bool | None = None
    tls_verify_server_certificate: bool | None = None
    query_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    connection_options: dict[str, Any] | None = Field(
        default=None, title="Connection Options Update"
    )
    extraction_spec: dict[str, Any] | None = Field(
        default=None, title="Connection Extraction Spec Update"
    )
    credentials: dict[str, Any] | None = Field(default=None, title="Connection Credentials")
    credential_expires_at: datetime | None = Field(
        default=None, title="Connection Credential Expires At"
    )


class DatabaseConnectionRead(ClosedModel):
    """One connection's bank-facing view. Never carries credential values —
    only status, fingerprint, and expiry."""

    id: UUID
    backend: Backend
    display_name: str
    status: str
    host: str
    port: int | None
    database: str
    service_name: str | None
    schemas: list[str]
    read_replicas: list[str]
    prefer_read_replica: bool
    tls_enabled: bool
    tls_verify_server_certificate: bool
    query_timeout_seconds: int
    connection_options: dict[str, Any] = Field(title="Connection Options")
    extraction_spec: dict[str, Any] = Field(title="Connection Extraction Spec")
    credential_fingerprint: str | None = Field(title="Connection Credential Fingerprint")
    credential_expires_at: datetime | None = Field(title="Connection Credential Expires At")
    last_validated_at: datetime | None = Field(title="Connection Last Validated At")
    last_synced_at: datetime | None = Field(title="Connection Last Synced At")
    last_sync_status: str | None = Field(title="Connection Last Sync Status")
    created_at: datetime
    # Bank-facing message from the most recent inline credential validation:
    # populated when a create/rotate check fails, never raw driver text.
    validation_error: str | None = Field(default=None, title="Connection Validation Error")


class DatabaseConnectionListRead(ClosedModel):
    connections: list[DatabaseConnectionRead]
    total: int


class DatabaseConnectionTestResult(ClosedModel):
    """Result of a live connection test: whether the core was reachable, the
    round-trip latency, and a bank-safe classified error otherwise. The error is
    always a pre-authored, bank-facing message — never a raw driver exception."""

    reachable: bool
    latency_ms: int | None = Field(default=None, title="Test Latency Ms")
    tables_pulled: int = Field(default=0, title="Test Tables Pulled")
    rows_pulled: int = Field(default=0, title="Test Rows Pulled")
    error_code: str | None = Field(default=None, title="Test Error Code")
    error: str | None = Field(default=None, title="Test Error")


class DiscoveredColumn(ClosedModel):
    name: str
    sample_values: list[str] = Field(default_factory=list, title="Discovered Column Samples")


class DiscoveredTable(ClosedModel):
    name: str
    row_count: int | None = Field(default=None, title="Discovered Table Row Count")
    columns: list[DiscoveredColumn]


class DatabaseConnectionDiscoverResult(ClosedModel):
    """The source schema discovered for mapping: tables with their columns and a
    few sample values per column, drawn from a live introspection pull."""

    tables: list[DiscoveredTable]


class DatabaseConnectionSyncRequest(ClosedModel):
    """On-demand sync for a single as-of date (defaults to today)."""

    as_of_date: date | None = Field(default=None, title="Sync As Of Date")
    reason: str = Field(default="manual database-direct sync", min_length=1, max_length=500)


class DatabaseConnectionSyncResult(ClosedModel):
    """The ingestion batch a sync produced, plus its terminal status."""

    batch_id: UUID
    status: str
    reused: bool
    records_extracted: int
    records_accepted: int
    # The as-of the batch was ingested at. Equals the requested date unless the
    # source snapshot carried a different authoritative reporting date, in which
    # case that date was adopted and ``as_of_note`` explains the reconciliation.
    as_of_date: date
    as_of_note: str | None = None
