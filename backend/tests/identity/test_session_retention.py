"""Credential cleanup boundaries, batching and scheduled reachability."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from typing import cast
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import Engine, event, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.config import AuthSettings, get_settings
from app.identity.models.refresh_token import RefreshToken
from app.identity.service.session_retention import purge_refresh_tokens
from app.models import Job
from app.services import job_queue, scheduler
from app.worker import HANDLERS
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def _token(
    session: Session,
    *,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    organization_id: str = ORG_1,
) -> UUID:
    identifier = uuid4()
    session.add(
        RefreshToken(
            id=identifier,
            organization_id=organization_id,
            user_id=USER_1 if organization_id == ORG_1 else USER_2,
            family_id=identifier,
            token_hash=identifier.hex * 2,
            issued_at=NOW - timedelta(days=90),
            expires_at=expires_at,
            revoked_at=revoked_at,
            revoked_reason="logout" if revoked_at is not None else None,
        )
    )
    session.flush()
    return identifier


@given(
    days=st.integers(min_value=1, max_value=3650), batch=st.integers(min_value=1, max_value=10000)
)
def test_retention_settings_accept_positive_windows_and_bounded_batches(
    days: int, batch: int
) -> None:
    settings = AuthSettings(
        AUTH_REFRESH_TOKEN_RETENTION_DAYS=days, AUTH_SESSION_PURGE_BATCH_SIZE=batch
    )
    assert settings.refresh_token_retention_days == days
    assert settings.session_purge_batch_size == batch


@pytest.mark.parametrize(
    "options",
    [
        {"AUTH_REFRESH_TOKEN_RETENTION_DAYS": 0},
        {"AUTH_REFRESH_TOKEN_RETENTION_DAYS": -1},
        {"AUTH_SESSION_PURGE_BATCH_SIZE": 0},
        {"AUTH_SESSION_PURGE_BATCH_SIZE": 10001},
    ],
)
def test_retention_settings_reject_unbounded_or_nonpositive_values(options: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AuthSettings.model_validate(options)


@pytest.mark.parametrize("days", [1, 30, 90])
def test_purge_keeps_exact_boundary_active_recent_and_other_tenant(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, days: int
) -> None:
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_RETENTION_DAYS", str(days))
    get_settings.cache_clear()
    cutoff = NOW - timedelta(days=days)
    old = cutoff - timedelta(microseconds=1)
    removed = {
        _token(db_session, expires_at=old),
        _token(db_session, expires_at=NOW + timedelta(days=1), revoked_at=old),
    }
    retained = {
        _token(db_session, expires_at=cutoff),
        _token(db_session, expires_at=NOW + timedelta(days=1), revoked_at=cutoff),
        _token(db_session, expires_at=NOW - timedelta(hours=1)),
        _token(db_session, expires_at=NOW + timedelta(days=1), revoked_at=NOW),
        _token(db_session, expires_at=NOW + timedelta(days=1)),
        _token(db_session, expires_at=old, organization_id=ORG_2),
    }
    assert purge_refresh_tokens(db_session, ORG_1, now=NOW) == len(removed)
    assert set(db_session.scalars(select(RefreshToken.id))) == retained
    assert purge_refresh_tokens(db_session, ORG_1, now=NOW) == 0


def test_batch_limit_and_rerun_drain_backlog(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_SESSION_PURGE_BATCH_SIZE", "2")
    get_settings.cache_clear()
    for _ in range(5):
        _token(db_session, expires_at=NOW - timedelta(days=31))
    assert [purge_refresh_tokens(db_session, ORG_1, now=NOW) for _ in range(4)] == [2, 2, 1, 0]
    assert list(db_session.scalars(select(RefreshToken))) == []


@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize("days", [1, 30, 90])
def test_settings_loading_refuses_retention_shorter_than_rotation_grace(
    monkeypatch: pytest.MonkeyPatch, enabled: str, days: int
) -> None:
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", enabled)
    monkeypatch.setenv("AUTH_REFRESH_TOKEN_RETENTION_DAYS", str(days))
    monkeypatch.setenv("AUTH_REFRESH_ROTATION_GRACE", str(days * 86400 + 1))
    get_settings.cache_clear()
    with pytest.raises(
        ValidationError,
        match="AUTH_REFRESH_TOKEN_RETENTION_DAYS must cover AUTH_REFRESH_ROTATION_GRACE",
    ):
        get_settings()


def test_rotation_grace_equal_to_retention_uses_configured_cleanup_window(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_REFRESH_ROTATION_GRACE", str(30 * 86400))
    get_settings.cache_clear()
    assert get_settings().auth.refresh_token_retention_days == 30
    expired = _token(db_session, expires_at=NOW - timedelta(days=31))
    revoked = _token(
        db_session, expires_at=NOW + timedelta(days=1), revoked_at=NOW - timedelta(days=31)
    )
    boundary = _token(db_session, expires_at=NOW - timedelta(days=30))
    assert purge_refresh_tokens(db_session, ORG_1, now=NOW) == 2
    assert db_session.get(RefreshToken, expired) is None
    assert db_session.get(RefreshToken, revoked) is None
    assert db_session.get(RefreshToken, boundary) is not None


def test_scheduler_enqueues_purge_without_other_features_and_handler_rechecks_switch(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", "true")
    monkeypatch.setattr(scheduler, "utc_now", lambda: NOW)
    get_settings.cache_clear()
    assert scheduler.any_scheduling_enabled(get_settings())
    tick = job_queue.enqueue(db_session, ORG_1, "scheduled_tick")
    db_session.flush()
    tick.status = "running"
    scheduler.run_tick(db_session, tick)
    purge = db_session.scalar(select(Job).where(Job.job_type == "session_purge"))
    assert purge is not None
    assert purge.organization_id == ORG_1
    # Completed jobs retain their hourly key; another tick must not repeat it.
    purge.status = "succeeded"
    scheduler.run_tick(db_session, tick)
    assert list(db_session.scalars(select(Job).where(Job.job_type == "session_purge"))) == [purge]
    monkeypatch.setattr(scheduler, "utc_now", lambda: NOW + timedelta(hours=1))
    scheduler.run_tick(db_session, tick)
    assert len(list(db_session.scalars(select(Job).where(Job.job_type == "session_purge")))) == 2
    identifier = _token(db_session, expires_at=NOW - timedelta(days=90))
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", "false")
    get_settings.cache_clear()
    HANDLERS["session_purge"](db_session, purge)
    assert purge.progress == {"status": "skipped", "reason": "session_purge_disabled"}
    assert db_session.get(RefreshToken, identifier) is not None
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", "true")
    get_settings.cache_clear()
    HANDLERS["session_purge"](db_session, purge)
    assert purge.progress == {"status": "succeeded", "refresh_tokens_deleted": 1}
    assert db_session.get(RefreshToken, identifier) is None


def _wait_for_purge_lock(bind: Engine | Connection, pid: int) -> None:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with Session(bind) as observer:
            if observer.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ) is True:
                return
        sleep(0.01)
    pytest.fail("The concurrent tenant tick did not wait for the transaction lock.")


@pytest.mark.committing_db
@pytest.mark.parametrize("rollback_first", [False, True])
def test_concurrent_tenant_ticks_serialize_hourly_purge(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, rollback_first: bool
) -> None:
    bind = db_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL transaction locks and independent connections.")
    monkeypatch.setenv("AUTH_SESSION_PURGE_ENABLED", "true")
    monkeypatch.setattr(scheduler, "utc_now", lambda: NOW)
    get_settings.cache_clear()
    ticks = [
        job_queue.enqueue(db_session, org_id, "scheduled_tick")
        for org_id in (ORG_1, ORG_1, ORG_2)
    ]
    for tick in ticks:
        tick.status = "running"
    first_id, second_id, other_id = [tick.id for tick in ticks]
    db_session.commit()
    entered, release = Event(), Event()
    contender_pid: Queue[int] = Queue()

    def pause_commit(_session: Session) -> None:
        entered.set()
        assert release.wait(20)
        if rollback_first:
            raise RuntimeError("rollback tick")

    def run(tick_id: UUID) -> bool:
        with Session(bind) as session:
            tick = session.get(Job, tick_id)
            assert tick is not None
            if tick_id == first_id:
                event.listen(session, "before_commit", pause_commit)
                if rollback_first:
                    with pytest.raises(RuntimeError, match="rollback tick"):
                        scheduler.run_tick(session, tick)
                    return False
            if tick_id == second_id:
                pid = cast(object, session.scalar(text("SELECT pg_backend_pid()")))
                assert isinstance(pid, int)
                contender_pid.put(pid)
            scheduler.run_tick(session, tick)
            return tick.progress.get("session_purge_enqueued") is True

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(run, first_id)
        try:
            assert entered.wait(10)
            second = executor.submit(run, second_id)
            pid = contender_pid.get(timeout=10)
            other = executor.submit(run, other_id)
            assert other.result(timeout=10)
            _wait_for_purge_lock(bind, pid)
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=10) is not rollback_first
        assert second.result(timeout=10) is rollback_first
    purges = list(db_session.scalars(select(Job).where(Job.job_type == "session_purge")))
    assert sorted(job.organization_id for job in purges) == sorted([ORG_1, ORG_2])
    assert all(
        job.coalesce_key == f"session-purge:{job.organization_id}:2026-10-12T12" for job in purges
    )
