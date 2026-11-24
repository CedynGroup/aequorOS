"""The background worker's container health probe.

Docker starts a fresh interpreter for every probe, once a minute per worker. The
probe used to live in ``app.worker``, which imports every job handler: about nine
CPU-seconds per probe on the two-core production host, so the probe overran its
timeout and marked healthy workers unhealthy. This module imports only settings
and the database session; ``tests/db/test_worker_probe.py`` keeps it that way.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import DateTime, Engine, String, column, func, select, table
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import assert_worker_database_access, get_worker_sessionmaker

#: ``worker_heartbeats`` as the probe reads it. The table has no tenant key and no
#: row-level security (``app.live.models.live.WorkerHeartbeat``), so a plain worker
#: connection reads it; naming it here keeps the model registry out of the probe.
_HEARTBEATS = table(
    "worker_heartbeats",
    column("worker_id", String(160)),
    column("last_seen_at", DateTime(timezone=True)),
)


def latest_heartbeat(connection: Connection, worker_id: str | None) -> datetime | None:
    """When this worker last recorded a heartbeat.

    A worker with a pinned identity reads only its own row. Without the filter a
    dedicated container (the AI worker) reports healthy on the strength of the
    core worker's heartbeat, and a probe that can never fail is not a probe.
    Unpinned local workers keep the fleet-wide latest.
    """
    query = select(func.max(_HEARTBEATS.c.last_seen_at))
    if worker_id is not None:
        query = query.where(_HEARTBEATS.c.worker_id == worker_id)
    return cast(datetime | None, connection.scalar(query))


def require_fresh_heartbeat(
    latest: datetime | None, *, stale_after: timedelta, now: datetime
) -> None:
    """Raise unless the heartbeat is at most ``stale_after`` old."""
    if latest is None:
        msg = "No heartbeat has been recorded for this worker."
        raise RuntimeError(msg)
    age = now - latest
    if age > stale_after:
        msg = (
            f"Worker heartbeat is {age.total_seconds():.0f}s old "
            f"(limit {stale_after.total_seconds():.0f}s)."
        )
        raise RuntimeError(msg)


def healthcheck() -> None:
    """Container health probe: can this worker claim, and is it still polling?

    Two questions, because they have different answers and only one of them was
    ever askable. ``assert_worker_database_access`` catches the configuration
    that makes ``claim_next`` match zero rows forever (audit finding P0-16);
    the heartbeat catches a loop that is wedged or dead while the process is
    still alive. Raises on failure: the compose healthcheck reads the exit code.
    """
    assert_worker_database_access()
    settings = get_settings()
    engine = cast(Engine, get_worker_sessionmaker().kw["bind"])
    with engine.connect() as connection:
        latest = latest_heartbeat(connection, settings.worker.worker_id)
    require_fresh_heartbeat(
        latest,
        stale_after=timedelta(seconds=settings.worker.worker_stale_job_seconds),
        now=utc_now(),
    )


if __name__ == "__main__":
    healthcheck()
