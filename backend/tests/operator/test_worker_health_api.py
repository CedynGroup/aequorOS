"""Authenticated operator worker-heartbeat evidence."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models import WorkerHeartbeat
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/worker-health"


def test_requires_authentication(operator_client: TestClient) -> None:
    assert operator_client.get(BASE).status_code == 401


def test_reports_stale_heartbeats_using_the_governed_threshold(
    operator_client: TestClient,
    operator_db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("WORKER_HEARTBEAT_STALE_SECONDS", "30")
    get_settings.cache_clear()
    now = utc_now()
    operator_db.add_all(
        [
            WorkerHeartbeat(
                worker_id="risk-worker:healthy",
                started_at=now - timedelta(minutes=2),
                last_seen_at=now - timedelta(seconds=29),
                last_job_at=now - timedelta(seconds=20),
            ),
            WorkerHeartbeat(
                worker_id="risk-worker:stale",
                started_at=now - timedelta(minutes=2),
                last_seen_at=now - timedelta(seconds=31),
                last_error_at=now - timedelta(seconds=30),
                last_error="connection reset",
            ),
        ]
    )
    operator_db.commit()

    response = operator_client.get(BASE, headers=operator_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert body["stale_after_seconds"] == 30
    workers = {worker["worker_id"]: worker for worker in body["workers"]}
    assert workers["risk-worker:healthy"]["status"] == "healthy"
    assert workers["risk-worker:stale"]["status"] == "stale"
    assert workers["risk-worker:stale"]["last_error"] == "connection reset"