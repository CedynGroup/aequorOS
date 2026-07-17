"""Temenos pull execution and scheduling glue for the live engine.

Two halves, mirroring ``market_data_jobs``:

- :func:`run_temenos_pull` is the ``temenos_pull`` worker handler: it loads the
  org-scoped :class:`TemenosConnection`, decrypts its credentials for one sign-on
  cycle, selects the live transport for the connection's mode, and runs the
  stage-then-ingest pull. Error handling follows the connection-lifecycle policy:
  transient core failures (core unavailable, network, rate-limited, COB running,
  session limit) re-raise so the worker retries with backoff, while credential-
  class failures mark the connection (INVALID / EXPIRED / REVOKED) and complete
  the job — retrying cannot fix credentials.

- :func:`enqueue_due_temenos_pulls` is the scheduler-tick extension: it applies
  the cheap expiry-based credential health check, then enqueues one coalesced
  ``temenos_pull`` per connection whose EOD/COB schedule slot has arrived. Gated
  on ``TEMENOS_PULL_ENABLED`` (off by default).

The live transports are portal-gated, so an enabled scheduled pull fails-retries
with an actionable ``CORE_UNAVAILABLE`` until a Temenos-approved engineer
completes the transport; fixtures inject a working transport in tests.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data.credential_manager import derive_status
from app.adapters.temenos_t24.auth import SimulatedSessionProvider
from app.adapters.temenos_t24.catalog import load_mode_catalog, supported_domains
from app.adapters.temenos_t24.credential_vault import (
    CredentialVaultError,
    TemenosCredentialVault,
)
from app.adapters.temenos_t24.domains import (
    DEFAULT_CADENCE_BY_CATEGORY,
    CoreBankingDomain,
    PullCadence,
    category_of,
)
from app.adapters.temenos_t24.errors import TemenosError, TemenosErrorCode
from app.adapters.temenos_t24.mappings.default import default_t24_mapping_config
from app.adapters.temenos_t24.pull import pull_and_ingest
from app.adapters.temenos_t24.transport import T24Transport
from app.adapters.temenos_t24.transports import live_transport_for
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Bank, Job, MappingConfigRecord
from app.models.temenos import TemenosConnection
from app.schemas.ingestion import MappingConfigCreate
from app.services import job_queue
from app.services.audit import record_event
from app.services.ingestion import create_mapping_config
from app.storage.factory import get_storage_client

logger = logging.getLogger(__name__)

TEMENOS_PULL = "temenos_pull"

# Statuses eligible for scheduled pulls: EXPIRING_SOON credentials still
# authenticate, so pulls continue while the bank rotates them.
_SCHEDULABLE_STATUSES = ("ACTIVE", "EXPIRING_SOON")

# Core/transport codes the worker may retry with backoff — a later attempt can
# genuinely succeed (the core comes back, COB finishes, the rate limit clears).
_RETRYABLE_CODES = frozenset(
    {
        TemenosErrorCode.CORE_UNAVAILABLE,
        TemenosErrorCode.NETWORK_ERROR,
        TemenosErrorCode.RATE_LIMITED,
        TemenosErrorCode.COB_IN_PROGRESS,
        TemenosErrorCode.SESSION_LIMIT_REACHED,
    }
)

# Credential-class codes mark the connection and complete the job without retry.
_CONNECTION_STATUS_BY_CODE: dict[TemenosErrorCode, str] = {
    TemenosErrorCode.CREDENTIAL_INVALID: "INVALID",
    TemenosErrorCode.CREDENTIAL_EXPIRED: "EXPIRED",
    TemenosErrorCode.CREDENTIAL_REVOKED: "REVOKED",
}

_CADENCE_INTERVAL: dict[PullCadence, timedelta] = {
    PullCadence.END_OF_DAY: timedelta(hours=20),
    PullCadence.WEEKLY: timedelta(days=7),
    PullCadence.MONTHLY: timedelta(days=30),
}


class TemenosJobError(Exception):
    """A pull job could not run (missing connection, bank, or payload)."""


def resolve_transport(connection: TemenosConnection) -> T24Transport:
    """The live transport for a connection's mode (overridable in tests)."""
    return live_transport_for(connection.connection_mode)


