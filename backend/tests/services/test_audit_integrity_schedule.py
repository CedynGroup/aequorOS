from __future__ import annotations

import threading
from contextlib import nullcontext
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

    def verify(session: Session, _now: datetime) -> bool:
        calls.append(session)
        return True

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
