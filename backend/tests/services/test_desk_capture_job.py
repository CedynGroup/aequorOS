"""The nightly desk capture job: enqueue gating, per-source isolation,
cadence, the Friday auction pass, and determination staging — plus the
JOB_TYPES/HANDLERS parity invariant the repo previously lacked (the
notification_email_mirror orphaned-job-type precedent).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import worker
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import DeskDetermination, DeskObservation, DeskSourceCapture, Job
from app.services import job_queue, scheduler
from app.services.market_desk import capture_job, register
from app.services.market_desk.capture_job import (
    CAPTURE_IDENTITY,
    enqueue_due_desk_capture,
    run_desk_capture,
)
from app.services.market_desk.sources.fetch import FetchError, RawFetch
from tests.api.helpers import ORG_1, ORG_2
from tests.services.test_market_desk_calculation import (
    COB,
    _seed_fixture_observations,
)

LEAD = "desk-lead@aequoros.com"

# 2026-08-09 is a Sunday (the harvest date); 2026-08-12 Wednesday, -14 Friday.
WEDNESDAY_EVENING = datetime(2026, 8, 12, 19, 0, tzinfo=UTC)
FRIDAY_EVENING = datetime(2026, 8, 14, 18, 30, tzinfo=UTC)


def _enable(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DESK_CAPTURE_ENABLED", "true")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _raw_fetch(url: str, content: bytes, meta: dict[str, Any] | None = None) -> RawFetch:
    return RawFetch(
        url=url,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        meta=meta or {},
    )


def _wdt_page(rows: list[list[str]]) -> bytes:
    return json.dumps(
        {"draw": 1, "recordsTotal": 38145, "recordsFiltered": len(rows), "data": rows}
    ).encode()


def _desk_job(db: Session, payload: dict[str, Any]) -> Job:
    job = job_queue.enqueue(db, ORG_1, "desk_capture", payload=payload)
    db.commit()
    return job


def _count(db: Session, job_type: str) -> int:
    return db.scalar(select(func.count()).select_from(Job).where(Job.job_type == job_type)) or 0


def _seed_parsed_capture(db: Session, source_key: str, captured_at: datetime) -> None:
    db.add(
        DeskSourceCapture(
            id=new_uuid7(),
            source_key=source_key,
            captured_at=captured_at,
            as_of_date=captured_at.date(),
            content_sha256=hashlib.sha256(b"seed").hexdigest(),
            parser_version="test/1",
            status="parsed",
            created_by="desk-analyst@aequoros.com",
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# JOB_TYPES <-> HANDLERS parity (the orphaned-job-type hazard)
# ---------------------------------------------------------------------------


def test_every_job_type_has_a_worker_handler() -> None:
    """A job type must ship in JOB_TYPES and worker.HANDLERS together — a
    type without a handler is enqueueable but never claimable, so its jobs
    queue forever (how notification_email_mirror was orphaned until its
    handler entry landed). No documented exceptions."""
    assert set(worker.HANDLERS) == set(job_queue.JOB_TYPES)


def test_notification_email_mirror_is_no_longer_orphaned() -> None:
    """Pin the specific fix: the tick enqueues this type, so the worker must
    claim it."""
    assert "notification_email_mirror" in worker.HANDLERS


# ---------------------------------------------------------------------------
# Enqueue gating
# ---------------------------------------------------------------------------


def test_enqueue_is_inert_when_disabled(db_session: Session) -> None:
    job = enqueue_due_desk_capture(db_session, ORG_1, now=WEDNESDAY_EVENING)
    assert job is None
    assert _count(db_session, "desk_capture") == 0


def test_enqueue_one_coalesced_job_per_day(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    # Before the capture hour (default 18 UTC): not yet due.
    early = WEDNESDAY_EVENING.replace(hour=17)
    assert enqueue_due_desk_capture(db_session, ORG_1, now=early) is None

    job = enqueue_due_desk_capture(db_session, ORG_1, now=WEDNESDAY_EVENING)
    assert job is not None
    assert job.coalesce_key == "desk_capture:2026-08-12"
    assert job.payload == {"cob_date": "2026-08-12"}  # no auction_pass midweek
    db_session.commit()

    # Later ticks the same day — same org or ANOTHER org (the desk is
    # global) — find the day taken.
    assert enqueue_due_desk_capture(db_session, ORG_1, now=WEDNESDAY_EVENING) is None
    assert enqueue_due_desk_capture(db_session, ORG_2, now=WEDNESDAY_EVENING) is None
    assert _count(db_session, "desk_capture") == 1
    get_settings.cache_clear()


def test_enqueue_friday_flags_auction_pass(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    job = enqueue_due_desk_capture(db_session, ORG_1, now=FRIDAY_EVENING)
    assert job is not None
    assert job.payload["auction_pass"] is True
    get_settings.cache_clear()


def test_scheduler_tick_carries_the_desk_capture_flag(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DESK_CAPTURE_ENABLED alone must keep the tick chain alive (the
    any_scheduling_enabled ships-together invariant) and enqueue the job."""
    _enable(monkeypatch, DESK_CAPTURE_HOUR_UTC="0")
    assert scheduler.any_scheduling_enabled(get_settings())

    job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    assert _count(db_session, "desk_capture") == 1
    assert tick.progress["desk_capture_enqueued"] is True
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Handler: capture stage
# ---------------------------------------------------------------------------


