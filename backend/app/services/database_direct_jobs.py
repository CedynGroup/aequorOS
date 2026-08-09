"""Database-Direct connection health probes: scheduling glue for the live engine.

Two halves, mirroring ``temenos_jobs`` / ``market_data_jobs``:

- :func:`run_database_direct_health` is the ``database_direct_health`` worker
  handler: it loads the org-scoped connection and runs the existing live test
  (:func:`app.services.database_connections.test_connection` — connect,
  authenticate, read the data dictionary), which stamps ``last_validated_at``,
  writes the audit event, and classifies any failure into a bank-safe error.
  Transient reachability failures complete the job with ``reachable: false``
  rather than retrying — the next day's probe is the retry, and worker backoff
  cannot wake a stopped core.

- :func:`enqueue_due_database_direct_probes` is the scheduler-tick extension:
  one coalesced probe per ACTIVE/EXPIRING_SOON connection per day. Gated on
  ``DATABASE_DIRECT_HEALTH_ENABLED`` (off by default).

Why a scheduled probe exists at all: it is connection-health monitoring for a
bank's reporting replica (a dead replica is noticed by the probe, not at the
next demo), and the login+read is genuine database activity, which keeps
idle-stopping test cores alive (OCI stops an Always Free Oracle ADB after seven
consecutive idle days).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Job
from app.models.database_connection import DatabaseDirectConnection
from app.services import database_connections, job_queue

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.adapters.database_direct.drivers.base import DatabaseDriver

DATABASE_DIRECT_HEALTH = "database_direct_health"

# Statuses worth probing: EXPIRING_SOON credentials still authenticate, so the
# probe keeps watching while the bank rotates them.
_PROBEABLE_STATUSES = ("ACTIVE", "EXPIRING_SOON")

# Daily cadence with slack for tick jitter (same spacing as EOD Temenos pulls);
# the date-scoped coalesce key caps it at one probe per connection per day even
# when a failed probe leaves ``last_validated_at`` untouched.
_PROBE_INTERVAL = timedelta(hours=20)


class DatabaseDirectJobError(Exception):
    """A probe job could not run (missing connection or payload)."""


def resolve_driver(connection: DatabaseDirectConnection) -> DatabaseDriver | None:
    """Driver override seam for tests; None selects the live driver."""
    _ = connection
    return None


def run_database_direct_health(session: Session, job: Job) -> None:
    """Worker handler: run one live connection test for a connection.

    Payload: ``{"connection_id": ...}``.
    """
    connection = _connection_or_error(session, job)
    if connection.status not in _PROBEABLE_STATUSES:
        job.progress = {
            "connection_id": str(connection.id),
            "status": "skipped",
            "reason": f"connection status {connection.status} is not probeable",
        }
        return
    ctx = TenantContext(
        organization_id=connection.organization_id, actor_user_id=connection.created_by
    )
    try:
        result = database_connections.test_connection(
            session,
            ctx,
            connection.bank_id,
            connection.id,
            driver=resolve_driver(connection),
        )
    except HTTPException as exc:
        # Missing credentials / unconfigured vault: retrying cannot fix either,
        # so complete the job with the failure recorded.
        job.progress = {
            "connection_id": str(connection.id),
            "status": "failed_no_retry",
            "error": str(exc.detail),
        }
        return

    job.progress = {
        "connection_id": str(connection.id),
        "backend": connection.backend,
        "reachable": result.reachable,
        **(
            {"latency_ms": result.latency_ms}
            if result.reachable
            else {"error_code": result.error_code}
        ),
    }


def enqueue_due_database_direct_probes(
    session: Session,
    organization_id: str,
    now: datetime | None = None,
) -> list[Job]:
    """Enqueue one coalesced daily probe per probeable connection.

    Inert unless ``DATABASE_DIRECT_HEALTH_ENABLED``.
    """
    if not get_settings().database_direct.database_direct_health_enabled:
        return []
    now = now or utc_now()
    connections = list(
        session.scalars(
            select(DatabaseDirectConnection).where(
                DatabaseDirectConnection.organization_id == organization_id
            )
        )
    )
    enqueued: list[Job] = []
    for connection in connections:
        if connection.status not in _PROBEABLE_STATUSES:
            continue
        if not _is_due(connection, now):
            continue
        enqueued.append(
            job_queue.enqueue(
                session,
                organization_id,
                DATABASE_DIRECT_HEALTH,
                bank_id=connection.bank_id,
                payload={"connection_id": str(connection.id)},
                coalesce_key=f"dbdirect_health:{connection.id}:{now.date().isoformat()}",
            )
        )
    session.flush()
    return enqueued


def _is_due(connection: DatabaseDirectConnection, now: datetime) -> bool:
    last = connection.last_validated_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    return (now - last) >= _PROBE_INTERVAL


def _connection_or_error(session: Session, job: Job) -> DatabaseDirectConnection:
    raw_id = job.payload.get("connection_id")
    if not raw_id:
        msg = f"Job {job.id} payload carries no connection_id."
        raise DatabaseDirectJobError(msg)
    connection = session.scalar(
        select(DatabaseDirectConnection).where(
            DatabaseDirectConnection.id == UUID(str(raw_id)),
            DatabaseDirectConnection.organization_id == job.organization_id,
        )
    )
    if connection is None:
        msg = f"Job {job.id} references unknown database-direct connection {raw_id}."
        raise DatabaseDirectJobError(msg)
    return connection