def run_temenos_pull(session: Session, job: Job) -> None:
    """Worker handler: execute one Temenos pull for a connection.

    Payload: ``{"connection_id": ..., "as_of_date": ...}``.
    """
    connection = _connection_or_error(session, job)
    if connection.status not in _SCHEDULABLE_STATUSES and connection.status != "TESTING":
        job.progress = {
            "connection_id": str(connection.id),
            "status": "skipped",
            "reason": f"connection status {connection.status} is not pullable",
        }
        return
    _bank_or_error(session, job.organization_id, connection.bank_id)  # tenant/bank guard
    as_of = _as_of_from_payload(job)
    ctx = TenantContext(
        organization_id=connection.organization_id, actor_user_id=connection.created_by
    )
    now = utc_now()

    try:
        credentials = TemenosCredentialVault(session).retrieve(connection)
    except CredentialVaultError as exc:
        connection.last_pull_at = now
        connection.last_pull_status = "failed"
        session.commit()
        logger.warning("Temenos pull for %s has no stored credentials: %s", connection.id, exc)
        job.progress = {
            "connection_id": str(connection.id),
            "status": "failed_no_retry",
            "error": "connection holds no stored credentials",
        }
        return

    mapping_id = _ensure_default_mapping(session, ctx, connection)
    transport = resolve_transport(connection)
    company = connection.companies[0] if connection.companies else None
    try:
        result = pull_and_ingest(
            session,
            ctx,
            connection.bank_id,
            get_storage_client(),
            mode=connection.connection_mode,
            as_of=as_of,
            company=company,
            transport=transport,
            session_provider=SimulatedSessionProvider(),
            credentials=credentials,
            endpoint=connection.endpoint,
            reason=f"scheduled T24 pull {as_of.isoformat()}",
            mapping_config_id=mapping_id,
            domains=connection.domains or None,
            catalog_overrides=connection.catalog_overrides or None,
        )
    except TemenosError as exc:
        _record_pull_failure(session, job, connection, exc, now)
        return

    connection.last_pull_at = now
    connection.last_pull_status = "succeeded"
    session.commit()
    job.progress = {
        "connection_id": str(connection.id),
        "as_of_date": as_of.isoformat(),
        "batch_status": result.batch.status,
        "records_accepted": result.batch.records_accepted,
        "reused": result.reused,
    }


def _record_pull_failure(
    session: Session,
    job: Job,
    connection: TemenosConnection,
    exc: TemenosError,
    now: datetime,
) -> None:
    """Retry transient core faults; park credential ones. Only the bank-facing
    message is persisted — ``internal_detail`` goes to the engineering log."""
    code = exc.bank_facing.code
    connection.last_pull_at = now
    connection.last_pull_status = "failed"
    logger.warning(
        "Temenos pull failed for connection %s (%s): %s",
        connection.id,
        code.value,
        exc.internal_detail,
    )
    if code in _RETRYABLE_CODES:
        session.commit()
        raise exc

    credential_status = _CONNECTION_STATUS_BY_CODE.get(code)
    if credential_status is not None and connection.status != credential_status:
        _transition_status(session, connection, credential_status, reason=code.value)
    session.commit()
    job.progress = {
        "connection_id": str(connection.id),
        "status": "failed_no_retry",
        "error_code": code.value,
        "error": exc.bank_facing.message,
    }


def enqueue_due_temenos_pulls(
    session: Session,
    organization_id: UUID,
    now: datetime | None = None,
) -> list[Job]:
    """Enqueue one coalesced pull per connection whose schedule slot arrived.

    Also runs the cheap expiry-based credential health check (ACTIVE →
    EXPIRING_SOON → EXPIRED). Inert unless ``TEMENOS_PULL_ENABLED``.
    """
    if not get_settings().temenos.temenos_pull_enabled:
        return []
    now = now or utc_now()
    connections = list(
        session.scalars(
            select(TemenosConnection).where(
                TemenosConnection.organization_id == organization_id
            )
        )
    )
    enqueued: list[Job] = []
    for connection in connections:
        _apply_credential_health(session, connection, now)
        if connection.status not in _SCHEDULABLE_STATUSES:
            continue
        if not _is_due(connection, now):
            continue
        as_of = now.date()
        enqueued.append(
            job_queue.enqueue(
                session,
                organization_id,
                TEMENOS_PULL,
                bank_id=connection.bank_id,
                payload={
                    "connection_id": str(connection.id),
                    "as_of_date": as_of.isoformat(),
                },
                coalesce_key=f"t24_pull:{connection.id}:{as_of.isoformat()}",
            )
        )
    session.flush()
    return enqueued


def enqueue_backfill(
    session: Session,
    ctx: TenantContext,
    connection: TemenosConnection,
    start: date,
    end: date,
) -> list[Job]:
    """Enqueue one pull job per as-of date in ``[start, end]`` (inclusive).

    Each date is an independent, coalesced pull; immutable calculation runs mean
    re-staging an already-ingested date supersedes rather than duplicates.
    """
    if end < start:
        raise TemenosJobError("backfill end date precedes start date.")
    jobs: list[Job] = []
    cursor = start
    while cursor <= end:
        jobs.append(
            job_queue.enqueue(
                session,
                ctx.organization_id,
                TEMENOS_PULL,
                bank_id=connection.bank_id,
                payload={
                    "connection_id": str(connection.id),
                    "as_of_date": cursor.isoformat(),
                },
                coalesce_key=f"t24_pull:{connection.id}:{cursor.isoformat()}",
            )
        )
        cursor += timedelta(days=1)
    session.flush()
    return jobs


