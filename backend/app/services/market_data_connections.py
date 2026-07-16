"""Market data connection lifecycle management (market_data_adapter.md §9/§10).

Owns the "Market Data Sources" management surface: onboarding (§9.2),
post-onboarding management (§9.3), and the credential lifecycle (§10) —
create, validate, test, rotate, disable/enable, revoke — plus the scope
catalog and quota views the onboarding UI renders.

Invariants:
- Credentials are write-only: they arrive in request payloads, are encrypted
  through :class:`EncryptedDbVault`, and never appear in any response, log,
  or audit record — only the SHA-256 fingerprint does (§15).
- The MVP vault is keyed by ``(organization, bank, vendor)``, so one
  connection per vendor per bank is enforced here (409 on duplicates).
  Re-adding a vendor whose connection was REVOKED reuses the retained row
  (§10.5 keeps it for audit) with fresh credentials.
- Rotation (§10.4) validates the new credential set FIRST and swaps
  ciphertext/fingerprint/expiry atomically in one transaction. MVP deviation:
  the replaced credentials are cryptographically overwritten immediately —
  there is no 7-day ``REPLACED_PENDING_DELETION`` grace retention (Phase 2).
- Every lifecycle change is audited with dotted event types
  (``market_data_connection.created`` / ``.validated`` / ``.rotated`` /
  ``.updated`` / ``.disabled`` / ``.enabled`` / ``.revoked``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

import app.adapters.market_data  # noqa: F401 - registers shipped vendor adapters
from app.adapters.market_data.base import (
    AuthResult,
    CredentialSet,
    MarketDataAdapter,
    get_market_data_adapter_class,
    registered_vendors,
)
from app.adapters.market_data.credential_manager import (
    CredentialVaultError,
    EncryptedDbVault,
    build_vault_path,
    derive_status,
)
from app.adapters.market_data.quota_tracker import month_key
from app.adapters.market_data.scope_taxonomy import (
    DEFAULT_FREQUENCY_BY_CATEGORY,
    DataScope,
    PullFrequency,
    ScopeCategory,
    category_of,
)
from app.db.base import utc_now
from app.models import Bank
from app.models.market_data import MarketDataConnection, MarketDataQuotaUsage
from app.schemas.market_data_connections import (
    MarketDataConnectionCreate,
    MarketDataConnectionListRead,
    MarketDataConnectionRead,
    MarketDataConnectionUpdate,
    MarketDataQuotaListRead,
    MarketDataScopeListRead,
    QuotaSummaryRead,
    ScopeInfoRead,
    TestPullRead,
)
from app.services.audit import record_event
from app.services.ingestion import bank_slug

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext

MANUAL_UPLOAD_VENDOR = "manual_upload"

# §10.2 mapping from a failed validation's bank-facing code to the credential
# state it evidences (mirrors app/services/market_data_jobs.py).
_STATUS_BY_ERROR_CODE: dict[str, str] = {
    "CREDENTIAL_INVALID": "INVALID",
    "CREDENTIAL_EXPIRED": "EXPIRED",
    "CREDENTIAL_REVOKED": "REVOKED",
    "SUBSCRIPTION_LAPSED": "INVALID",
    "SCOPE_NOT_PERMITTED": "INVALID",
}


class _AdapterFactory(Protocol):
    """The constructor shape every registered market data adapter exposes."""

    def __call__(self, *, db: Session, bank: Bank, bank_slug: str) -> MarketDataAdapter: ...


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_connections(
    db: Session, ctx: TenantContext, bank_id: UUID
) -> MarketDataConnectionListRead:
    _get_bank_or_404(db, ctx, bank_id)
    rows = list(
        db.scalars(
            select(MarketDataConnection)
            .where(
                MarketDataConnection.organization_id == ctx.organization_id,
                MarketDataConnection.bank_id == bank_id,
            )
            .order_by(MarketDataConnection.created_at)
        )
    )
    connections = [_read_model(row) for row in rows]
    return MarketDataConnectionListRead(connections=connections, total=len(connections))


def list_scopes(db: Session, ctx: TenantContext, bank_id: UUID) -> MarketDataScopeListRead:
    """Every taxonomy scope with per-vendor support and quota impact (§9.2 step 4)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)

    supported_by: dict[DataScope, list[str]] = {scope: [] for scope in DataScope}
    units: dict[DataScope, int] = dict.fromkeys(DataScope, 0)
    for vendor in registered_vendors():
        adapter = _adapter(db, bank, slug, vendor)
        for scope in adapter.list_available_scopes():
            supported_by[scope].append(vendor)
            if vendor == MANUAL_UPLOAD_VENDOR:
                continue  # manual uploads consume zero vendor quota (§8.3)
            estimate = adapter.estimate_quota_cost(
                [scope], DEFAULT_FREQUENCY_BY_CATEGORY[category_of(scope)], str(bank.id)
            )
            units[scope] = max(units[scope], estimate.estimated_units_per_pull)

    scopes = [
        ScopeInfoRead(
            scope=scope.value,
            category=category_of(scope).value,
            default_frequency=DEFAULT_FREQUENCY_BY_CATEGORY[category_of(scope)].value,
            quota_units=units[scope],
            supported_by=sorted(supported_by[scope]),
        )
        for scope in sorted(DataScope, key=lambda scope: scope.value)
    ]
    return MarketDataScopeListRead(scopes=scopes)


