"""Credential cleanup boundaries, batching and scheduled reachability."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from sqlalchemy import select
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


def test_long_rotation_grace_survives_cleanup(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_REFRESH_ROTATION_GRACE", str(60 * 86400))
    get_settings.cache_clear()
    identifier = _token(db_session, expires_at=NOW - timedelta(days=31))
    assert purge_refresh_tokens(db_session, ORG_1, now=NOW) == 0
    assert db_session.get(RefreshToken, identifier) is not None


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
