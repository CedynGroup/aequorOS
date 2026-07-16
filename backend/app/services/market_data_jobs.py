"""Market data pull execution and scheduling glue for the live engine.

Two halves:

- :func:`run_market_data_pull` is the ``market_data_pull`` worker handler:
  it loads the org-scoped :class:`MarketDataConnection`, retrieves the
  bank's credentials from the encrypted vault for one pull cycle
  (market_data_adapter.md §10.1/§15), instantiates the vendor's adapter from
  the registry — never importing a concrete adapter — and runs the pull.
  Error handling follows §10/§12: transient vendor failures re-raise so the
  worker retries with backoff, while credential-class failures mark the
  connection (INVALID / EXPIRED / REVOKED) and complete the job — retrying
  cannot fix credentials.

- :func:`enqueue_due_market_data_pulls` is the scheduler-tick extension:
  for every schedulable connection it derives the §10.3 credential health
  state, persists ACTIVE → EXPIRING_SOON → EXPIRED transitions, computes
  the scopes whose §9.2 schedule slot has arrived, and enqueues one
  coalesced ``market_data_pull`` per (connection, as-of). Gated on
  ``MARKET_DATA_PULL_ENABLED`` (off by default per §14.1).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.adapters.market_data  # noqa: F401 - registers shipped vendor adapters
from app.adapters.market_data.base import (
    CredentialSet,
    MarketDataAdapter,
    get_market_data_adapter_class,
)
from app.adapters.market_data.credential_manager import EncryptedDbVault, derive_status
from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError
from app.adapters.market_data.scheduler import due_scopes
from app.adapters.market_data.scope_taxonomy import (
    DEFAULT_FREQUENCY_BY_CATEGORY,
    DataScope,
    PullFrequency,
    category_of,
)
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Bank, Job
from app.models.market_data import MarketDataConnection
from app.services import job_queue
from app.services.audit import record_event
from app.services.ingestion import bank_slug

logger = logging.getLogger(__name__)

MARKET_DATA_PULL = "market_data_pull"

# Connection statuses eligible for scheduled pulls: EXPIRING_SOON credentials
# still authenticate (§10.2), so pulls continue while the bank rotates them.
_SCHEDULABLE_STATUSES = ("ACTIVE", "EXPIRING_SOON")

# §12 codes the worker may retry with backoff: the vendor or the network is
# transiently unhappy and a later attempt can genuinely succeed.
_RETRYABLE_CODES = frozenset(
    {
        BankFacingErrorCode.VENDOR_UNAVAILABLE,
        BankFacingErrorCode.NETWORK_ERROR,
        BankFacingErrorCode.RATE_LIMITED,
    }
)

# Credential-class codes mark the connection per the §10.2 state machine and
# complete the job without retry — retrying cannot fix credentials.
_CONNECTION_STATUS_BY_CODE: dict[BankFacingErrorCode, str] = {
    BankFacingErrorCode.CREDENTIAL_INVALID: "INVALID",
    BankFacingErrorCode.CREDENTIAL_EXPIRED: "EXPIRED",
    BankFacingErrorCode.CREDENTIAL_REVOKED: "REVOKED",
    BankFacingErrorCode.SUBSCRIPTION_LAPSED: "INVALID",
    BankFacingErrorCode.SCOPE_NOT_PERMITTED: "INVALID",
}


class MarketDataJobError(Exception):
    """A pull job could not run (missing connection, bank, or payload)."""


class _AdapterFactory(Protocol):
    """The constructor shape every registered market data adapter exposes."""

    def __call__(self, *, db: Session, bank: Bank, bank_slug: str) -> MarketDataAdapter: ...


def run_market_data_pull(session: Session, job: Job) -> None:
    """Worker handler: execute one market data pull for a connection.

    Payload: ``{"connection_id": ..., "scopes": [names]?, "as_of_date": ...}``.
    ``scopes`` defaults to every scope the connection is authorized to pull.
    """
    connection = _connection_or_error(session, job)
    bank = _bank_or_error(session, job.organization_id, connection.bank_id)
    slug = bank_slug(session, bank)
    scopes = _scopes_from_payload(job, connection)
    as_of = _as_of_from_payload(job)

    credentials = _retrieve_credentials(session, connection, bank)
    adapter_factory = cast("_AdapterFactory", get_market_data_adapter_class(connection.vendor))
    adapter = adapter_factory(db=session, bank=bank, bank_slug=slug)

    now = utc_now()
    try:
        result = adapter.pull(
            credentials,
            scopes,
            as_of,
            institution_id=str(bank.id),
            batch_id="",  # adapters mint their own batch via the pull runner
        )
    except MarketDataError as exc:
        _record_pull_failure(session, job, connection, exc, now)
        return

    connection.last_pull_at = now
    connection.last_pull_status = "succeeded"
    session.commit()
    job.progress = {
        "connection_id": str(connection.id),
        "batch_id": result.batch_id,
        "scopes_pulled": [scope.value for scope in result.scopes_pulled],
        "canonical_records_produced": result.canonical_records_produced,
        "errors": result.errors,
    }


def _record_pull_failure(
    session: Session,
    job: Job,
    connection: MarketDataConnection,
    exc: MarketDataError,
    now: datetime,
) -> None:
    """Apply §10/§12 failure policy: retry transient codes, park credential ones.

    Only the bank-facing message is persisted; ``internal_detail`` goes to the
    engineering log and never to a bank-visible surface (§12.3).
    """
    code = exc.bank_facing.code
    connection.last_pull_at = now
    connection.last_pull_status = "failed"
    logger.warning(
        "Market data pull failed for connection %s (%s): %s",
        connection.id,
        code.value,
        exc.internal_detail,
    )
    if code in _RETRYABLE_CODES:
        # Commit the failure mark before re-raising: the worker rolls the
        # session back and requeues the job with backoff.
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


def enqueue_due_market_data_pulls(
    session: Session,
    organization_id: UUID,
    now: datetime | None = None,
) -> list[Job]:
    """Enqueue one coalesced pull per connection whose schedule slot arrived.

    Also performs the cheap half of the §10.3 daily health check: expiry-based
    state transitions (ACTIVE → EXPIRING_SOON → EXPIRED) are derived from
    ``credential_expires_at`` and persisted. Manual-upload connections are
    never scheduled (§8: the operator pushes; nothing to pull). Inert unless
    ``MARKET_DATA_PULL_ENABLED``.
    """
    if not get_settings().market_data.market_data_pull_enabled:
        return []
    now = now or utc_now()
    connections = list(
        session.scalars(
            select(MarketDataConnection).where(
                MarketDataConnection.organization_id == organization_id,
                MarketDataConnection.vendor != "manual_upload",
            )
        )
    )
    enqueued: list[Job] = []
    for connection in connections:
        _apply_credential_health(session, connection, now)
        if connection.status not in _SCHEDULABLE_STATUSES:
            continue
        due = _due_connection_scopes(connection, now)
        if not due:
            continue
        as_of = now.date()
        enqueued.append(
            job_queue.enqueue(
                session,
                organization_id,
                MARKET_DATA_PULL,
                bank_id=connection.bank_id,
                payload={
                    "connection_id": str(connection.id),
                    "scopes": [scope.value for scope in due],
                    "as_of_date": as_of.isoformat(),
                },
                coalesce_key=f"md_pull:{connection.id}:{as_of.isoformat()}",
            )
        )
    session.flush()
    return enqueued


# ---------------------------------------------------------------------------
# Scheduling internals
# ---------------------------------------------------------------------------


def _apply_credential_health(
    session: Session, connection: MarketDataConnection, now: datetime
) -> None:
    """Persist expiry-derived state transitions (§10.3 cheap health check).

    Only the expiry-driven ACTIVE → EXPIRING_SOON → EXPIRED path is derived
    here; INVALID/REVOKED are set by validation checks and pull failures, and
    operator-driven states (TESTING, DISABLED, ...) are never overridden.
    """
    if connection.status not in _SCHEDULABLE_STATUSES:
        return
    expires_at = connection.credential_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    derived = derive_status(expires_at, None, now)
    if derived != connection.status and derived in ("EXPIRING_SOON", "EXPIRED", "ACTIVE"):
        _transition_status(session, connection, derived, reason="credential_expiry_check")


def _transition_status(
    session: Session, connection: MarketDataConnection, new_status: str, *, reason: str
) -> None:
    """Move the connection through the §10.2 state machine, audited."""
    old_status = connection.status
    connection.status = new_status
    record_event(
        session,
        TenantContext(organization_id=connection.organization_id),
        event_type="market_data_connection.status_changed",
        entity_type="market_data_connection",
        entity_id=connection.id,
        details={
            "vendor": connection.vendor,
            "from": old_status,
            "to": new_status,
            "reason": reason,
        },
    )
    session.flush()


def _due_connection_scopes(connection: MarketDataConnection, now: datetime) -> list[DataScope]:
    """The connection's scopes whose next §9.2 schedule slot has arrived.

    ``connection.schedule`` maps scope names — or scope-category names — to
    ``PullFrequency`` values; scopes without an entry fall back to their
    category default (§9.2 step 6). ``last_pull_at`` is connection-level, so
    every scope shares it.
    """
    schedule: dict[DataScope | str, PullFrequency | str] = {}
    raw_schedule = connection.schedule or {}
    for scope_name in connection.scopes:
        try:
            scope = DataScope[scope_name]
        except KeyError:
            logger.warning(
                "Connection %s carries unknown scope %r; skipped.", connection.id, scope_name
            )
            continue
        category = category_of(scope)
        raw = raw_schedule.get(scope.value, raw_schedule.get(category.value))
        frequency = (
            PullFrequency[str(raw)] if raw is not None else DEFAULT_FREQUENCY_BY_CATEGORY[category]
        )
        schedule[scope] = frequency
    if not schedule:
        return []
    last_pull_at = connection.last_pull_at
    if last_pull_at is not None and last_pull_at.tzinfo is None:
        last_pull_at = last_pull_at.replace(tzinfo=now.tzinfo)
    last_pull_map: dict[DataScope | str, datetime | None] = dict.fromkeys(schedule, last_pull_at)
    return due_scopes(schedule, last_pull_map, now)


# ---------------------------------------------------------------------------
# Pull internals
# ---------------------------------------------------------------------------


def _connection_or_error(session: Session, job: Job) -> MarketDataConnection:
    raw_id = job.payload.get("connection_id")
    if not raw_id:
        msg = f"Job {job.id} payload carries no connection_id."
        raise MarketDataJobError(msg)
    connection = session.scalar(
        select(MarketDataConnection).where(
            MarketDataConnection.id == UUID(str(raw_id)),
            MarketDataConnection.organization_id == job.organization_id,
        )
    )
    if connection is None:
        msg = f"Job {job.id} references unknown market data connection {raw_id}."
        raise MarketDataJobError(msg)
    return connection


def _bank_or_error(session: Session, organization_id: UUID, bank_id: UUID) -> Bank:
    bank = session.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        msg = f"Market data connection references unknown bank {bank_id}."
        raise MarketDataJobError(msg)
    return bank


def _scopes_from_payload(job: Job, connection: MarketDataConnection) -> list[DataScope]:
    names = job.payload.get("scopes") or connection.scopes
    scopes: list[DataScope] = []
    for name in names:
        try:
            scopes.append(DataScope[str(name)])
        except KeyError as exc:
            msg = f"Job {job.id} requests unknown market data scope {name!r}."
            raise MarketDataJobError(msg) from exc
    if not scopes:
        msg = f"Job {job.id} resolves to zero pull scopes."
        raise MarketDataJobError(msg)
    return scopes


def _as_of_from_payload(job: Job) -> date:
    raw = job.payload.get("as_of_date")
    return date.fromisoformat(str(raw)) if raw else utc_now().date()


def _retrieve_credentials(
    session: Session, connection: MarketDataConnection, bank: Bank
) -> CredentialSet:
    """Decrypt the connection's credentials for exactly one pull cycle (§15).

    Manual-upload connections authenticate nothing and hold no ciphertext;
    they get an empty credential set (they are also never scheduled — this
    path only serves explicit on-demand jobs).
    """
    if connection.vendor == "manual_upload":
        return CredentialSet(
            institution_id=str(bank.id),
            vendor=connection.vendor,
            credentials={},
            issued_at=utc_now(),
            expires_at=None,
        )
    vault = EncryptedDbVault(session)
    return vault.retrieve(
        organization_id=connection.organization_id,
        bank_id=connection.bank_id,
        vendor=connection.vendor,
    )
