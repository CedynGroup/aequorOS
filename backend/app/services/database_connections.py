"""Database-Direct core-database connection management.

Mirrors ``temenos_connections`` / ``market_data_connections``: onboarding,
credential lifecycle (create / rotate / disable / enable / revoke), a live
connection *test*, live schema *discovery* for mapping, and an on-demand *sync*
that stages a read-only pull and runs it through the existing ingestion spine
for source system ``DB_DIRECT``.

Credentials are write-only: request bodies may carry them, but the sealed
ciphertext never crosses a response boundary — only status, fingerprint, and
expiry do. Every mutation writes an audit event with a non-empty reason.

Two adapter config blocks travel with the connection: a
:class:`~app.adapters.database_direct.config.ConnectionConfig` (*where/how* to
reach the core, secrets excluded) and an
:class:`~app.adapters.database_direct.config.ExtractionSpec` (*what* to read).
Both are validated on create so a malformed onboarding payload is rejected up
front rather than at pull time. A live database is never required by the tests:
the driver is resolved through the module-level :func:`driver_for` seam, which
the offline fixture driver stands in for.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.adapters.database_direct.adapter import DatabaseDirectAdapter
from app.adapters.database_direct.config import (
    Backend,
    ConnectionConfig,
    ExtractionSpec,
)
from app.adapters.database_direct.credential_vault import (
    CredentialVaultError,
    DatabaseDirectCredentialVault,
    build_db_vault_path,
)
from app.adapters.database_direct.drivers import driver_for
from app.adapters.database_direct.drivers.base import (
    DatabaseDriver,
    DbCredentials,
    DriverSession,
    TableSchema,
)
from app.adapters.database_direct.errors import DatabaseDirectError
from app.adapters.database_direct.extraction import StagedBundle, StagedTableError
from app.adapters.database_direct.pull import stage_pull
from app.adapters.database_direct.query_builder import build_count_select, build_sample_select
from app.adapters.market_data.credential_manager import derive_status
from app.db.base import utc_now
from app.models import Bank
from app.models.database_connection import DatabaseDirectConnection
from app.schemas.database_connection import (
    DatabaseConnectionCreate,
    DatabaseConnectionDiscoverResult,
    DatabaseConnectionListRead,
    DatabaseConnectionRead,
    DatabaseConnectionSyncRequest,
    DatabaseConnectionSyncResult,
    DatabaseConnectionTestResult,
    DatabaseConnectionUpdate,
    DiscoveredColumn,
    DiscoveredTable,
)
from app.services.audit import record_event

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext
    from app.storage.client import StorageClient

# Classified adapter error codes that imply a credential/config problem, mapped
# to the lifecycle state a failed live test moves the connection into. Transient
# reachability codes (CORE_UNAVAILABLE, NETWORK_ERROR, ...) are diagnostic only
# and never mutate the stored status.
_STATUS_BY_ERROR_CODE: dict[str, str] = {
    "CREDENTIAL_INVALID": "INVALID",
    "CREDENTIAL_EXPIRED": "EXPIRED",
    "CREDENTIAL_REVOKED": "REVOKED",
    "CONFIGURATION_ERROR": "INVALID",
}

_adapter = DatabaseDirectAdapter()


# --- Reads -----------------------------------------------------------------


def list_connections(
    db: Session, ctx: TenantContext, bank_id: UUID
) -> DatabaseConnectionListRead:
    _get_bank_or_404(db, ctx, bank_id)
    rows = db.scalars(
        select(DatabaseDirectConnection)
        .where(
            DatabaseDirectConnection.organization_id == ctx.organization_id,
            DatabaseDirectConnection.bank_id == bank_id,
        )
        .order_by(DatabaseDirectConnection.created_at)
    ).all()
    return DatabaseConnectionListRead(
        connections=[_read_model(row) for row in rows], total=len(rows)
    )


def get_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> DatabaseConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    return _read_model(_get_connection_or_404(db, ctx, bank_id, connection_id))


# --- Lifecycle mutations ---------------------------------------------------


def create_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: DatabaseConnectionCreate
) -> DatabaseConnectionRead:
    """Onboard a connection. The adapter config blocks are validated for shape,
    credentials are sealed into the vault, and a successful credential-shape
    check activates the row; a failed one leaves it TESTING with a bank-facing
    error on the response."""
    _get_bank_or_404(db, ctx, bank_id)
    backend = payload.backend
    if not payload.credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credentials are required for a database-direct connection.",
        )
    # Validate both adapter config blocks up front (raises 400 on malformed).
    _validate_connection_config(payload_to_connection_dict(payload))
    _validate_extraction_spec(payload.extraction_spec)

    existing = db.scalar(
        select(DatabaseDirectConnection).where(
            DatabaseDirectConnection.organization_id == ctx.organization_id,
            DatabaseDirectConnection.bank_id == bank_id,
            DatabaseDirectConnection.display_name == payload.display_name,
        )
    )
    recreated = False
    if existing is not None:
        if existing.status != "REVOKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A connection named {payload.display_name!r} already exists for this bank.",
            )
        connection = existing
        recreated = True
        connection.status = "TESTING"
        connection.last_synced_at = None
        connection.last_sync_status = None
        connection.last_validated_at = None
    else:
        connection = DatabaseDirectConnection(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            backend=backend,
            display_name=payload.display_name,
            status="TESTING",
            vault_path=build_db_vault_path(bank_id, backend),
            created_by=ctx.actor_user_id,
        )
        db.add(connection)
    _apply_connection_fields(connection, payload)
    db.flush()

    _seal_credentials(connection, payload.credentials, payload.credential_expires_at)
    ok, error, _code = _check_credentials(payload.credentials)
    validation_error: str | None = None
    now = utc_now()
    if ok:
        connection.last_validated_at = now
        connection.status = derive_status(payload.credential_expires_at, True, now)
    else:
        validation_error = error  # stays TESTING

    record_event(
        db,
        ctx,
        event_type="database_direct_connection.created",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={
            "backend": connection.backend,
            "display_name": connection.display_name,
            "status": connection.status,
            "credential_fingerprint": connection.credential_fingerprint,
            "recreated": recreated,
        },
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def update_connection(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    payload: DatabaseConnectionUpdate,
) -> DatabaseConnectionRead:
    """Post-onboarding edits and credential rotation (validate new first)."""
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)

    changed: dict[str, Any] = {}
    _apply_update_fields(connection, payload, changed)
    if changed:
        # Re-validate the merged connection/extraction shape after edits.
        _validate_connection_config(_connection_to_config_dict(connection))
        _validate_extraction_spec(connection.extraction_spec)

    rotated = False
    if payload.credentials is not None:
        ok, error, _code = _check_credentials(payload.credentials)
        if not ok:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=error or "The new credentials failed validation.",
            )
        _seal_credentials(connection, payload.credentials, payload.credential_expires_at)
        now = utc_now()
        connection.last_validated_at = now
        connection.status = derive_status(payload.credential_expires_at, True, now)
        rotated = True

    if rotated:
        record_event(
            db,
            ctx,
            event_type="database_direct_connection.rotated",
            entity_type="database_direct_connection",
            entity_id=connection.id,
            details={
                "backend": connection.backend,
                "credential_fingerprint": connection.credential_fingerprint,
                "status": connection.status,
            },
        )
    if changed:
        record_event(
            db,
            ctx,
            event_type="database_direct_connection.updated",
            entity_type="database_direct_connection",
            entity_id=connection.id,
            details={"backend": connection.backend, "changed": sorted(changed)},
        )
    db.commit()
    return _read_model(connection)


def disable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> DatabaseConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    if connection.status != "DISABLED":
        previous = connection.status
        connection.status = "DISABLED"
        record_event(
            db,
            ctx,
            event_type="database_direct_connection.disabled",
            entity_type="database_direct_connection",
            entity_id=connection.id,
            details={"backend": connection.backend, "from": previous},
        )
    db.commit()
    return _read_model(connection)


def enable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> DatabaseConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status != "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a disabled connection can be enabled.",
        )
    now = utc_now()
    connection.status = derive_status(_aware(connection.credential_expires_at), True, now)
    record_event(
        db,
        ctx,
        event_type="database_direct_connection.enabled",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={"backend": connection.backend, "status": connection.status},
    )
    db.commit()
    return _read_model(connection)


def revoke_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> DatabaseConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status == "REVOKED":
        return _read_model(connection)
    connection.credential_ciphertext = None
    connection.credential_fingerprint = None
    connection.credential_expires_at = None
    connection.status = "REVOKED"
    record_event(
        db,
        ctx,
        event_type="database_direct_connection.revoked",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={"backend": connection.backend},
    )
    db.commit()
    return _read_model(connection)


# --- Live test / discover / sync -------------------------------------------


def test_connection(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    *,
    driver: DatabaseDriver | None = None,
) -> DatabaseConnectionTestResult:
    """Open a read-only session and stage a pull to prove reachability.

    Returns reachability, round-trip latency, and the volume a pull would
    return. Any failure is a classified, bank-safe error (never a raw driver
    exception); a credential/config-class failure also moves the stored status
    into the matching lifecycle state.
    """
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)

    started = time.perf_counter()
    try:
        tables = _introspect_live(db, connection, driver=driver)
    except DatabaseDirectError as exc:
        return _record_test_failure(db, ctx, connection, exc.code.value, str(exc))
    except (ValueError, StagedTableError) as exc:
        return _record_test_failure(db, ctx, connection, "CONFIGURATION_ERROR", str(exc))
    latency_ms = int((time.perf_counter() - started) * 1000)

    connection.last_validated_at = utc_now()
    record_event(
        db,
        ctx,
        event_type="database_direct_connection.tested",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={"backend": connection.backend, "reachable": True, "tables_visible": len(tables)},
    )
    db.commit()
    # A successful introspection proves connect + auth + read, so the connection is
    # reachable regardless of how many tables are visible or whether an extraction spec
    # has been configured yet — Discover schema lists what is available for mapping.
    return DatabaseConnectionTestResult(
        reachable=True,
        latency_ms=latency_ms,
        tables_pulled=len(tables),
        rows_pulled=0,
        error_code=None,
        error=None,
    )


def discover_schema(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    *,
    driver: DatabaseDriver | None = None,
) -> DatabaseConnectionDiscoverResult:
    """Stage a read-only pull and report its tables/columns for mapping."""
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)

    try:
        tables = _discover_live(db, connection, driver=driver)
    except DatabaseDirectError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except (ValueError, StagedTableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return DatabaseConnectionDiscoverResult(tables=tables)


def sync_now(  # noqa: PLR0913 - a sync binds bank, connection, storage, and request
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    storage: StorageClient,
    payload: DatabaseConnectionSyncRequest,
    *,
    driver: DatabaseDriver | None = None,
) -> DatabaseConnectionSyncResult:
    """Stage a read-only pull for one as-of date and ingest it end-to-end.

    Builds the adapter config from the stored connection plus opened credentials,
    stages the bundle to the bank's temp tier, and hands the ``temp://`` location
    to the existing ingestion spine for source system ``DB_DIRECT``. Returns the
    resulting batch id and terminal status.
    """
    # Imported lazily to keep this module import-light and mirror the T24 pull.
    from app.schemas.ingestion import IngestionBatchCreate  # noqa: PLC0415
    from app.services.ingestion import start_ingestion, upload_source  # noqa: PLC0415

    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    if connection.credential_ciphertext is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection holds no stored credentials.",
        )

    requested_as_of = payload.as_of_date or utc_now().date()
    as_of = requested_as_of
    try:
        bundle = _stage_pull(db, connection, as_of=as_of, driver=driver)
    except DatabaseDirectError as exc:
        connection.last_synced_at = utc_now()
        connection.last_sync_status = "failed"
        record_event(
            db,
            ctx,
            event_type="database_direct_connection.sync_failed",
            entity_type="database_direct_connection",
            entity_id=connection.id,
            details={"backend": connection.backend, "error_code": exc.code.value},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except (ValueError, StagedTableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Reconcile the requested as-of against the snapshot's own reporting date so a
    # point-in-time book is never valued at the wrong date (which would, e.g.,
    # falsely flag every position past the requested — not the true — as-of as
    # matured). When the data's as-of wins, re-stamp the bundle to match.
    spec = _build_extraction_spec(connection)
    as_of, as_of_note = _reconcile_as_of(spec, bundle, requested=requested_as_of)
    if as_of != requested_as_of:
        bundle = bundle.model_copy(update={"as_of_date": as_of.isoformat()})

    filename = f"db-direct-{connection.backend}-{as_of.isoformat()}.json"
    upload = upload_source(
        db, ctx, bank_id, storage, filename, bundle.to_json().encode("utf-8")
    )
    batch_payload = IngestionBatchCreate(
        source_system="DB_DIRECT",
        # Each connection is its own data source: scope the mapping to this
        # connection so two DB_DIRECT sources at one bank stay separate.
        source_ref=str(connection.id),
        as_of_date=as_of,
        location=upload.location,
        reason=payload.reason,
    )
    started = start_ingestion(db, ctx, bank_id, batch_payload, storage)

    connection.last_synced_at = utc_now()
    connection.last_sync_status = started.batch.status
    record_event(
        db,
        ctx,
        event_type="database_direct_connection.synced",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={
            "backend": connection.backend,
            "batch_id": str(started.batch.id),
            "status": started.batch.status,
            "as_of_date": as_of.isoformat(),
            "requested_as_of_date": requested_as_of.isoformat(),
            **({"as_of_reconciliation": as_of_note} if as_of_note else {}),
        },
    )
    db.commit()
    return DatabaseConnectionSyncResult(
        batch_id=started.batch.id,
        status=started.batch.status,
        reused=started.reused,
        records_extracted=started.batch.records_extracted,
        records_accepted=started.batch.records_accepted,
        as_of_date=as_of,
        as_of_note=as_of_note,
    )


# --- Internals -------------------------------------------------------------


def _stage_pull(
    db: Session,
    connection: DatabaseDirectConnection,
    *,
    as_of: date,
    driver: DatabaseDriver | None,
) -> StagedBundle:
    """Open a read-only session and stage one pull. Credentials are opened for
    exactly this cycle and discarded when the function returns."""
    config = _build_connection_config(connection)
    spec = _build_extraction_spec(connection)
    credentials = _open_credentials(db, connection)
    resolved = driver if driver is not None else driver_for(cast("Backend", connection.backend))
    return stage_pull(resolved, config, credentials, spec, as_of=as_of)


def _introspect_live(
    db: Session,
    connection: DatabaseDirectConnection,
    *,
    driver: DatabaseDriver | None,
) -> list[TableSchema]:
    """Open a read-only session and introspect the configured schemas.

    Unlike a pull, this needs NO extraction spec: opening the session proves
    connectivity + authentication (incl. Oracle mTLS), and the data-dictionary
    introspection lists what the read-only account can see. This is how a freshly
    created connection is tested and its schema discovered *before* any tables have
    been selected for extraction. Credentials are opened for this cycle and discarded.
    """
    config = _build_connection_config(connection)
    credentials = _open_credentials(db, connection)
    resolved = driver if driver is not None else driver_for(cast("Backend", connection.backend))
    with resolved.connect(config, credentials) as session:
        return session.introspect(config.schemas)


# Discovery reads a tiny bounded sample so the operator can map against real
# values without pulling the whole table; kept small so it is cheap and never a
# de-facto data export.
_DISCOVER_SAMPLE_ROWS = 5
_DISCOVER_SAMPLE_VALUES_PER_COLUMN = 3


def _discover_live(
    db: Session,
    connection: DatabaseDirectConnection,
    *,
    driver: DatabaseDriver | None,
) -> list[DiscoveredTable]:
    """Open one read-only session: introspect structure, then per table read a
    bounded ``COUNT(*)`` and a small row sample so the operator maps against real
    data. Count/sample are best-effort — a table whose bounded read errors still
    returns its structure (row_count ``None``, no samples) rather than failing the
    whole discovery.
    """
    config = _build_connection_config(connection)
    credentials = _open_credentials(db, connection)
    resolved = driver if driver is not None else driver_for(cast("Backend", connection.backend))
    backend = cast("Backend", connection.backend)
    discovered: list[DiscoveredTable] = []
    with resolved.connect(config, credentials) as session:
        for table in session.introspect(config.schemas):
            qualified = f"{table.schema}.{table.name}" if table.schema else table.name
            column_names = [column.name for column in table.columns]
            row_count = _safe_row_count(session, qualified, backend)
            samples = _safe_column_samples(session, qualified, backend, column_names)
            discovered.append(
                DiscoveredTable(
                    name=qualified,
                    row_count=row_count,
                    columns=[
                        DiscoveredColumn(name=name, sample_values=samples.get(name, []))
                        for name in column_names
                    ],
                )
            )
    return discovered


def _safe_row_count(session: DriverSession, table: str, backend: Backend) -> int | None:
    try:
        rows = session.fetch(build_count_select(table, backend)).as_dicts()
    except Exception:  # noqa: BLE001 - count is best-effort enrichment, not the contract
        return None
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_column_samples(
    session: DriverSession, table: str, backend: Backend, columns: list[str]
) -> dict[str, list[str]]:
    try:
        rows = session.fetch(
            build_sample_select(table, backend, row_limit=_DISCOVER_SAMPLE_ROWS)
        ).as_dicts()
    except Exception:  # noqa: BLE001 - sampling is best-effort enrichment
        return {}
    samples: dict[str, list[str]] = {}
    for column in columns:
        seen: list[str] = []
        for row in rows:
            value = row.get(column)
            if value is None:
                continue
            text_value = str(value).strip()
            if not text_value or text_value in seen:
                continue
            seen.append(text_value)
            if len(seen) >= _DISCOVER_SAMPLE_VALUES_PER_COLUMN:
                break
        if seen:
            samples[column] = seen
    return samples


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_connection_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> DatabaseDirectConnection:
    connection = db.scalar(
        select(DatabaseDirectConnection).where(
            DatabaseDirectConnection.id == connection_id,
            DatabaseDirectConnection.organization_id == ctx.organization_id,
            DatabaseDirectConnection.bank_id == bank_id,
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Database-direct connection not found."
        )
    return connection


def _ensure_not_revoked(connection: DatabaseDirectConnection) -> None:
    if connection.status == "REVOKED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection has been revoked; create a new one.",
        )


def _ensure_not_disabled(connection: DatabaseDirectConnection) -> None:
    if connection.status == "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection is disabled; enable it first.",
        )


def _vault() -> DatabaseDirectCredentialVault:
    try:
        return DatabaseDirectCredentialVault()
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The credential vault is not configured.",
        ) from exc


def _seal_credentials(
    connection: DatabaseDirectConnection,
    credentials: dict[str, Any],
    expires_at: datetime | None,
) -> None:
    vault = _vault()
    connection.credential_ciphertext = vault.seal(
        institution_id=str(connection.bank_id),
        backend=connection.backend,
        credentials=credentials,
        expires_at=expires_at,
    )
    connection.credential_fingerprint = vault.fingerprint(credentials)
    connection.credential_expires_at = expires_at


def _open_credentials(db: Session, connection: DatabaseDirectConnection) -> DbCredentials:
    _ = db
    if connection.credential_ciphertext is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection holds no stored credentials.",
        )
    try:
        return _vault().open(connection.credential_ciphertext)
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection holds no readable credentials.",
        ) from exc


def _check_credentials(
    credentials: dict[str, Any],
) -> tuple[bool, str | None, str | None]:
    """Validate credential SHAPE (no live core required). Returns (ok, error, code)."""
    username = str(credentials.get("username", "")).strip()
    if not username:
        return (
            False,
            "The connection credentials are missing a service-account username.",
            "CREDENTIAL_INVALID",
        )
    password = str(credentials.get("password", "")).strip()
    extra = credentials.get("extra")
    has_extra = isinstance(extra, dict) and bool(extra)
    if not password and not has_extra:
        return (
            False,
            "The connection credentials are missing a service-account password.",
            "CREDENTIAL_INVALID",
        )
    return True, None, None


def _record_test_failure(
    db: Session,
    ctx: TenantContext,
    connection: DatabaseDirectConnection,
    code: str,
    message: str,
) -> DatabaseConnectionTestResult:
    """Persist the outcome of a failed live test and return the bank-safe result."""
    mapped = _STATUS_BY_ERROR_CODE.get(code)
    if mapped is not None and connection.status not in ("TESTING", "DISABLED"):
        connection.status = mapped
    connection.last_validated_at = utc_now()
    record_event(
        db,
        ctx,
        event_type="database_direct_connection.tested",
        entity_type="database_direct_connection",
        entity_id=connection.id,
        details={"backend": connection.backend, "reachable": False, "error_code": code},
    )
    db.commit()
    return DatabaseConnectionTestResult(
        reachable=False, latency_ms=None, error_code=code, error=message
    )


def payload_to_connection_dict(payload: DatabaseConnectionCreate) -> dict[str, Any]:
    """The ConnectionConfig-shaped dict for a create payload (secrets excluded)."""
    return {
        "backend": payload.backend,
        "host": payload.host,
        "port": payload.port,
        "database": payload.database,
        "service_name": payload.service_name,
        "schemas": list(payload.schemas),
        "read_replicas": list(payload.read_replicas),
        "query_timeout_seconds": payload.query_timeout_seconds,
        "tls": {
            "enabled": payload.tls_enabled,
            "verify_server_certificate": payload.tls_verify_server_certificate,
        },
        **_backend_option_blocks(payload.connection_options),
    }


def _connection_to_config_dict(connection: DatabaseDirectConnection) -> dict[str, Any]:
    return {
        "backend": connection.backend,
        "host": connection.host,
        "port": connection.port,
        "database": connection.database,
        "service_name": connection.service_name,
        "schemas": list(connection.schemas or []),
        "read_replicas": list(connection.read_replicas or []),
        "query_timeout_seconds": connection.query_timeout_seconds,
        "tls": {
            "enabled": connection.tls_enabled,
            "verify_server_certificate": connection.tls_verify_server_certificate,
        },
        **_backend_option_blocks(connection.connection_options),
    }


def _backend_option_blocks(options: dict[str, Any] | None) -> dict[str, Any]:
    """Pull the backend-specific blocks (JDBC/ODBC/Snowflake) out of the stored
    connection options so they merge into the runtime ConnectionConfig."""
    opts = dict(options or {})
    blocks: dict[str, Any] = {}
    for key in ("jdbc", "odbc", "snowflake"):
        if opts.get(key) is not None:
            blocks[key] = opts[key]
    return blocks


def _build_connection_config(connection: DatabaseDirectConnection) -> ConnectionConfig:
    config = _validate_connection_config(_connection_to_config_dict(connection))
    if not connection.prefer_read_replica:
        # Honor the operator's preference to read the primary directly.
        return config.model_copy(update={"read_replicas": ()})
    return config


def _build_extraction_spec(connection: DatabaseDirectConnection) -> ExtractionSpec:
    return _validate_extraction_spec(connection.extraction_spec)


def _coerce_source_date(value: Any) -> date | None:
    """Parse a normalized as-of cell (ISO string / date / datetime) to a date.

    Staged rows are normalized to JSON-safe values, so the as-of column is an ISO
    string (``2026-04-30`` or ``2026-04-30T00:00:00``); dates/datetimes are also
    accepted defensively. Anything unparseable contributes no date.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _reconcile_as_of(
    spec: ExtractionSpec, bundle: StagedBundle, *, requested: date
) -> tuple[date, str | None]:
    """Reconcile the requested as-of against the snapshot's own reporting date.

    When the extraction spec names an ``as_of_column``, the authoritative as-of is
    the latest such date present in the pulled data. If it disagrees with the
    requested date we adopt the data's date (a point-in-time book is only ever
    valued at its own snapshot date) and return a human-readable note so the
    mismatch is surfaced, never silent. With no ``as_of_column`` or no parsable
    values, the requested date stands unchanged.
    """
    column = spec.as_of_column
    if not column:
        return requested, None
    found: set[date] = set()
    for table in bundle.tables:
        if column not in table.columns:
            continue
        for row in table.rows:
            parsed = _coerce_source_date(row.get(column))
            if parsed is not None:
                found.add(parsed)
    if not found:
        return requested, None
    data_as_of = max(found)
    if data_as_of == requested:
        return requested, None
    note = (
        f"Requested as-of {requested.isoformat()} does not match the source "
        f"snapshot's {column} of {data_as_of.isoformat()}; adopted the snapshot "
        f"date so the book is valued at its true reporting date."
    )
    return data_as_of, note


