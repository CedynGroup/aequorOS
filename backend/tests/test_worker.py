"""Worker dispatch wiring that does not require a live database."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app import worker
from app.db import session as session_module
from app.models import WorkerHeartbeat


def test_run_once_passes_runtime_identity_to_job_claim(monkeypatch: Any) -> None:
    claimed_by: list[str | None] = []

    def claim_next(_db: object, _now: object, _types: object, *, claimed_by: str | None) -> None:
        claimed_by_values.append(claimed_by)

    claimed_by_values = claimed_by
    monkeypatch.setattr(worker, "_new_session", lambda *_args: nullcontext(object()))
    monkeypatch.setattr(worker.job_queue, "claim_next", claim_next)

    assert worker.run_once(("pipeline_refresh",), worker_id="risk-worker:test-blue") is False
    assert claimed_by_values == ["risk-worker:test-blue"]


def test_run_once_binds_the_claimed_tenant_before_invoking_a_handler(
    monkeypatch: Any,
) -> None:
    job = SimpleNamespace(
        id=uuid4(), organization_id="OR-TEST0001", job_type="pipeline_refresh"
    )

    class FakeSession:
        def __init__(self, claimed: object | None = None) -> None:
            self.claimed = claimed
            self.info: dict[str, str] = {}

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _model: object, _id: object) -> object | None:
            return self.claimed

        def rollback(self) -> None:
            return None

    claim_session = FakeSession()
    handler_session = FakeSession(job)
    sessions = iter((claim_session, handler_session))
    monkeypatch.setattr(worker, "get_worker_sessionmaker", lambda: lambda: next(sessions))
    monkeypatch.setattr(worker.job_queue, "claim_next", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(worker.job_queue, "complete", lambda *_args, **_kwargs: None)
    seen: list[tuple[dict[str, str], object]] = []

    def handler(session: Any, claimed_job: object) -> None:
        seen.append((dict(session.info), claimed_job))

    monkeypatch.setitem(worker.HANDLERS, "pipeline_refresh", handler)

    assert worker.run_once(("pipeline_refresh",), worker_id="risk-worker:test-blue") is True
    assert seen == [({"organization_id": "OR-TEST0001"}, job)]


def test_production_worker_requires_dedicated_database_url(monkeypatch: Any) -> None:
    settings = SimpleNamespace(
        app=SimpleNamespace(app_env="production"),
        worker=SimpleNamespace(worker_database_url=None),
        database=SimpleNamespace(database_url="postgresql://tenant-role"),
    )
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="WORKER_DATABASE_URL"):
        session_module.get_worker_sessionmaker()


class _FakeConnection:
    """A Postgres connection whose role does not bypass row-level security."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, *, bypasses_rls: bool, forced_tables: tuple[str, ...]) -> None:
        self._bypasses_rls = bypasses_rls
        self._forced_tables = forced_tables

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalar(self, statement: object) -> object:
        if "current_user" in str(statement) and "rolbypassrls" not in str(statement):
            return "tenant_role"
        return self._bypasses_rls

    def execute(self, _statement: object, _params: object = None) -> Any:
        return SimpleNamespace(scalars=lambda: self._forced_tables)


def _fake_worker_engine(*, bypasses_rls: bool, forced_tables: tuple[str, ...]) -> Any:
    class Engine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self) -> _FakeConnection:
            return _FakeConnection(bypasses_rls=bypasses_rls, forced_tables=forced_tables)

    return SimpleNamespace(kw={"bind": Engine()})


def test_worker_rejects_postgres_role_without_bypassrls(monkeypatch: Any) -> None:
    """The silent-zero condition: FORCE-RLS `jobs` + a role that cannot see it."""
    monkeypatch.setattr(
        session_module,
        "get_worker_sessionmaker",
        lambda: _fake_worker_engine(bypasses_rls=False, forced_tables=("jobs",)),
    )

    with pytest.raises(session_module.RlsBlindError, match="BYPASSRLS"):
        session_module.assert_worker_database_access()


def test_worker_accepts_a_role_that_bypasses_rls(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        session_module,
        "get_worker_sessionmaker",
        lambda: _fake_worker_engine(bypasses_rls=True, forced_tables=("jobs",)),
    )

    session_module.assert_worker_database_access()

    assert session_module.worker_visibility().can_claim is True


def test_worker_accepts_a_tenant_role_when_jobs_does_not_force_rls(monkeypatch: Any) -> None:
    """Not every deployment forces RLS on `jobs`; refusing those would be wrong."""
    monkeypatch.setattr(
        session_module,
        "get_worker_sessionmaker",
        lambda: _fake_worker_engine(bypasses_rls=False, forced_tables=()),
    )

    session_module.assert_worker_database_access()


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


def test_heartbeat_records_work_and_error(db_session: Any) -> None:
    worker._record_heartbeat(db_session, "risk-worker:test", worked=True)
    worker._record_heartbeat(db_session, "risk-worker:test", error=RuntimeError("fixture failure"))

    heartbeat = db_session.get(WorkerHeartbeat, "risk-worker:test")

    assert heartbeat is not None
    assert heartbeat.last_job_at is not None
    assert heartbeat.last_error == "fixture failure"