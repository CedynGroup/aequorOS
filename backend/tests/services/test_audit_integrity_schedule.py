from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_integrity import ChainVerification, scheduled_verification
from app.core.config import get_settings
from app.core.observability import Condition
from app.db.base import utc_now
from app.models import Job
from app.services import job_queue, scheduler
from tests.support.helpers import ORG_1


def test_audit_flag_alone_schedules_verification_and_next_tick(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()

    calls: list[Session] = []

    def verify(session: Session) -> list[ChainVerification]:
        calls.append(session)
        return [ChainVerification("audit_events", 2, False)]

    monkeypatch.setattr(scheduler.audit_integrity, "scheduled_verification", verify)
    job_queue.enqueue(db_session, ORG_1, "scheduled_tick")
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None
    scheduler.run_tick(db_session, tick)
    assert calls == [db_session]
    # Tenant-readable job progress must not carry global audit stream counts.
    assert "audit_chains" not in tick.progress
    assert db_session.scalar(select(Job).where(Job.status == "queued")) is not None
    get_settings.cache_clear()


def test_unavailable_verification_emits_paging_condition(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Condition, dict[str, object]]] = []

    def capture(condition: Condition, _message: str, **fields: object) -> None:
        calls.append((condition, fields))

    monkeypatch.setattr("app.core.audit_integrity.emit", capture)
    pages: list[str] = []

    def page(reason: str, _stream: str | None = None) -> bool:
        pages.append(reason)
        return True

    monkeypatch.setattr("app.core.audit_integrity.publish_alert", page)
    assert scheduled_verification(db_session) == [
        ChainVerification("verification_unavailable", 0, False)
    ]
    assert calls == [
        (Condition.AUDIT_CHAIN_BROKEN, {"severity": "error", "reason": "verification_unavailable"})
    ]
    assert pages == ["verification_unavailable"]
