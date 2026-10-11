from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import worker
from app.core import audit_integrity
from app.core.audit_integrity import ChainVerification, scheduled_verification
from app.core.config import get_settings
from app.core.observability import Condition
from app.db.base import utc_now
from app.models import Job
from app.services import job_queue, scheduler
from tests.support.helpers import ORG_1


def test_audit_flag_does_not_keep_tenant_ticks_alive(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    assert not scheduler.any_scheduling_enabled(get_settings())
    job_queue.enqueue(db_session, ORG_1, "scheduled_tick")
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None
    scheduler.run_tick(db_session, tick)
    assert tick.progress == {"status": "inert", "reason": "scheduling_disabled"}
    assert db_session.scalar(select(Job).where(Job.status == "queued")) is None


@pytest.mark.parametrize("job_types", [("scheduled_tick",), ("icaap_ai_draft",), ("bi_retention",)])
def test_worker_starts_platform_schedule_without_tenant_ticks(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, job_types: tuple[str, ...]
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    stop = threading.Event()
    calls: list[Session] = []

    def verify(session: Session, _now: datetime) -> list[ChainVerification]:
        calls.append(session)
        return [ChainVerification("audit_events", 0, True)]

    def poll(_types: tuple[str, ...], *, worker_id: str) -> bool:
        stop.set()
        return True

    monkeypatch.setattr(worker, "assert_worker_database_access", lambda: None)

    def ignore(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(worker, "_heartbeat_safely", ignore)
    monkeypatch.setattr(worker, "_reap_stale", ignore)
    monkeypatch.setattr(worker, "_new_session", lambda: nullcontext(db_session))
    monkeypatch.setattr(worker, "run_once", poll)
    monkeypatch.setattr(audit_integrity, "run_due_verification", verify)
    worker.run_worker(job_types=job_types, stop_event=stop)
    assert calls == ([db_session] if "scheduled_tick" in job_types else [])


@pytest.fixture
def paging_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]]:
    conditions: list[tuple[Condition, dict[str, object]]] = []
    pages: list[tuple[str, str | None]] = []

    def capture(condition: Condition, _message: str, **fields: object) -> None:
        conditions.append((condition, fields))

    def page(reason: str, stream: str | None = None) -> bool:
        pages.append((reason, stream))
        return True

    monkeypatch.setattr(audit_integrity, "emit", capture)
    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    return conditions, pages


def test_unavailable_verification_returns_failed_outcome(
    db_session: Session,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    assert scheduled_verification(db_session) == [
        ChainVerification("verification_unavailable", 0, False)
    ]
    assert paging_evidence == ([], [])


@pytest.mark.parametrize(
    "failure",
    [None, "acquisition", "advance", "commit", "session_creation", "session_entry", "session_exit"],
)
@pytest.mark.parametrize("verifier_unavailable", [False, True])
def test_platform_task_failure_pages_and_preserves_job_dispatch(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
    verifier_unavailable: bool,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    stop = threading.Event()
    conditions, pages = paging_evidence
    polls: list[tuple[str, ...]] = []
    stages: list[str] = []

    def fail_at(stage: str) -> None:
        stages.append(stage)
        if failure == stage:
            stop.set()
            raise RuntimeError("private database failure detail")

    def acquire(*args: object, **kwargs: object) -> bool:
        fail_at("acquisition")
        return True

    def advance(*args: object, **kwargs: object) -> None:
        fail_at("advance")

    def commit() -> None:
        fail_at("commit")

    @contextmanager
    def session_context() -> Generator[Session]:
        fail_at("session_entry")
        try:
            yield db_session
        finally:
            fail_at("session_exit")

    def new_session() -> AbstractContextManager[Session]:
        fail_at("session_creation")
        return session_context()

    def verify(_session: Session) -> list[ChainVerification]:
        stages.append("verification")
        if verifier_unavailable:
            stop.set()
            raise RuntimeError("private verification failure detail")
        return [ChainVerification("audit_events", 0, True)]

    def poll(types: tuple[str, ...], *, worker_id: str) -> bool:
        polls.append(types)
        stop.set()
        return True

    def ignore(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(db_session, "scalar", acquire)
    monkeypatch.setattr(db_session, "execute", advance)
    monkeypatch.setattr(db_session, "commit", commit)
    monkeypatch.setattr(audit_integrity, "verify_chains", verify)
    monkeypatch.setattr(worker, "assert_worker_database_access", ignore)
    monkeypatch.setattr(worker, "_heartbeat_safely", ignore)
    monkeypatch.setattr(worker, "_reap_stale", ignore)
    monkeypatch.setattr(worker, "_new_session", new_session)
    monkeypatch.setattr(worker, "run_once", poll)
    worker.run_worker(job_types=("scheduled_tick",), stop_event=stop)
    assert polls == [("scheduled_tick",)]
    assert conditions == (
        [
            (
                Condition.AUDIT_CHAIN_BROKEN,
                {"severity": "error", "reason": "verification_unavailable"},
            )
        ]
        if failure is not None or verifier_unavailable
        else []
    )
    assert pages == ([("verification_unavailable", None)] if conditions else [])
    assert ("verification" in stages) == (failure in {None, "advance", "commit", "session_exit"})