def test_run_desk_capture_isolates_a_failing_source(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One broken site never aborts the night: the other sources land, and
    the failure is recorded on a failed DeskSourceCapture row."""
    _enable(
        monkeypatch,
        DESK_CAPTURE_SOURCES="bog_interbank_daily,bog_fx_daily,bog_fx_reference",
    )
    interbank_page = _wdt_page([["1", "07 Aug 2026", "10.23"], ["2", "06 Aug 2026", "10.25"]])
    fx_page = _wdt_page([["07 Aug 2026", "US DOLLAR", "USDGHS", "11.75", "11.77", "11.76"]])

    def fake_fetch_source(source_key: str, session: Any, **kwargs: Any) -> list[RawFetch]:
        if source_key == "bog_interbank_daily":
            return [_raw_fetch("https://bog/t69", interbank_page, {"table_id": 69})]
        if source_key == "bog_fx_daily":
            return [_raw_fetch("https://bog/t31", fx_page, {"table_id": 31})]
        raise FetchError("bot filter said no")

    monkeypatch.setattr(capture_job, "fetch_source", fake_fetch_source)
    job = _desk_job(db_session, {"cob_date": "2026-08-09"})

    run_desk_capture(db_session, job)

    sources = job.progress["sources"]
    assert sources["bog_interbank_daily"]["status"] == "captured"
    assert sources["bog_interbank_daily"]["observations"] == 2
    assert sources["bog_fx_daily"]["status"] == "captured"
    assert sources["bog_fx_daily"]["observations"] == 4  # BUY/SELL/MID + USDGHS.MID alias
    assert sources["bog_fx_reference"]["status"] == "failed"
    assert "FetchError" in sources["bog_fx_reference"]["error"]
    # Sources outside the allow-list are reported, not silently absent.
    assert sources["bog_tbill_rates"]["status"] == "skipped_not_allowed"
    # No approved methodology in this test: governance stops the robot.
    assert job.progress["determination"] == {
        "status": "skipped",
        "reason": "no_approved_methodology",
    }

    failed = db_session.scalar(
        select(DeskSourceCapture).where(DeskSourceCapture.source_key == "bog_fx_reference")
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.parse_error is not None and failed.parse_error.startswith("fetch failed")
    parsed = db_session.scalars(
        select(DeskSourceCapture).where(DeskSourceCapture.status == "parsed")
    ).all()
    assert {row.source_key for row in parsed} == {"bog_interbank_daily", "bog_fx_daily"}
    observations = db_session.scalars(
        select(DeskObservation).where(DeskObservation.series_code == "GHS.INTERBANK.ON")
    ).all()
    assert len(observations) == 2
    get_settings.cache_clear()


def test_run_desk_capture_respects_weekly_cadence_and_auction_pass(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A weekly source with a fresh parsed capture is skipped on an ordinary
    night, but the Friday auction pass re-pulls it — auctions clear Fridays."""
    _enable(monkeypatch, DESK_CAPTURE_SOURCES="bog_tbill_rates")
    _seed_parsed_capture(db_session, "bog_tbill_rates", datetime(2026, 8, 8, 12, 0, tzinfo=UTC))
    calls: list[str] = []
    tender_page = _wdt_page([["07 Aug 2026", "1,912", "91 DAY BILL", "5.4800", "5.5570"]])

    def fake_fetch_source(source_key: str, session: Any, **kwargs: Any) -> list[RawFetch]:
        calls.append(source_key)
        return [_raw_fetch("https://bog/t2", tender_page, {"table_id": 2})]

    monkeypatch.setattr(capture_job, "fetch_source", fake_fetch_source)

    ordinary = _desk_job(db_session, {"cob_date": "2026-08-09"})
    run_desk_capture(db_session, ordinary)
    assert calls == []  # fresh weekly capture: nothing fetched
    assert ordinary.progress["sources"]["bog_tbill_rates"]["status"] == "skipped_not_due"

    friday = _desk_job(db_session, {"cob_date": "2026-08-14", "auction_pass": True})
    run_desk_capture(db_session, friday)
    assert calls == ["bog_tbill_rates"]
    summary = friday.progress["sources"]["bog_tbill_rates"]
    assert summary["status"] == "captured"
    assert summary["due"] == "auction_pass"
    assert summary["observations"] == 3  # DISCOUNT + INTEREST + YIELD alias
    get_settings.cache_clear()


def test_run_desk_capture_honors_kill_switch_at_run_time(
    db_session: Session,
) -> None:
    """A job queued before the flag was flipped off must not scrape."""
    job = _desk_job(db_session, {"cob_date": "2026-08-09"})
    run_desk_capture(db_session, job)
    assert job.progress == {"skipped": "desk_capture_disabled"}
    assert db_session.scalar(select(func.count()).select_from(DeskSourceCapture)) == 0


# ---------------------------------------------------------------------------
# Handler: determination staging
# ---------------------------------------------------------------------------


@pytest.fixture
def approved_desk(db_session: Session) -> Session:
    """Approved AEQ-GHS-CURVES v1 + the harvested fixture observations —
    the state a real deployment is in once Track-2 approval has happened."""
    register.ensure_default_methodology(db_session)
    register.approve_version(
        db_session,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        approved_by=LEAD,
        effective_from=date(2026, 1, 1),
    )
    _seed_fixture_observations(db_session)
    db_session.commit()
    return db_session


def _quiet_capture_env(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """Allow-list one weekly source with a fresh capture so the capture stage
    is a no-op and the test isolates the determination stage."""
    _enable(monkeypatch, DESK_CAPTURE_SOURCES="bog_gog_auction_pdf")
    _seed_parsed_capture(db, "bog_gog_auction_pdf", datetime(2026, 8, 6, 18, 0, tzinfo=UTC))

    def refuse(*args: Any, **kwargs: Any) -> list[RawFetch]:
        raise AssertionError("fetch_source must not be called for a fresh source")

    monkeypatch.setattr(capture_job, "fetch_source", refuse)


def test_determination_staged_draft_ready_with_qa(
    approved_desk: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture stages a pre-computed draft for the Analyst — never auto-submits."""
    _quiet_capture_env(monkeypatch, approved_desk)
    job = _desk_job(approved_desk, {"cob_date": COB.isoformat()})

    run_desk_capture(approved_desk, job)

    summary = job.progress["determination"]
    assert summary["status"] == "draft_ready"
    row = approved_desk.get(DeskDetermination, UUID(summary["determination_id"]))
    assert row is not None
    assert row.status == "draft"
    assert row.prepared_by == CAPTURE_IDENTITY
    assert row.derived_values  # pre-computed proposed package, not an empty shell
    assert "qa_passed" in row.derived_values
    assert row.qa_results
    assert summary["qa_passed"] == row.derived_values["qa_passed"]
    assert summary["input_digest"] == row.input_digest
    # Job stages material for Analyst review and STOPPED — submit/approve/publish
    # are human-only (Analyst → Supervisor).
    assert row.published_at is None
    assert row.reviewed_by is None
    get_settings.cache_clear()


def test_rerun_same_day_does_not_duplicate_the_draft(
    approved_desk: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet_capture_env(monkeypatch, approved_desk)
    first = _desk_job(approved_desk, {"cob_date": COB.isoformat()})
    run_desk_capture(approved_desk, first)
    assert first.progress["determination"]["status"] == "draft_ready"

    second = _desk_job(approved_desk, {"cob_date": COB.isoformat()})
    run_desk_capture(approved_desk, second)

    summary = second.progress["determination"]
    assert summary["status"] == "skipped"
    assert summary["reason"] == "determination_exists"
    total = approved_desk.scalar(
        select(func.count()).select_from(DeskDetermination).where(DeskDetermination.cob_date == COB)
    )
    assert total == 1
    get_settings.cache_clear()


def test_determination_skipped_without_approved_methodology(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DRAFT methodology is not enough — the robot cannot bypass Track-2
    approval, so the night ends at observations."""
    register.ensure_default_methodology(db_session)  # draft, never approved
    _quiet_capture_env(monkeypatch, db_session)
    job = _desk_job(db_session, {"cob_date": COB.isoformat()})

    run_desk_capture(db_session, job)

    assert job.progress["determination"] == {
        "status": "skipped",
        "reason": "no_approved_methodology",
    }
    assert db_session.scalar(select(func.count()).select_from(DeskDetermination)) == 0
    get_settings.cache_clear()
