"""Worker dispatch wiring that does not require a live database.

This module's other-suite cases live in ``test_worker.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app import worker
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


def test_heartbeat_records_work_and_error(db_session: Any) -> None:
    worker._record_heartbeat(db_session, "risk-worker:test", worked=True)
    worker._record_heartbeat(db_session, "risk-worker:test", error=RuntimeError("fixture failure"))

    heartbeat = db_session.get(WorkerHeartbeat, "risk-worker:test")

    assert heartbeat is not None
    assert heartbeat.last_job_at is not None
    assert heartbeat.last_error == "fixture failure"