def get_quota(db: Session, ctx: TenantContext, bank_id: UUID) -> MarketDataQuotaListRead:
    """Current-month quota ledger per vendor (§11.1). Vendors without a ledger
    row report zero consumption and no cap."""
    _get_bank_or_404(db, ctx, bank_id)
    month = month_key(utc_now())
    rows = {
        row.vendor: row
        for row in db.scalars(
            select(MarketDataQuotaUsage).where(
                MarketDataQuotaUsage.organization_id == ctx.organization_id,
                MarketDataQuotaUsage.bank_id == bank_id,
                MarketDataQuotaUsage.month == month,
            )
        )
    }
    vendors = [
        QuotaSummaryRead(
            vendor=vendor,
            month=month,
            units_consumed=int(rows[vendor].units_consumed) if vendor in rows else 0,
            monthly_cap=rows[vendor].monthly_cap if vendor in rows else None,
            pull_count=int(rows[vendor].pull_count) if vendor in rows else 0,
        )
        for vendor in registered_vendors()
    ]
    return MarketDataQuotaListRead(vendors=vendors)


# ---------------------------------------------------------------------------
# Lifecycle mutations
# ---------------------------------------------------------------------------


def create_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: MarketDataConnectionCreate
) -> MarketDataConnectionRead:
    """Onboard a connection (§9.2 step 7).

    ``manual_upload`` takes no credentials and is ACTIVE immediately (§8:
    always available). Vendor connections store credentials through the
    encrypted vault and are validated inline: success activates them, failure
    leaves them TESTING with the bank-facing error on the response.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    scopes = _validated_scopes(db, bank, slug, payload.vendor, payload.scopes)
    schedule = _validated_schedule(payload.schedule)
    if payload.vendor == MANUAL_UPLOAD_VENDOR and payload.credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual upload connections do not take credentials.",
        )
    if payload.vendor != MANUAL_UPLOAD_VENDOR and not payload.credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credentials are required for a {payload.vendor} connection.",
        )

    existing = db.scalar(
        select(MarketDataConnection).where(
            MarketDataConnection.organization_id == ctx.organization_id,
            MarketDataConnection.bank_id == bank_id,
            MarketDataConnection.vendor == payload.vendor,
        )
    )
    recreated = False
    if existing is not None:
        if existing.status != "REVOKED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A {payload.vendor} connection already exists for this bank.",
            )
        # §10.5 retains revoked rows for audit; re-adding the vendor reuses the
        # retained row with fresh credentials (the vault is keyed per vendor).
        connection = existing
        recreated = True
        connection.display_name = payload.display_name
        connection.status = "TESTING"
        connection.last_pull_at = None
        connection.last_pull_status = None
        connection.last_validated_at = None
    else:
        connection = MarketDataConnection(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            vendor=payload.vendor,
            display_name=payload.display_name,
            status="TESTING",
            vault_path=build_vault_path(bank_id, payload.vendor),
            created_by=ctx.actor_user_id,
        )
        db.add(connection)
    connection.scopes = scopes
    connection.schedule = schedule
    db.flush()

    validation_error: str | None = None
    if payload.vendor == MANUAL_UPLOAD_VENDOR:
        connection.status = "ACTIVE"
    else:
        credentials = payload.credentials or {}  # non-empty: guarded above
        _vault(db).store(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            vendor=payload.vendor,
            credentials=credentials,
            expires_at=payload.credential_expires_at,
        )
        result = _adapter(db, bank, slug, payload.vendor).validate_credentials(
            _credential_set(bank, payload.vendor, credentials, payload.credential_expires_at)
        )
        if result.success:
            connection.last_validated_at = utc_now()
            connection.status = derive_status(payload.credential_expires_at, True, utc_now())
        else:
            validation_error = result.error_message  # stays TESTING (§10.2)

    record_event(
        db,
        ctx,
        event_type="market_data_connection.created",
        entity_type="market_data_connection",
        entity_id=connection.id,
        details={
            "vendor": connection.vendor,
            "display_name": connection.display_name,
            "status": connection.status,
            "scopes": list(connection.scopes),
            "credential_fingerprint": connection.credential_fingerprint,
            "recreated": recreated,
        },
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def validate_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> MarketDataConnectionRead:
    """Re-run the §10.3 credential health check on demand."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    now = utc_now()

    validation_error: str | None = None
    if connection.vendor == MANUAL_UPLOAD_VENDOR:
        # Nothing to authenticate: manual upload is always available (§8.4).
        connection.last_validated_at = now
        connection.status = "ACTIVE"
    else:
        result = _validate_stored_credentials(db, bank, connection)
        connection.last_validated_at = now
        if result.success:
            connection.status = derive_status(_aware(connection.credential_expires_at), True, now)
        else:
            validation_error = result.error_message
            # A failed check on a never-activated connection keeps it TESTING
            # (§10.2: TESTING = newly entered, not yet activated).
            if connection.status != "TESTING":
                connection.status = _STATUS_BY_ERROR_CODE.get(result.error_code or "", "INVALID")

    record_event(
        db,
        ctx,
        event_type="market_data_connection.validated",
        entity_type="market_data_connection",
        entity_id=connection.id,
        details={
            "vendor": connection.vendor,
            "success": validation_error is None,
            "status": connection.status,
            "error": validation_error,
        },
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def test_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TestPullRead:
    """Run the §9.2 step-5 representative test pull for the connection's scopes."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.vendor == MANUAL_UPLOAD_VENDOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual upload connections are tested by uploading a file; "
            "use the upload endpoint.",
        )
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    credentials = _retrieve_credentials(db, connection)
    adapter = _adapter(db, bank, bank_slug(db, bank), connection.vendor)
    result = adapter.test_pull(credentials, _connection_scopes(connection))
    return TestPullRead(
        success=result.success,
        sample_values=dict(result.sample_values),
        error=result.error,
    )


def update_connection(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    payload: MarketDataConnectionUpdate,
) -> MarketDataConnectionRead:
    """Post-onboarding edits (§9.3) and credential rotation (§10.4)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    slug = bank_slug(db, bank)

    changed: dict[str, Any] = {}
    if payload.display_name is not None and payload.display_name != connection.display_name:
        connection.display_name = payload.display_name
        changed["display_name"] = payload.display_name
    if payload.scopes is not None:
        connection.scopes = _validated_scopes(db, bank, slug, connection.vendor, payload.scopes)
        changed["scopes"] = list(connection.scopes)
    if payload.schedule is not None:
        connection.schedule = _validated_schedule(payload.schedule)
        changed["schedule"] = dict(connection.schedule)

    rotated = False
    if payload.credentials is not None:
        if connection.vendor == MANUAL_UPLOAD_VENDOR:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manual upload connections do not take credentials.",
            )
        # §10.4: validate the NEW credentials first; nothing changes on failure.
        result = _adapter(db, bank, slug, connection.vendor).validate_credentials(
            _credential_set(
                bank, connection.vendor, payload.credentials, payload.credential_expires_at
            )
        )
        if not result.success:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=result.error_message or "The new credentials failed validation.",
            )
        # Atomic swap of ciphertext + fingerprint + expiry in this transaction.
        # MVP: replaced credentials are overwritten immediately — no 7-day
        # REPLACED_PENDING_DELETION grace retention (Phase 2).
        _vault(db).store(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            vendor=connection.vendor,
            credentials=payload.credentials,
            expires_at=payload.credential_expires_at,
        )
        now = utc_now()
        connection.last_validated_at = now
        connection.status = derive_status(payload.credential_expires_at, True, now)
        rotated = True

    if rotated:
        record_event(
            db,
            ctx,
            event_type="market_data_connection.rotated",
            entity_type="market_data_connection",
            entity_id=connection.id,
            details={
                "vendor": connection.vendor,
                "credential_fingerprint": connection.credential_fingerprint,
                "status": connection.status,
            },
        )
    if changed:
        record_event(
            db,
            ctx,
            event_type="market_data_connection.updated",
            entity_type="market_data_connection",
            entity_id=connection.id,
            details={"vendor": connection.vendor, "changed": changed},
        )
    db.commit()
    return _read_model(connection)


