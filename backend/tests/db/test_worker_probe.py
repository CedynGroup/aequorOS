"""The worker container's health probe: the same two questions, cheap to start."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import Engine, Table, insert
from sqlalchemy.engine import Connection

from app.db import worker_probe
from app.live.models.live import WorkerHeartbeat
from tests.support.database import TestDatabaseConnections

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(seconds=900)


def test_the_probe_does_not_load_the_job_handlers() -> None:
    """Docker starts a fresh interpreter per probe; ``app.worker`` cost ~9 CPU-seconds."""
    script = (
        "import sys, app.db.worker_probe; "
        "print(*sorted(m for m in sys.modules if m.startswith('app.')), sep='\\n')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    loaded = set(result.stdout.split())
    assert "app.db.worker_probe" in loaded
    outside_kernel = sorted(m for m in loaded if m.split(".")[1] not in {"core", "db"})
    assert outside_kernel == []


@pytest.fixture
def heartbeats() -> Iterator[Engine]:
    with TestDatabaseConnections("sqlite://") as db:
        engine = db.engine()
        cast(Table, WorkerHeartbeat.__table__).create(engine)
        with engine.begin() as connection:
            _ = connection.execute(
                insert(WorkerHeartbeat),
                [
                    {
                        "worker_id": "risk-worker",
                        "started_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW - timedelta(minutes=1),
                    },
                    {
                        "worker_id": "risk-worker-bi",
                        "started_at": NOW - timedelta(hours=1),
                        "last_seen_at": NOW,
                    },
                ],
            )
        yield engine


def _aware(value: datetime | None) -> datetime | None:
    # SQLite keeps no zone; PostgreSQL returns the stored UTC instant.
    return None if value is None else value.replace(tzinfo=UTC)


def test_a_pinned_worker_reads_only_its_own_heartbeat(heartbeats: Engine) -> None:
    with heartbeats.connect() as connection:
        own = worker_probe.latest_heartbeat(connection, "risk-worker")
        absent = worker_probe.latest_heartbeat(connection, "risk-worker-ai")
    assert _aware(own) == NOW - timedelta(minutes=1)
    assert absent is None, "a peer's heartbeat must not answer for this worker"


def test_an_unpinned_worker_reads_the_fleets_latest(heartbeats: Engine) -> None:
    with heartbeats.connect() as connection:
        assert _aware(worker_probe.latest_heartbeat(connection, None)) == NOW


def test_a_missing_heartbeat_fails() -> None:
    with pytest.raises(RuntimeError, match="No heartbeat"):
        worker_probe.require_fresh_heartbeat(None, stale_after=STALE_AFTER, now=NOW)


def test_a_stale_heartbeat_fails_and_says_how_old() -> None:
    with pytest.raises(RuntimeError, match=r"901s old \(limit 900s\)"):
        worker_probe.require_fresh_heartbeat(
            NOW - timedelta(seconds=901), stale_after=STALE_AFTER, now=NOW
        )


def test_a_heartbeat_at_the_limit_passes() -> None:
    worker_probe.require_fresh_heartbeat(NOW - STALE_AFTER, stale_after=STALE_AFTER, now=NOW)


def test_the_probe_checks_claim_access_then_this_workers_heartbeat(
    heartbeats: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setenv("WORKER_ID", "risk-worker")
    monkeypatch.setenv("WORKER_STALE_JOB_SECONDS", "900")
    monkeypatch.setattr(
        worker_probe, "assert_worker_database_access", lambda: calls.append("access")
    )
    # The probe reads only the worker engine from the sessionmaker.
    worker_sessionmaker = SimpleNamespace(kw={"bind": heartbeats})
    monkeypatch.setattr(worker_probe, "get_worker_sessionmaker", lambda: worker_sessionmaker)
    monkeypatch.setattr(worker_probe, "utc_now", lambda: NOW)

    def latest(connection: Connection, worker_id: str | None) -> datetime:
        calls.append((connection.engine is heartbeats, worker_id))
        return NOW - timedelta(seconds=901)

    monkeypatch.setattr(worker_probe, "latest_heartbeat", latest)

    with pytest.raises(RuntimeError, match="901s old"):
        worker_probe.healthcheck()
    assert calls == ["access", (True, "risk-worker")]
