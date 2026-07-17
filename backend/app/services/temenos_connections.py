"""Temenos core-banking connection management (mirrors market_data_connections).

Onboarding, credential lifecycle (create/validate/test/rotate/disable/enable/
revoke), and the enabled-domain catalog for one bank's Temenos connection.
Credentials are write-only: request bodies may carry them, but the ciphertext
never crosses a response boundary — only status, fingerprint, and expiry do.

Validation checks credential SHAPE and signs on structurally (no live core is
required); a live health check plugs in when the portal-gated transport lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

import app.adapters.temenos_t24  # noqa: F401 - registers the T24 adapter
from app.adapters.market_data.credential_manager import derive_status
from app.adapters.temenos_t24.auth import (
    SimulatedSessionProvider,
    TemenosCredentials,
    missing_credential_fields,
)
from app.adapters.temenos_t24.catalog import CatalogError, load_mode_catalog, supported_domains
from app.adapters.temenos_t24.credential_vault import (
    CredentialVaultError,
    TemenosCredentialVault,
)
from app.adapters.temenos_t24.domains import (
    DEFAULT_CADENCE_BY_CATEGORY,
    DOMAIN_TO_ENTITY_TYPE,
    CoreBankingDomain,
    DomainCategory,
    PullCadence,
    category_of,
)
from app.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.db.base import utc_now
from app.models import Bank, MappingConfigRecord
from app.models.temenos import TemenosConnection
from app.schemas.ingestion import MappingConfigCreate
from app.schemas.temenos_connections import (
    TemenosBackfillRequest,
    TemenosConnectionCreate,
    TemenosConnectionListRead,
    TemenosConnectionRead,
    TemenosConnectionUpdate,
    TemenosDomainInfoRead,
    TemenosDomainListRead,
    TemenosPullTriggerRead,
    TemenosPullTriggerRequest,
    TemenosTestPullRead,
)
from app.services import job_queue
from app.services.audit import record_event
from app.services.ingestion import create_mapping_config

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.api.deps import TenantContext

_STATUS_BY_ERROR_CODE: dict[str, str] = {
    "CREDENTIAL_INVALID": "INVALID",
    "CREDENTIAL_EXPIRED": "EXPIRED",
    "CREDENTIAL_REVOKED": "REVOKED",
    "CONFIGURATION_ERROR": "INVALID",
}


# --- Reads -----------------------------------------------------------------


def list_connections(
    db: Session, ctx: TenantContext, bank_id: UUID
) -> TemenosConnectionListRead:
    _get_bank_or_404(db, ctx, bank_id)
    rows = db.scalars(
        select(TemenosConnection)
        .where(
            TemenosConnection.organization_id == ctx.organization_id,
            TemenosConnection.bank_id == bank_id,
        )
        .order_by(TemenosConnection.created_at)
    ).all()
    return TemenosConnectionListRead(
        connections=[_read_model(row) for row in rows], total=len(rows)
    )


def list_domains(
    db: Session, ctx: TenantContext, bank_id: UUID, mode: str
) -> TemenosDomainListRead:
    """The core-banking domain catalog for a connection mode: category, canonical
    entity type, default cadence, and whether the mode catalog supports it."""
    _get_bank_or_404(db, ctx, bank_id)
    try:
        catalog = load_mode_catalog(mode)
    except CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    supported = {domain.name for domain in supported_domains(catalog)}
    domains = [
        TemenosDomainInfoRead(
            domain=domain.name,
            category=category_of(domain).value,
            entity_type=DOMAIN_TO_ENTITY_TYPE[domain],
            default_cadence=DEFAULT_CADENCE_BY_CATEGORY[category_of(domain)].value,
            supported=domain.name in supported,
        )
        for domain in CoreBankingDomain
    ]
    return TemenosDomainListRead(mode=mode, domains=domains)


# --- Lifecycle mutations ---------------------------------------------------


def create_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: TemenosConnectionCreate
) -> TemenosConnectionRead:
    """Onboard a connection. Credentials are stored through the encrypted vault
    and validated inline: success activates it, failure leaves it TESTING with
    the bank-facing error on the response."""
    _get_bank_or_404(db, ctx, bank_id)
    mode = payload.connection_mode
    domains = _validated_domains(mode, payload.domains)
    schedule = _validated_schedule(payload.schedule)
    if not payload.credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Credentials are required for a {mode} connection.",
        )

    existing = db.scalar(
        select(TemenosConnection).where(
            TemenosConnection.organization_id == ctx.organization_id,
            TemenosConnection.bank_id == bank_id,
            TemenosConnection.display_name == payload.display_name,
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
        connection.last_pull_at = None
        connection.last_pull_status = None
        connection.last_validated_at = None
    else:
        connection = TemenosConnection(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            display_name=payload.display_name,
            status="TESTING",
            vault_path="",
            created_by=ctx.actor_user_id,
        )
        db.add(connection)
    connection.core_system = payload.core_system
    connection.connection_mode = mode
    connection.endpoint = payload.endpoint
    connection.companies = list(payload.companies)
    connection.default_currency = payload.default_currency
    connection.domains = domains
    connection.schedule = schedule
    connection.catalog_overrides = dict(payload.catalog_overrides or {})
    db.flush()

    _vault(db).store(
        connection, credentials=payload.credentials, expires_at=payload.credential_expires_at
    )
    ok, error, _code = _check_credentials(
        mode, payload.endpoint, payload.credentials, payload.credential_expires_at
    )
    validation_error: str | None = None
    if ok:
        connection.last_validated_at = utc_now()
        connection.status = derive_status(payload.credential_expires_at, True, utc_now())
    else:
        validation_error = error  # stays TESTING

    record_event(
        db,
        ctx,
        event_type="temenos_connection.created",
        entity_type="temenos_connection",
        entity_id=connection.id,
        details={
            "connection_mode": connection.connection_mode,
            "display_name": connection.display_name,
            "status": connection.status,
            "domains": list(connection.domains),
            "credential_fingerprint": connection.credential_fingerprint,
            "recreated": recreated,
        },
    )
    db.commit()
    _seed_default_mapping(db, ctx, bank_id, mode)
    return _read_model(connection, validation_error=validation_error)


def _seed_default_mapping(db: Session, ctx: TenantContext, bank_id: UUID, mode: str) -> None:
    """Seed the bank's default T24 mapping on onboarding if it has none, so the
    connection is immediately pull-ready. The default mapping is mode-independent
    (identity fields + attribute columns + enum maps), so one serves every mode.
    """
    existing = db.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.organization_id == ctx.organization_id,
            MappingConfigRecord.bank_id == bank_id,
            MappingConfigRecord.source_system == "T24",
            MappingConfigRecord.status == "active",
        )
    )
    if existing is not None:
        return
    create_mapping_config(
        db,
        ctx,
        bank_id,
        MappingConfigCreate(
            source_system="T24",
            name=f"Default T24 ({mode})",
            config=default_t24_mapping_config(mode),
            activate=True,
            reason="auto-seeded on Temenos connection onboarding",
        ),
    )


def validate_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)

    now = utc_now()
    ok, error, code = _validate_stored(db, connection, now)
    connection.last_validated_at = now
    validation_error: str | None = None
    if ok:
        connection.status = derive_status(_aware(connection.credential_expires_at), True, now)
    else:
        validation_error = error
        if connection.status != "TESTING":
            connection.status = _STATUS_BY_ERROR_CODE.get(code or "", "INVALID")

    record_event(
        db,
        ctx,
        event_type="temenos_connection.validated",
        entity_type="temenos_connection",
        entity_id=connection.id,
        details={
            "connection_mode": connection.connection_mode,
            "success": validation_error is None,
        },
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def test_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosTestPullRead:
    """Sign on and report the pull plan. A live pull runs when the portal-gated
    transport is enabled; MVP verifies configuration + credentials end-to-end
    short of the live network."""
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    credentials = _retrieve_credentials(db, connection)
    try:
        session = SimulatedSessionProvider().sign_on(
            connection.connection_mode,
            connection.endpoint,
            credentials,
            company=connection.companies[0] if connection.companies else None,
        )
    except ValueError as exc:
        return TemenosTestPullRead(success=False, sample_values={}, error=str(exc))
    return TemenosTestPullRead(
        success=True,
        sample_values={
            "connection_mode": connection.connection_mode,
            "endpoint": connection.endpoint,
            "company": str(session.company or ""),
            "enabled_domains": str(len(connection.domains)),
            "note": "Configuration and credentials verified; a live pull runs when the "
            "core transport is enabled.",
        },
        error=None,
    )


def update_connection(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosConnectionUpdate,
) -> TemenosConnectionRead:
    """Post-onboarding edits and credential rotation (validate new first)."""
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)

    changed: dict[str, Any] = {}
    if payload.display_name is not None and payload.display_name != connection.display_name:
        connection.display_name = payload.display_name
        changed["display_name"] = payload.display_name
    if payload.endpoint is not None and payload.endpoint != connection.endpoint:
        connection.endpoint = payload.endpoint
        changed["endpoint"] = payload.endpoint
    if payload.companies is not None:
        connection.companies = list(payload.companies)
        changed["companies"] = list(connection.companies)
    if payload.default_currency is not None:
        connection.default_currency = payload.default_currency
        changed["default_currency"] = payload.default_currency
    if payload.domains is not None:
        connection.domains = _validated_domains(connection.connection_mode, payload.domains)
        changed["domains"] = list(connection.domains)
    if payload.schedule is not None:
        connection.schedule = _validated_schedule(payload.schedule)
        changed["schedule"] = dict(connection.schedule)
    if payload.catalog_overrides is not None:
        connection.catalog_overrides = dict(payload.catalog_overrides)
        changed["catalog_overrides"] = "updated"

    rotated = False
    if payload.credentials is not None:
        ok, error, _code = _check_credentials(
            connection.connection_mode,
            connection.endpoint,
            payload.credentials,
            payload.credential_expires_at,
        )
        if not ok:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=error or "The new credentials failed validation.",
            )
        _vault(db).store(
            connection,
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
            event_type="temenos_connection.rotated",
            entity_type="temenos_connection",
            entity_id=connection.id,
            details={
                "connection_mode": connection.connection_mode,
                "credential_fingerprint": connection.credential_fingerprint,
                "status": connection.status,
            },
        )
    if changed:
        record_event(
            db,
            ctx,
            event_type="temenos_connection.updated",
            entity_type="temenos_connection",
            entity_id=connection.id,
            details={"connection_mode": connection.connection_mode, "changed": changed},
        )
    db.commit()
    return _read_model(connection)


def disable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    if connection.status != "DISABLED":
        previous = connection.status
        connection.status = "DISABLED"
        record_event(
            db,
            ctx,
            event_type="temenos_connection.disabled",
            entity_type="temenos_connection",
            entity_id=connection.id,
            details={"connection_mode": connection.connection_mode, "from": previous},
        )
    db.commit()
    return _read_model(connection)


def enable_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status != "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a disabled connection can be enabled.",
        )
    now = utc_now()
    ok, error, code = _validate_stored(db, connection, now)
    connection.last_validated_at = now
    validation_error: str | None = None
    if ok:
        connection.status = derive_status(_aware(connection.credential_expires_at), True, now)
    else:
        validation_error = error
        connection.status = _STATUS_BY_ERROR_CODE.get(code or "", "INVALID")
    record_event(
        db,
        ctx,
        event_type="temenos_connection.enabled",
        entity_type="temenos_connection",
        entity_id=connection.id,
        details={"connection_mode": connection.connection_mode, "status": connection.status},
    )
    db.commit()
    return _read_model(connection, validation_error=validation_error)


def trigger_pull(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosPullTriggerRequest,
) -> TemenosPullTriggerRead:
    """Enqueue an on-demand pull for one as-of date (coalesced per date)."""
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    as_of = payload.as_of_date or utc_now().date()
    job = job_queue.enqueue(
        db,
        ctx.organization_id,
        "temenos_pull",
        bank_id=connection.bank_id,
        payload={"connection_id": str(connection.id), "as_of_date": as_of.isoformat()},
        coalesce_key=f"t24_pull:{connection.id}:{as_of.isoformat()}",
    )
    db.commit()
    return TemenosPullTriggerRead(job_ids=[job.id], count=1)


def trigger_backfill(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    connection_id: UUID,
    payload: TemenosBackfillRequest,
) -> TemenosPullTriggerRead:
    """Enqueue one pull per as-of date across an inclusive historical range."""
    from app.services.temenos_jobs import TemenosJobError, enqueue_backfill  # noqa: PLC0415

    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    _ensure_not_revoked(connection)
    _ensure_not_disabled(connection)
    try:
        jobs = enqueue_backfill(db, ctx, connection, payload.start_date, payload.end_date)
    except TemenosJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return TemenosPullTriggerRead(job_ids=[job.id for job in jobs], count=len(jobs))


def revoke_connection(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosConnectionRead:
    _get_bank_or_404(db, ctx, bank_id)
    connection = _get_connection_or_404(db, ctx, bank_id, connection_id)
    if connection.status == "REVOKED":
        return _read_model(connection)
    _vault(db).delete(connection)
    connection.status = "REVOKED"
    record_event(
        db,
        ctx,
        event_type="temenos_connection.revoked",
        entity_type="temenos_connection",
        entity_id=connection.id,
        details={"connection_mode": connection.connection_mode},
    )
    db.commit()
    return _read_model(connection)


# --- Internals -------------------------------------------------------------


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_connection_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, connection_id: UUID
) -> TemenosConnection:
    connection = db.scalar(
        select(TemenosConnection).where(
            TemenosConnection.id == connection_id,
            TemenosConnection.organization_id == ctx.organization_id,
            TemenosConnection.bank_id == bank_id,
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Temenos connection not found."
        )
    return connection


def _ensure_not_revoked(connection: TemenosConnection) -> None:
    if connection.status == "REVOKED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection has been revoked; create a new one.",
        )


def _ensure_not_disabled(connection: TemenosConnection) -> None:
    if connection.status == "DISABLED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection is disabled; enable it first.",
        )


def _vault(db: Session) -> TemenosCredentialVault:
    try:
        return TemenosCredentialVault(db)
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The credential vault is not configured.",
        ) from exc


def _retrieve_credentials(db: Session, connection: TemenosConnection) -> TemenosCredentials:
    try:
        return _vault(db).retrieve(connection)
    except CredentialVaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This connection holds no stored credentials.",
        ) from exc


def _check_credentials(
    mode: str,
    endpoint: str,
    credentials_dict: dict[str, Any],
    expires_at: datetime | None,
) -> tuple[bool, str | None, str | None]:
    """Validate credential shape + structural sign-on. Returns (ok, error, code)."""
    creds = TemenosCredentials.from_dict(credentials_dict)
    missing = missing_credential_fields(mode, creds)
    if missing:
        return (
            False,
            f"The {mode} connection is missing required credential fields: {', '.join(missing)}.",
            "CREDENTIAL_INVALID",
        )
    expires = _aware(expires_at)
    if expires is not None and expires <= utc_now():
        return False, "The supplied credentials have already expired.", "CREDENTIAL_EXPIRED"
    try:
        SimulatedSessionProvider().sign_on(mode, endpoint, creds)
    except ValueError as exc:
        return False, str(exc), "CONFIGURATION_ERROR"
    return True, None, None


def _validate_stored(
    db: Session, connection: TemenosConnection, now: datetime
) -> tuple[bool, str | None, str | None]:
    credentials = _retrieve_credentials(db, connection)
    missing = missing_credential_fields(connection.connection_mode, credentials)
    if missing:
        return (
            False,
            f"The {connection.connection_mode} connection is missing required credential "
            f"fields: {', '.join(missing)}.",
            "CREDENTIAL_INVALID",
        )
    expires = _aware(connection.credential_expires_at)
    if expires is not None and expires <= now:
        return False, "The stored credentials have expired.", "CREDENTIAL_EXPIRED"
    return True, None, None


def _validated_domains(mode: str, names: list[str] | None) -> list[str]:
    try:
        catalog = load_mode_catalog(mode)
    except CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    supported = {domain.name for domain in supported_domains(catalog)}
    if not names:
        return sorted(supported)
    result: set[str] = set()
    for name in names:
        if name not in CoreBankingDomain.__members__:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown core-banking domain {name!r}.",
            )
        if name not in supported:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Domain {name!r} is not supported by the {mode} catalog.",
            )
        result.add(name)
    return sorted(result)


def _validated_schedule(schedule: dict[str, str] | None) -> dict[str, str]:
    if not schedule:
        return {}
    categories = {c.name for c in DomainCategory}
    cadences = {c.name for c in PullCadence}
    for key, value in schedule.items():
        if key not in categories:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown domain category {key!r} in schedule.",
            )
        if value not in cadences:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown pull cadence {value!r} in schedule.",
            )
    return dict(schedule)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=utc_now().tzinfo)
    return value


def _read_model(
    connection: TemenosConnection, *, validation_error: str | None = None
) -> TemenosConnectionRead:
    """Build the bank-facing view field-by-field: the credential ciphertext
    never crosses this boundary."""
    return TemenosConnectionRead(
        id=connection.id,
        core_system=connection.core_system,  # type: ignore[arg-type]
        connection_mode=connection.connection_mode,  # type: ignore[arg-type]
        display_name=connection.display_name,
        endpoint=connection.endpoint,
        status=connection.status,
        companies=list(connection.companies or []),
        default_currency=connection.default_currency,
        domains=list(connection.domains or []),
        schedule={str(k): str(v) for k, v in (connection.schedule or {}).items()},
        catalog_overrides=dict(connection.catalog_overrides or {}),
        credential_fingerprint=connection.credential_fingerprint,
        credential_expires_at=connection.credential_expires_at,
        last_validated_at=connection.last_validated_at,
        last_pull_at=connection.last_pull_at,
        last_pull_status=connection.last_pull_status,
        created_at=connection.created_at,
        validation_error=validation_error,
    )