def disable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> MarketDataConnectionRead:
    """Temporarily disable a source (§9.3): scheduled pulls stop, credentials stay."""
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    if connection.status != "DISABLED":
        previous = connection.status
        connection.status = "DISABLED"
        record_event(
            db,
            ctx,
            event_type="market_data_connection.disabled",
            entity_type="market_data_connection",
            entity_id=connection.id,
            details={"vendor": connection.vendor, "from": previous},
        )
    db.commit()
    return _read_model(connection)


def enable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> MarketDataConnectionRead:
    """Re-enable a disabled source: credentials are re-validated before pulls resume."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status != "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a disabled connection can be enabled.",
        )
    now = utc_now()
    validation_error: str | None = None
    if connection.vendor == MANUAL_UPLOAD_VENDOR:
        connection.status = "ACTIVE"
    else:
        result = _validate_stored_credentials(db, bank, connection)
        connection.last_validated_at = now
        if result.success:
            connection.status = derive_status(_aware(connection.credential_expires_at), True, now)
        else:
            validation_error = result.error_message
            connection.status = _STATUS_BY_ERROR_CODE.get(result.error_code or "", "INVALID")
    record_event(
        db,
        ctx,
        event_type="market_data_connection.enabled",
        entity_type="market_data_connection",
        entity_id=connection.id,
        details={
            "vendor": connection.vendor,
            "status": connection.status,
            "error": validation_error,
        },
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def revoke_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> MarketDataConnectionRead:
    """Revoke a connection (§10.5): wipe the stored credential, keep the row.

    Historical canonical data pulled with the credential remains valid; the
    connection is simply no longer usable for new pulls. Idempotent.
    """
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status == "REVOKED":
        return _read_model(connection)
    if connection.vendor != MANUAL_UPLOAD_VENDOR:
        _vault(db).delete(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            vendor=connection.vendor,
        )
    connection.status = "REVOKED"
    record_event(
        db,
        ctx,
        event_type="market_data_connection.revoked",
        entity_type="market_data_connection",
        entity_id=connection.id,
        details={"vendor": connection.vendor},
    )
    db.commit()
    return _read_model(connection)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_connection_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> MarketDataConnection:
    connection = db.scalar(
        select(MarketDataConnection).where(
            MarketDataConnection.id == connection_id,
            MarketDataConnection.organization_id == ctx.organization_id,
            MarketDataConnection.bank_id == bank_id,
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Market data connection not found."
        )
    return connection


def _ensure_not_revoked(connection: MarketDataConnection) -> None:
    if connection.status == "REVOKED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection is revoked; add a new connection instead.",
        )


def _ensure_not_disabled(connection: MarketDataConnection) -> None:
    if connection.status == "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection is disabled; enable it first.",
        )


def _vault(db: Session) -> EncryptedDbVault:
    try:
        return EncryptedDbVault(db)
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The credential vault is not configured.",
        ) from exc


def _adapter(db: Session, bank: Bank, slug: str, vendor: str) -> MarketDataAdapter:
    factory = cast("_AdapterFactory", get_market_data_adapter_class(vendor))
    return factory(db=db, bank=bank, bank_slug=slug)


def _credential_set(
    bank: Bank,
    vendor: str,
    credentials: dict[str, Any],
    expires_at: datetime | None,
) -> CredentialSet:
    return CredentialSet(
        institution_id=str(bank.id),
        vendor=vendor,
        credentials=credentials,
        issued_at=utc_now(),
        expires_at=expires_at,
    )


def _retrieve_credentials(db: Session, connection: MarketDataConnection) -> CredentialSet:
    """Decrypt the stored credential for exactly one validation/test cycle (§15)."""
    try:
        return _vault(db).retrieve(
            organization_id=connection.organization_id,
            bank_id=connection.bank_id,
            vendor=connection.vendor,
        )
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection holds no stored credentials.",
        ) from exc


def _validate_stored_credentials(
    db: Session, bank: Bank, connection: MarketDataConnection
) -> AuthResult:
    credentials = _retrieve_credentials(db, connection)
    adapter = _adapter(db, bank, bank_slug(db, bank), connection.vendor)
    return adapter.validate_credentials(credentials)


def _connection_scopes(connection: MarketDataConnection) -> list[DataScope]:
    return [DataScope[name] for name in connection.scopes if name in DataScope.__members__]


def _validated_scopes(
    db: Session, bank: Bank, slug: str, vendor: str, names: list[str]
) -> list[str]:
    """Resolve scope names against the taxonomy and the vendor's catalog (§9.2 step 4)."""
    scopes: list[DataScope] = []
    for name in names:
        try:
            scopes.append(DataScope[name])
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown market data scope {name!r}.",
            ) from exc
    available = set(_adapter(db, bank, slug, vendor).list_available_scopes())
    unsupported = [scope.value for scope in scopes if scope not in available]
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scopes not supported by {vendor}: {', '.join(sorted(unsupported))}.",
        )
    return sorted({scope.value for scope in scopes})