# ---------------------------------------------------------------------------
# Scheduling internals
# ---------------------------------------------------------------------------


def _apply_credential_health(
    session: Session, connection: TemenosConnection, now: datetime
) -> None:
    """Persist expiry-derived transitions (ACTIVE → EXPIRING_SOON → EXPIRED)."""
    if connection.status not in _SCHEDULABLE_STATUSES:
        return
    expires_at = connection.credential_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    derived = derive_status(expires_at, None, now)
    if derived != connection.status and derived in ("EXPIRING_SOON", "EXPIRED", "ACTIVE"):
        _transition_status(session, connection, derived, reason="credential_expiry_check")


def _transition_status(
    session: Session, connection: TemenosConnection, new_status: str, *, reason: str
) -> None:
    old_status = connection.status
    connection.status = new_status
    record_event(
        session,
        TenantContext(organization_id=connection.organization_id),
        event_type="temenos_connection.status_changed",
        entity_type="temenos_connection",
        entity_id=connection.id,
        details={
            "connection_mode": connection.connection_mode,
            "from": old_status,
            "to": new_status,
            "reason": reason,
        },
    )
    session.flush()


def _scheduled_domains(connection: TemenosConnection) -> list[str]:
    """The domains this connection pulls: its enabled set, or — when empty —
    every domain the mode catalog supports (matching the pull's behavior)."""
    if connection.domains:
        return list(connection.domains)
    try:
        catalog = load_mode_catalog(connection.connection_mode)
    except Exception:  # noqa: BLE001 - an unknown mode simply has no schedule
        return []
    return [domain.name for domain in supported_domains(catalog)]


def _effective_interval(connection: TemenosConnection) -> timedelta | None:
    """The shortest schedule interval across the connection's enabled domains,
    or None when every enabled domain is on-demand only."""
    intervals: list[timedelta] = []
    schedule = connection.schedule or {}
    for name in _scheduled_domains(connection):
        if name not in CoreBankingDomain.__members__:
            continue
        category = category_of(CoreBankingDomain[name])
        raw = schedule.get(category.name)
        cadence = (
            PullCadence[raw]
            if isinstance(raw, str) and raw in PullCadence.__members__
            else DEFAULT_CADENCE_BY_CATEGORY[category]
        )
        interval = _CADENCE_INTERVAL.get(cadence)
        if interval is not None:
            intervals.append(interval)
    return min(intervals) if intervals else None


def _is_due(connection: TemenosConnection, now: datetime) -> bool:
    interval = _effective_interval(connection)
    if interval is None:
        return False
    last = connection.last_pull_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    return (now - last) >= interval


# ---------------------------------------------------------------------------
# Pull internals
# ---------------------------------------------------------------------------


def _ensure_default_mapping(
    session: Session, ctx: TenantContext, connection: TemenosConnection
) -> UUID:
    """The bank's active T24 mapping, seeding the catalog default if absent."""
    existing = session.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.organization_id == ctx.organization_id,
            MappingConfigRecord.bank_id == connection.bank_id,
            MappingConfigRecord.source_system == "T24",
            MappingConfigRecord.status == "active",
        )
    )
    if existing is not None:
        return existing.id
    created = create_mapping_config(
        session,
        ctx,
        connection.bank_id,
        MappingConfigCreate(
            source_system="T24",
            name=f"Default T24 ({connection.connection_mode})",
            config=default_t24_mapping_config(connection.connection_mode),
            activate=True,
            reason="auto-seeded for scheduled T24 pull",
        ),
    )
    return created.id


def _connection_or_error(session: Session, job: Job) -> TemenosConnection:
    raw_id = job.payload.get("connection_id")
    if not raw_id:
        msg = f"Job {job.id} payload carries no connection_id."
        raise TemenosJobError(msg)
    connection = session.scalar(
        select(TemenosConnection).where(
            TemenosConnection.id == UUID(str(raw_id)),
            TemenosConnection.organization_id == job.organization_id,
        )
    )
    if connection is None:
        msg = f"Job {job.id} references unknown Temenos connection {raw_id}."
        raise TemenosJobError(msg)
    return connection


def _bank_or_error(session: Session, organization_id: UUID, bank_id: UUID) -> Bank:
    bank = session.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        msg = f"Temenos connection references unknown bank {bank_id}."
        raise TemenosJobError(msg)
    return bank


def _as_of_from_payload(job: Job) -> date:
    raw = job.payload.get("as_of_date")
    return date.fromisoformat(str(raw)) if raw else utc_now().date()
