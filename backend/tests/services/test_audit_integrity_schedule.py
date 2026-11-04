from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

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
    scalar_calls = recovery_epoch = pending_epoch = 0

    def fail_at(stage: str) -> None:
        if attempts[len(polls)].failure == stage:
            raise RuntimeError("private database failure detail")

    def scalar(
        _statement: object, parameters: dict[str, object] | None = None
    ) -> int | bool | None:
        nonlocal scalar_calls, pending_epoch
        scalar_calls += 1
        if scalar_calls == 1:
            fail_at("recovery")
            return recovery_epoch
        if scalar_calls == 2:
            fail_at("acquisition")
            return True if attempts[len(polls)].due else None
        fail_at("advance")
        assert parameters is not None
        pending_epoch = recovery_epoch + cast(int, parameters["healthy"])
        return pending_epoch

    def commit() -> None:
        nonlocal recovery_epoch
        fail_at("commit")
        recovery_epoch = pending_epoch

    @contextmanager
    def session_context() -> Generator[Session]:
        fail_at("session_entry")
        try:
            yield db_session
        finally:
            fail_at("session_exit")

    def new_session() -> AbstractContextManager[Session]:
        nonlocal scalar_calls
        scalar_calls = 0
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

    def legacy_access(_session: Session, _signature: str) -> bool:
        # These polls simulate direct schedule access, before the worker function rollout.
        return False

    for target, name, replacement in (
        (db_session, "scalar", scalar),
        (db_session, "commit", commit),
        (audit_integrity, "verify_chains", verify),
        (audit_integrity, "has_worker_function", legacy_access),
        (worker, "assert_worker_database_access", ignore),
        (worker, "_heartbeat_safely", ignore),
        (worker, "_reap_stale", ignore),
        (worker, "_new_session", new_session),
        (worker, "run_once", poll),
    ):
        monkeypatch.setattr(target, name, replacement)

    def run(sequence: list[TaskPoll]) -> list[tuple[str, ...]]:
        attempts.extend(sequence)
        worker.run_worker(job_types=("scheduled_tick",), stop_event=stop)
        return polls

    return run


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "recovery",
        "acquisition",
        "advance",
        "commit",
        "session_creation",
        "session_entry",
        "session_exit",
    ],
)
@pytest.mark.parametrize("verification", ["healthy", "unavailable", "audit_events"])
def test_repeated_platform_failures_page_once_and_preserve_job_dispatch(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    failure: str | None,
    verification: str,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    outage = TaskPoll(failure, verification)
    repeat = TaskPoll(failure, verification, due=False) if failure == "session_exit" else outage
    sequence = [outage, repeat, repeat]
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    conditions, pages = paging_evidence
    verified = failure in {None, "session_exit"}
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
    [
        None,
        "recovery",
        "acquisition",
        "advance",
        "commit",
        "session_creation",
        "session_entry",
        "session_exit",
    ],
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
        TaskPoll(),
        outage,
        outage,
    ]
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    _, pages = paging_evidence
    page = (
        ("chain_mismatch", "audit_events")
        if verification == "audit_events" and failure in {None, "session_exit"}
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
        TaskPoll(verification="audit_events"),
        TaskPoll(verification="audit_events"),
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


@pytest.mark.parametrize(
    ("verification", "failure"),
    [("unavailable", None), ("healthy", "acquisition"), ("audit_events", None)],
)
def test_failed_publication_retries_at_bounded_rate_until_delivered(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    monkeypatch: pytest.MonkeyPatch,
    verification: str,
    failure: str | None,
) -> None:
    attempts: list[tuple[str, str | None]] = []
    ticks = iter([0.0, 2.0, 299.0, 300.0])

    def page(reason: str, stream: str | None = None) -> bool:
        attempts.append((reason, stream))
        return len(attempts) == 2

    monkeypatch.setattr(audit_integrity, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    sequence = [TaskPoll(failure, verification)] * 6
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    expected = (
        ("chain_mismatch", "audit_events")
        if verification == "audit_events"
        else ("verification_unavailable", None)
    )
    assert attempts == [expected, expected]


@pytest.mark.parametrize("recovering_worker", [0, 1])
def test_peer_observes_shared_recovery_before_later_outage(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    recovering_worker: int,
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    workers = [audit_integrity.AuditIntegrityTask(), audit_integrity.AuditIntegrityTask()]
    epoch = 0
    pending_epoch = 0
    due = True
    unavailable = True
    scalar_calls = 0
    verifications: list[Session] = []
    now = datetime(2026, 10, 10, 12, 15, tzinfo=UTC)

    def scalar(
        _statement: object, parameters: dict[str, object] | None = None
    ) -> int | bool | None:
        nonlocal scalar_calls, pending_epoch
        if unavailable:
            raise RuntimeError("database unavailable")
        scalar_calls += 1
        if scalar_calls == 1:
            return epoch
        if scalar_calls == 2:
            return True if due else None
        assert parameters is not None
        pending_epoch = epoch + cast(int, parameters["healthy"])
        return pending_epoch

    def commit() -> None:
        nonlocal epoch, due
        epoch = pending_epoch
        due = False

    def verify(session: Session) -> list[ChainVerification]:
        verifications.append(session)
        return [ChainVerification(stream, 0, True) for stream in audit_integrity.STREAMS]

    def new_session() -> AbstractContextManager[Session]:
        nonlocal scalar_calls
        scalar_calls = 0
        return nullcontext(db_session)

    def legacy_access(_session: Session, _signature: str) -> bool:
        return False

    monkeypatch.setattr(db_session, "scalar", scalar)
    monkeypatch.setattr(db_session, "commit", commit)
    monkeypatch.setattr(audit_integrity, "verify_chains", verify)
    monkeypatch.setattr(audit_integrity, "has_worker_function", legacy_access)
    for _ in range(3):
        for task in workers:
            task.run(new_session, now)
    _, pages = paging_evidence
    assert pages == [("verification_unavailable", None)] * 2
    unavailable = False
    assert workers[recovering_worker].run(new_session, now)
    peer = workers[1 - recovering_worker]
    assert peer.run(new_session, now) is None
    assert verifications == [db_session]
    unavailable = True
    for _ in range(3):
        peer.run(new_session, now + timedelta(hours=1))
    assert pages == [("verification_unavailable", None)] * 3


def test_failed_commit_pages_unavailable_then_committed_mismatch_once(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    paging_evidence: tuple[list[tuple[Condition, dict[str, object]]], list[tuple[str, str | None]]],
) -> None:
    sequence = [TaskPoll("commit", "audit_events")] * 3 + [
        TaskPoll(verification="audit_events")
    ] * 3
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    _, pages = paging_evidence
    assert pages == [("verification_unavailable", None), ("chain_mismatch", "audit_events")]


@pytest.mark.parametrize("failed_stream", audit_integrity.STREAMS)
def test_failed_stream_retry_preserves_successful_sibling_page(
    run_audit_polls: Callable[[list[TaskPoll]], list[tuple[str, ...]]],
    monkeypatch: pytest.MonkeyPatch,
    failed_stream: str,
) -> None:
    attempts: list[str | None] = []
    delivered: list[str | None] = []
    ticks = iter([0.0, 0.0, 2.0, 300.0])

    def page(reason: str, stream: str | None = None) -> bool:
        attempts.append(stream)
        success = stream != failed_stream or attempts.count(stream) == 2
        if success:
            delivered.append(stream)
        return success

    monkeypatch.setattr(audit_integrity, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    sequence = [TaskPoll(verification="both")] * 5
    assert run_audit_polls(sequence) == [("scheduled_tick",)] * len(sequence)
    assert attempts == [*sorted(audit_integrity.STREAMS), failed_stream]
    assert set(delivered) == set(audit_integrity.STREAMS)
    assert len(delivered) == len(audit_integrity.STREAMS)