def _validated_schedule(schedule: dict[str, str] | None) -> dict[str, str]:
    """Schedule keys are scope-category (or scope) names, values PullFrequency names."""
    if not schedule:
        return {}
    valid_keys = set(ScopeCategory.__members__) | set(DataScope.__members__)
    for key, value in schedule.items():
        if key not in valid_keys:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown schedule key {key!r}; use a scope category or scope name.",
            )
        if value not in PullFrequency.__members__:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown pull frequency {value!r} for schedule key {key!r}.",
            )
    return dict(schedule)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; comparisons need tz-aware values."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=utc_now().tzinfo)
    return value


def _read_model(
    connection: MarketDataConnection, *, validation_error: str | None = None
) -> MarketDataConnectionRead:
    """Build the bank-facing view field-by-field: the credential ciphertext
    never crosses this boundary (§12.3)."""
    return MarketDataConnectionRead(
        id=connection.id,
        vendor=cast("Any", connection.vendor),
        display_name=connection.display_name,
        status=connection.status,
        scopes=list(connection.scopes),
        schedule={str(key): str(value) for key, value in (connection.schedule or {}).items()},
        credential_fingerprint=connection.credential_fingerprint,
        credential_expires_at=connection.credential_expires_at,
        last_validated_at=connection.last_validated_at,
        last_pull_at=connection.last_pull_at,
        last_pull_status=connection.last_pull_status,
        created_at=connection.created_at,
        validation_error=validation_error,
    )