def _validate_connection_config(config_dict: dict[str, Any]) -> ConnectionConfig:
    try:
        return ConnectionConfig.model_validate(config_dict)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid connection configuration: {exc}",
        ) from exc


def _validate_extraction_spec(spec_dict: dict[str, Any] | None) -> ExtractionSpec:
    try:
        return ExtractionSpec.model_validate(spec_dict or {})
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid extraction specification: {exc}",
        ) from exc


def _apply_connection_fields(
    connection: DatabaseDirectConnection, payload: DatabaseConnectionCreate
) -> None:
    connection.backend = payload.backend
    connection.host = payload.host
    connection.port = payload.port
    connection.database = payload.database
    connection.service_name = payload.service_name
    connection.schemas = list(payload.schemas)
    connection.read_replicas = list(payload.read_replicas)
    connection.prefer_read_replica = payload.prefer_read_replica
    connection.tls_enabled = payload.tls_enabled
    connection.tls_verify_server_certificate = payload.tls_verify_server_certificate
    connection.query_timeout_seconds = payload.query_timeout_seconds
    connection.connection_options = dict(payload.connection_options or {})
    connection.extraction_spec = dict(payload.extraction_spec or {})
    connection.vault_path = build_db_vault_path(connection.bank_id, payload.backend)


def _apply_update_fields(  # noqa: PLR0912 - one branch per optional editable field
    connection: DatabaseDirectConnection,
    payload: DatabaseConnectionUpdate,
    changed: dict[str, Any],
) -> None:
    if payload.display_name is not None and payload.display_name != connection.display_name:
        connection.display_name = payload.display_name
        changed["display_name"] = payload.display_name
    if payload.host is not None and payload.host != connection.host:
        connection.host = payload.host
        changed["host"] = payload.host
    if payload.port is not None and payload.port != connection.port:
        connection.port = payload.port
        changed["port"] = payload.port
    if payload.database is not None and payload.database != connection.database:
        connection.database = payload.database
        changed["database"] = payload.database
    if payload.service_name is not None and payload.service_name != connection.service_name:
        connection.service_name = payload.service_name
        changed["service_name"] = payload.service_name
    if payload.schemas is not None:
        connection.schemas = list(payload.schemas)
        changed["schemas"] = list(connection.schemas)
    if payload.read_replicas is not None:
        connection.read_replicas = list(payload.read_replicas)
        changed["read_replicas"] = list(connection.read_replicas)
    if payload.prefer_read_replica is not None:
        connection.prefer_read_replica = payload.prefer_read_replica
        changed["prefer_read_replica"] = payload.prefer_read_replica
    if payload.tls_enabled is not None:
        connection.tls_enabled = payload.tls_enabled
        changed["tls_enabled"] = payload.tls_enabled
    if payload.tls_verify_server_certificate is not None:
        connection.tls_verify_server_certificate = payload.tls_verify_server_certificate
        changed["tls_verify_server_certificate"] = payload.tls_verify_server_certificate
    if payload.query_timeout_seconds is not None:
        connection.query_timeout_seconds = payload.query_timeout_seconds
        changed["query_timeout_seconds"] = payload.query_timeout_seconds
    if payload.connection_options is not None:
        connection.connection_options = dict(payload.connection_options)
        changed["connection_options"] = "updated"
    if payload.extraction_spec is not None:
        connection.extraction_spec = dict(payload.extraction_spec)
        changed["extraction_spec"] = "updated"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=utc_now().tzinfo)
    return value


def _read_model(
    connection: DatabaseDirectConnection, *, validation_error: str | None = None
) -> DatabaseConnectionRead:
    """Build the bank-facing view field-by-field: the credential ciphertext
    never crosses this boundary."""
    return DatabaseConnectionRead(
        id=connection.id,
        backend=cast("Any", connection.backend),
        display_name=connection.display_name,
        status=connection.status,
        host=connection.host,
        port=connection.port,
        database=connection.database,
        service_name=connection.service_name,
        schemas=list(connection.schemas or []),
        read_replicas=list(connection.read_replicas or []),
        prefer_read_replica=connection.prefer_read_replica,
        tls_enabled=connection.tls_enabled,
        tls_verify_server_certificate=connection.tls_verify_server_certificate,
        query_timeout_seconds=connection.query_timeout_seconds,
        connection_options=dict(connection.connection_options or {}),
        extraction_spec=dict(connection.extraction_spec or {}),
        credential_fingerprint=connection.credential_fingerprint,
        credential_expires_at=connection.credential_expires_at,
        last_validated_at=connection.last_validated_at,
        last_synced_at=connection.last_synced_at,
        last_sync_status=connection.last_sync_status,
        created_at=connection.created_at,
        validation_error=validation_error,
    )
