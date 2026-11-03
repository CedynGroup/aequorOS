"""Worker dispatch wiring that does not require a live database."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app import worker
from app.db import session as session_module
from app.models import Job, WorkerHeartbeat
from app.services import job_queue
from tests.support.database import managed_session_factory
from tests.support.helpers import ORG_1


@pytest.mark.parametrize("job_type", ["reporting_deadline_scan", "bi_mart_refresh"])
def test_run_once_persists_runtime_identity_and_handler_completion(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    job_type: str,
    request: pytest.FixtureRequest,
) -> None:
    factory = managed_session_factory(
        request,
        db_session.get_bind(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(worker, "get_worker_sessionmaker", lambda: factory)
    job = job_queue.enqueue(db_session, ORG_1, job_type)
    db_session.commit()
    assert worker.run_once((job_type,), worker_id="risk-worker:test-blue") is True
    db_session.expire_all()
    persisted = db_session.get(Job, job.id)
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.claimed_by == "risk-worker:test-blue"
    assert persisted.completed_at is not None
    assert worker.run_once((job_type,), worker_id="risk-worker:test-blue") is False


def test_production_worker_requires_dedicated_database_url(monkeypatch: Any) -> None:
    settings = SimpleNamespace(
        app=SimpleNamespace(app_env="production"),
        worker=SimpleNamespace(worker_database_url=None),
        database=SimpleNamespace(database_url="postgresql://tenant-role"),
    )
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="WORKER_DATABASE_URL"):
        session_module.get_worker_sessionmaker()


def test_inprocess_worker_refuses_to_start_when_it_cannot_claim(monkeypatch: Any) -> None:
    """Raising inside the daemon thread would be as silent as the bug itself."""
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(worker=SimpleNamespace(run_inprocess_worker=True)),
    )
    monkeypatch.setattr(
        worker,
        "assert_worker_database_access",
        lambda: (_ for _ in ()).throw(session_module.RlsBlindError("blind")),
    )
    started: list[str] = []
    monkeypatch.setattr(
        worker.threading,
        "Thread",
        lambda **_kwargs: started.append("thread") or SimpleNamespace(start=lambda: None),
    )

    with pytest.raises(session_module.RlsBlindError):
        worker.start_inprocess_worker()

    assert started == []


def test_inprocess_worker_never_selects_the_bi_lane(monkeypatch: Any) -> None:
    """The API process is pinned to the core lane whatever WORKER_JOB_TYPES says.

    A request handler's neighbour thread must never stream a bank's book into
    a mart, so the pin that keeps AI work out of the API keeps BI work out too.
    """
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(worker=SimpleNamespace(run_inprocess_worker=True)),
    )
    monkeypatch.setattr(worker, "assert_worker_database_access", lambda: None)
    captured: dict[str, Any] = {}

    def fake_thread(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(worker.threading, "Thread", fake_thread)

    worker.start_inprocess_worker()

    selected = captured["kwargs"]["job_types"]
    assert "bi_mart_refresh" not in selected
    assert "bi_mart_backfill" not in selected
    assert "bi_retention" not in selected
    assert "pipeline_refresh" in selected


def test_heartbeat_records_work_and_error(db_session: Any) -> None:
    worker._record_heartbeat(db_session, "risk-worker:test", worked=True)
    worker._record_heartbeat(db_session, "risk-worker:test", error=RuntimeError("fixture failure"))

    heartbeat = db_session.get(WorkerHeartbeat, "risk-worker:test")

    assert heartbeat is not None
    assert heartbeat.last_job_at is not None
    assert heartbeat.last_error == "fixture failure"
