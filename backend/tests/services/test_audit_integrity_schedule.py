from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
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

    def verify(
        _task: audit_integrity.AuditIntegrityTask,
        new_session: Callable[[], AbstractContextManager[Session]],
        _now: datetime,
    ) -> list[ChainVerification]:
        with new_session() as session:
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
    monkeypatch.setattr(audit_integrity.AuditIntegrityTask, "run", verify)
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


@dataclass(frozen=True)
class TaskPoll:
    failure: str | None = None
    verification: str = "healthy"
    due: bool = True


@pytest.fixture
def run_audit_polls(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Callable[[list[TaskPoll]], list[tuple[str, ...]]]:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    stop = threading.Event()
    polls: list[tuple[str, ...]] = []
    attempts: list[TaskPoll] = []

    def fail_at(stage: str) -> None:
        if attempts[len(polls)].failure == stage:
            raise RuntimeError("private database failure detail")

    def acquire(*args: object, **kwargs: object) -> bool | None:
        fail_at("acquisition")
        return True if attempts[len(polls)].due else None

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
        outcome = attempts[len(polls)].verification
        if outcome == "unavailable":
            raise RuntimeError("private verification failure detail")
        return [
            ChainVerification(stream, 0, outcome not in {stream, "both"})
            for stream in audit_integrity.STREAMS
        ]

    def poll(types: tuple[str, ...], *, worker_id: str) -> bool:
        polls.append(types)
        if len(polls) == len(attempts):
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

    def run(sequence: list[TaskPoll]) -> list[tuple[str, ...]]:
        attempts.extend(sequence)
        worker.run_worker(job_types=("scheduled_tick",), stop_event=stop)
        return polls

    return run


@pytest.mark.parametrize(
    "failure",
    [None, "acquisition", "advance", "commit", "session_creation", "session_entry", "session_exit"],
)
@pytest.mark.parametrize("verification", ["healthy", "unavailable", "audit_events"])
def test_repeated_platform_failures_page_once_and_preserve_job_dispatch(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    failure: str | None,
    verification: str,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    sequence = [TaskPoll(failure, verification)] * 3
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    conditions, pages = paging_evidence
    verified = failure in {None, "advance", "commit", "session_exit"}
    if verified and verification == "audit_events":
        assert pages == [("chain_mismatch", "audit_events")]
        assert conditions == [
            (Condition.AUDIT_CHAIN_BROKEN, {"severity": "error", "stream": "audit_events"})
        ]
    elif failure is not None or verification == "unavailable":
        assert pages == [("verification_unavailable", None)]
        assert conditions == [
            (
                Condition.AUDIT_CHAIN_BROKEN,
                {"severity": "error", "reason": "verification_unavailable"},
            )
        ]
    else:
        assert paging_evidence == ([], [])


@pytest.mark.parametrize(
    "failure",
    [None, "acquisition", "advance", "commit", "session_creation", "session_entry", "session_exit"],
)
@pytest.mark.parametrize("verification", ["unavailable", "audit_events"])
def test_only_completed_healthy_sweep_resets_episode(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    failure: str | None,
    verification: str,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    outage = TaskPoll(failure, verification)
    sequence = [
        outage,
        TaskPoll(due=False),
        outage,
        TaskPoll("commit"),
        outage,
        TaskPoll("session_exit"),
        outage,
        TaskPoll(),
        outage,
        outage,
    ]
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    _, pages = paging_evidence
    page = (
        ("chain_mismatch", "audit_events")
        if verification == "audit_events" and failure in {None, "advance", "commit", "session_exit"}
        else ("verification_unavailable", None)
    )
    assert pages == [page, page]


def test_episode_escalates_to_each_new_broken_stream(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    sequence = [
        TaskPoll("acquisition"),
        TaskPoll("acquisition"),
        TaskPoll("commit", "audit_events"),
        TaskPoll("commit", "audit_events"),
        TaskPoll(verification="unavailable"),
        TaskPoll("session_exit", "both"),
        TaskPoll(verification="both"),
        TaskPoll(),
        TaskPoll(verification="both"),
    ]
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    _, pages = paging_evidence
    assert pages == [
        ("verification_unavailable", None),
        ("chain_mismatch", "audit_events"),
        ("chain_mismatch", "operator_audit_log"),
        ("chain_mismatch", "audit_events"),
        ("chain_mismatch", "operator_audit_log"),
    ]


@pytest.mark.parametrize("verification", ["unavailable", "audit_events"])
def test_failed_publication_does_not_restart_episode(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    monkeypatch: pytest.MonkeyPatch,
    verification: str,
) -> None:
    attempts: list[tuple[str, str | None]] = []

    def page(reason: str, stream: str | None = None) -> bool:
        attempts.append((reason, stream))
        return False

    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    outage = TaskPoll("commit", verification)
    sequence = [outage, outage, TaskPoll(), outage, outage]
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    expected = (
        ("chain_mismatch", "audit_events")
        if verification == "audit_events"
        else ("verification_unavailable", None)
    )
    assert attempts == [expected, expected]
