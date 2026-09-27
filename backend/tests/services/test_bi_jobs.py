"""The three ``bi`` lane handlers, with the mart builder stubbed at its one seam.

The builder (``app.services.bi.mart_builder``) lands in a later wave; these
tests prove the handlers' own contract — run gate, version rule (P1-B5),
tenant/bank resolution, dispatch signatures, the backfill's self-re-enqueue
and termination, and error classification — against a stub bound through
``bi_common.load_builder``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.jobs import bi_common, bi_mart_backfill, bi_mart_refresh, bi_retention
from app.models import Bank, Job
from app.services import job_queue
from tests.api.helpers import ORG_1, ORG_2

AS_OF = date(2026, 6, 30)


@dataclass
class StubBuilder:
    """Records every dispatch; ``next_cursors`` scripts the backfill chain."""

    BUILDER_VERSION: int = 3
    refresh_calls: list[dict[str, Any]] = field(default_factory=list)
    backfill_calls: list[dict[str, Any]] = field(default_factory=list)
    retention_calls: list[dict[str, Any]] = field(default_factory=list)
    next_cursors: list[date | None] = field(default_factory=list)
    raise_on_dispatch: BaseException | None = None
    dropped: tuple[str, ...] = ("bi_fact_position_daily_2026_02",)
    #: The real builder returns ``skipped`` when a fingerprint has not moved, and
    #: the handler must enqueue nothing on that path.
    outcome_status: str = "succeeded"

    def refresh_bank_as_of(self, db: Session, **kwargs: Any) -> Any:
        _ = db
        if self.raise_on_dispatch is not None:
            raise self.raise_on_dispatch
        self.refresh_calls.append(kwargs)
        return SimpleNamespace(
            status=self.outcome_status,
            fingerprint="fp-" + kwargs["as_of"].isoformat(),
            row_counts={"bi_fact_position_daily": 12},
            trust={"positions": "green"},
        )

    def backfill_step(self, db: Session, **kwargs: Any) -> date | None:
        _ = db
        if self.raise_on_dispatch is not None:
            raise self.raise_on_dispatch
        self.backfill_calls.append(kwargs)
        return self.next_cursors.pop(0)

    def apply_retention(self, db: Session, **kwargs: Any) -> tuple[str, ...]:
        _ = db
        if self.raise_on_dispatch is not None:
            raise self.raise_on_dispatch
        self.retention_calls.append(kwargs)
        return self.dropped


@pytest.fixture
def builder(monkeypatch: pytest.MonkeyPatch) -> StubBuilder:
    stub = StubBuilder()
    monkeypatch.setattr(bi_common, "load_builder", lambda: stub)
    return stub


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    get_settings.cache_clear()


@pytest.fixture
def bank(db_session: Session) -> Bank:
    row = Bank(
        organization_id=ORG_1,
        name="Mart Bank",
        short_name="mart",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _refresh_payload(bank: Bank, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": AS_OF.isoformat(),
        "builder_version": 3,
        "reason": "ingestion",
    }
    payload.update(overrides)
    return payload


def _backfill_payload(bank: Bank, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "cursor_date": AS_OF.isoformat(),
        "until_date": date(2026, 1, 1).isoformat(),
        "builder_version": 3,
    }
    payload.update(overrides)
    return payload


def _claimed(db: Session, job_type: str, payload: dict[str, Any], *, bank: Bank | None) -> Job:
    """Enqueue and claim, as the worker would, so the row is ``running``."""
    job = job_queue.enqueue(
        db,
        ORG_1,
        job_type,
        bank_id=None if bank is None else bank.id,
        payload=payload,
        coalesce_key=None,
    )
    db.commit()
    claimed = job_queue.claim_next(db, utc_now(), (job_type,))
    assert claimed is not None and claimed.id == job.id
    return claimed


def _count(db: Session, job_type: str, *, status: str | None = None) -> int:
    stmt = select(func.count()).select_from(Job).where(Job.job_type == job_type)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return db.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# Run gate: the kill-switch is honoured at run time, without touching the builder
# ---------------------------------------------------------------------------


def test_refresh_skips_when_mart_enqueue_is_off(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job queued before the switch was pulled must not build — and a
    disabled deployment must not even import the (possibly absent) builder."""
    monkeypatch.setattr(
        bi_common, "load_builder", lambda: pytest.fail("builder imported while disabled")
    )
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert job.progress == {"status": "skipped", "reason": "bi_mart_enqueue_disabled"}


def test_backfill_skips_and_ends_the_chain_when_mart_enqueue_is_off(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bi_common, "load_builder", lambda: pytest.fail("builder imported while disabled")
    )
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    bi_mart_backfill.run_bi_mart_backfill(db_session, job)

    assert job.progress["reason"] == "bi_mart_enqueue_disabled"
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0


def test_retention_skips_when_the_scheduler_is_off(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bi_common, "load_builder", lambda: pytest.fail("builder imported while disabled")
    )
    job = _claimed(db_session, "bi_retention", {"builder_version": 3}, bank=None)

    bi_retention.run_bi_retention(db_session, job)

    assert job.progress == {"status": "skipped", "reason": "bi_scheduler_disabled"}


# ---------------------------------------------------------------------------
# Version rule (P1-B5)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("bi_on")
def test_a_newer_payload_is_skipped_by_an_older_handler(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """The stale worker does nothing: the job succeeds as ``skipped:version``."""
    job = _claimed(
        db_session, "bi_mart_refresh", _refresh_payload(bank, builder_version=4), bank=bank
    )

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert job.progress == {
        "status": "skipped",
        "reason": "version",
        "payload_version": 4,
        "handler_version": 3,
    }
    assert builder.refresh_calls == []


@pytest.mark.usefixtures("bi_on")
def test_the_version_rule_applies_to_every_bi_handler(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    backfill = _claimed(
        db_session, "bi_mart_backfill", _backfill_payload(bank, builder_version=9), bank=bank
    )
    bi_mart_backfill.run_bi_mart_backfill(db_session, backfill)
    assert backfill.progress["reason"] == "version"
    assert builder.backfill_calls == []
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0

    retention = _claimed(db_session, "bi_retention", {"builder_version": 9}, bank=None)
    bi_retention.run_bi_retention(db_session, retention)
    assert retention.progress["reason"] == "version"
    assert builder.retention_calls == []


@pytest.mark.usefixtures("bi_on")
def test_an_older_payload_runs_on_a_newer_handler(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """A newer builder's output supersedes an older enqueuer's request."""
    job = _claimed(
        db_session, "bi_mart_refresh", _refresh_payload(bank, builder_version=1), bank=bank
    )

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert len(builder.refresh_calls) == 1
    assert job.progress["status"] == "succeeded"
    assert job.progress["builder_version"] == 3


@pytest.mark.usefixtures("bi_on")
@pytest.mark.parametrize("bad", [None, "three"])
def test_a_payload_without_a_usable_version_is_a_payload_error(
    db_session: Session, bank: Bank, builder: StubBuilder, bad: Any
) -> None:
    payload = _refresh_payload(bank)
    if bad is None:
        del payload["builder_version"]
    else:
        payload["builder_version"] = bad
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=bank)

    with pytest.raises(bi_common.BiJobError, match="builder_version"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert builder.refresh_calls == []


# ---------------------------------------------------------------------------
# Normal dispatch: the contract signatures reach the builder unchanged
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("bi_on")
def test_a_succeeded_build_asks_for_an_alert_evaluation_and_the_new_data_runs(
    db_session: Session, bank: Bank, builder: StubBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The absence of this call is what went unnoticed, so it is asserted directly.

    ``bi_alert_evaluate`` had a job type, a lane, a stale-window decision and a
    handler, and NO enqueue site anywhere in the application. Nothing failed: the
    threshold-alerts feature was inert, and inert is indistinguishable from
    working until somebody sets an alert and waits. The only place that knows a
    bank's figures have moved is a succeeded build, which is why both hang here.
    """
    calls: dict[str, object] = {}

    def fake_alerts_enqueue(db: Session, **kwargs: object) -> object:
        calls["alert"] = kwargs
        return object()

    def fake_on_new_data(db: Session, **kwargs: object) -> list[object]:
        calls["on_new_data"] = kwargs
        return [object(), object()]

    monkeypatch.setattr(
        bi_common,
        "load_module",
        lambda dotted: (
            SimpleNamespace(
                enqueue_evaluation=fake_alerts_enqueue, enqueue_on_new_data=fake_on_new_data
            )
            if dotted.startswith("app.services.bi.")
            else bi_common.load_builder()
        ),
    )
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert calls["alert"] == {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of": AS_OF,
    }
    on_new_data = calls["on_new_data"]
    assert isinstance(on_new_data, dict)
    assert on_new_data["organization_id"] == ORG_1
    assert on_new_data["bank_id"] == bank.id
    assert on_new_data["as_of"] == AS_OF
    assert on_new_data["completed_at"] is not None
    assert job.progress["alert_evaluations_enqueued"] == 1
    assert job.progress["on_new_data_runs_enqueued"] == 2


@pytest.mark.usefixtures("bi_on")
def test_a_build_that_changed_nothing_asks_for_neither(
    db_session: Session, bank: Bank, builder: StubBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped build enqueues nothing, which is what makes both idempotent.

    The builder returns ``skipped`` when a slice's fingerprint has not moved, so a
    rebuild that changed no figure must not re-evaluate an alert or re-send a
    report. Without this the backfill — which calls the builder once per date —
    would mail a bank a thousand board packs.
    """
    asked: list[str] = []
    monkeypatch.setattr(
        bi_common,
        "load_module",
        lambda dotted: SimpleNamespace(
            enqueue_evaluation=lambda *a, **k: asked.append("alert"),
            enqueue_on_new_data=lambda *a, **k: asked.append("run") or [],
        ),
    )
    builder.outcome_status = "skipped"
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert asked == [], asked
    assert job.progress["alert_evaluations_enqueued"] == 0
    assert job.progress["on_new_data_runs_enqueued"] == 0


@pytest.mark.usefixtures("bi_on")
def test_refresh_dispatches_with_the_contract_signature(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    bi_mart_refresh.run_bi_mart_refresh(db_session, job)

    assert builder.refresh_calls == [
        {
            "organization_id": ORG_1,
            "bank_id": bank.id,
            "as_of": AS_OF,
            "reason": "ingestion",
        }
    ]
    assert job.progress == {
        "status": "succeeded",
        "as_of_date": "2026-06-30",
        "reason": "ingestion",
        "builder_version": 3,
        "fingerprint": "fp-2026-06-30",
        "row_counts": {"bi_fact_position_daily": 12},
        "trust": {"positions": "green"},
        # A SUCCEEDED build is the only place that knows a bank's figures moved,
        # so it is where the alert evaluation and the on-new-data runs are queued.
        # Both are ZERO here because both features ship behind their own flag and
        # this test does not enable either; the counts are in the progress record
        # so a build that queued nothing is distinguishable from one that was
        # never asked to, which is the distinction that was missing when
        # `bi_alert_evaluate` had a handler and no enqueue site at all.
        "alert_evaluations_enqueued": 0,
        "on_new_data_runs_enqueued": 0,
    }


@pytest.mark.usefixtures("bi_on")
def test_retention_dispatches_the_configured_window(
    db_session: Session, builder: StubBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_DAILY_RETENTION_DAYS", "120")
    get_settings.cache_clear()
    job = _claimed(db_session, "bi_retention", {"builder_version": 3}, bank=None)

    bi_retention.run_bi_retention(db_session, job)

    assert builder.retention_calls == [{"retention_days": 120}]
    assert job.progress == {
        "status": "succeeded",
        "retention_days": 120,
        "builder_version": 3,
        "dropped": ["bi_fact_position_daily_2026_02"],
    }


# ---------------------------------------------------------------------------
# Backfill: one job, one hop, self-re-enqueue, termination
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("bi_on")
def test_backfill_re_enqueues_itself_with_the_next_cursor(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    builder.next_cursors = [date(2026, 3, 31)]
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    bi_mart_backfill.run_bi_mart_backfill(db_session, job)

    assert builder.backfill_calls == [
        {
            "organization_id": ORG_1,
            "bank_id": bank.id,
            "cursor": AS_OF,
            "until": date(2026, 1, 1),
            "budget_seconds": 600,
        }
    ]
    successor = db_session.scalar(
        select(Job).where(Job.job_type == "bi_mart_backfill", Job.status == "queued")
    )
    assert successor is not None
    assert successor.id != job.id
    assert successor.organization_id == ORG_1
    assert successor.bank_id == bank.id
    assert successor.coalesce_key == f"bi-backfill:{bank.id}"
    assert successor.entity_type == "bank"
    assert successor.entity_id == bank.id
    assert successor.run_after is not None
    assert successor.payload == _backfill_payload(bank, cursor_date="2026-03-31")
    assert job.progress["status"] == "continued"
    assert job.progress["next_cursor_date"] == "2026-03-31"
    assert job.progress["next_job_id"] == str(successor.id)


@pytest.mark.usefixtures("bi_on")
def test_backfill_walks_the_chain_hop_by_hop_and_terminates(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """Three hops scripted by the builder; the third returns ``None`` and no
    successor is queued. Each hop is claimed the way the worker would claim it."""
    builder.next_cursors = [date(2026, 4, 30), date(2026, 2, 28), None]
    first = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    cursors_seen: list[str] = []
    current: Job | None = first
    hops = 0
    while current is not None:
        cursors_seen.append(current.payload["cursor_date"])
        bi_mart_backfill.run_bi_mart_backfill(db_session, current)
        job_queue.complete(db_session, current)
        hops += 1
        current = job_queue.claim_next(db_session, utc_now(), ("bi_mart_backfill",))

    assert hops == 3
    assert cursors_seen == ["2026-06-30", "2026-04-30", "2026-02-28"]
    assert [call["cursor"] for call in builder.backfill_calls] == [
        AS_OF,
        date(2026, 4, 30),
        date(2026, 2, 28),
    ]
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0
    assert _count(db_session, "bi_mart_backfill", status="succeeded") == 3
    last = db_session.scalar(
        select(Job)
        .where(Job.job_type == "bi_mart_backfill")
        .order_by(Job.queued_at.desc())
        .limit(1)
    )
    assert last is not None
    assert last.progress["status"] == "completed"
    assert "next_job_id" not in last.progress


@pytest.mark.usefixtures("bi_on")
def test_a_second_trigger_coalesces_into_the_queued_hop(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """One chain per bank: while a hop is queued, a new trigger merges into it."""
    builder.next_cursors = [date(2026, 5, 31)]
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)
    bi_mart_backfill.run_bi_mart_backfill(db_session, job)
    job_queue.complete(db_session, job)

    merged = job_queue.enqueue(
        db_session,
        ORG_1,
        "bi_mart_backfill",
        bank_id=bank.id,
        payload=_backfill_payload(bank),
        coalesce_key=bi_mart_backfill.coalesce_key_for(bank.id),
    )
    db_session.commit()

    assert _count(db_session, "bi_mart_backfill", status="queued") == 1
    queued = db_session.scalar(
        select(Job).where(Job.job_type == "bi_mart_backfill", Job.status == "queued")
    )
    assert queued is not None and queued.id == merged.id


# --- A4-05: the handler pins termination; the builder's cursor is not trusted --


def _run_hop_expecting_no_successor(db: Session, job: Job) -> bi_common.BiJobError:
    with pytest.raises(bi_common.BiJobError, match="did not advance") as info:
        bi_mart_backfill.run_bi_mart_backfill(db, job)
    assert _count(db, "bi_mart_backfill", status="queued") == 0
    assert not isinstance(info.value, bi_common.TransientBiBuildError)
    return info.value


@pytest.mark.usefixtures("bi_on")
def test_a_non_advancing_cursor_ends_the_chain_as_a_failure(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """A builder that hands back the same cursor would otherwise re-enqueue the
    same hop on ``run_after=now`` for ever, burning a hop budget per pass."""
    builder.next_cursors = [AS_OF]
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    error = _run_hop_expecting_no_successor(db_session, job)

    assert "2026-06-30 for cursor 2026-06-30" in str(error)
    # The queue's bounded retry is the only failure channel: it lands ``failed``.
    for _ in range(job.max_attempts + 1):
        job_queue.fail_with_retry(db_session, job, str(error))
    assert job_queue.is_exhausted(job)
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0


@pytest.mark.usefixtures("bi_on")
def test_a_cursor_moving_forward_ends_the_chain_as_a_failure(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """Newest-first means strictly earlier; a later date is a builder defect."""
    builder.next_cursors = [date(2026, 7, 31)]
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    _run_hop_expecting_no_successor(db_session, job)


@pytest.mark.usefixtures("bi_on")
def test_a_cursor_below_until_ends_the_chain_as_a_failure(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """The builder must stop AT ``until`` (returning ``None``), never overshoot it."""
    builder.next_cursors = [date(2025, 12, 31)]
    job = _claimed(
        db_session,
        "bi_mart_backfill",
        _backfill_payload(bank, until_date="2026-01-01"),
        bank=bank,
    )

    _run_hop_expecting_no_successor(db_session, job)


@pytest.mark.usefixtures("bi_on")
def test_a_strictly_decreasing_cursor_down_to_until_is_admitted(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """The guard admits every legitimate hop, including one landing exactly on
    ``until`` (the builder then returns ``None`` from that final hop)."""
    builder.next_cursors = [date(2026, 3, 31), date(2026, 1, 1), None]
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    cursors: list[str] = []
    current: Job | None = job
    while current is not None:
        cursors.append(current.payload["cursor_date"])
        bi_mart_backfill.run_bi_mart_backfill(db_session, current)
        job_queue.complete(db_session, current)
        current = job_queue.claim_next(db_session, utc_now(), ("bi_mart_backfill",))

    assert cursors == ["2026-06-30", "2026-03-31", "2026-01-01"]
    assert _count(db_session, "bi_mart_backfill", status="succeeded") == 3
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0
    assert _count(db_session, "bi_mart_backfill", status="failed") == 0


@pytest.mark.usefixtures("bi_on")
def test_a_backfill_that_walks_forward_is_refused(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    payload = _backfill_payload(bank, cursor_date="2026-01-01", until_date="2026-06-30")
    job = _claimed(db_session, "bi_mart_backfill", payload, bank=bank)

    with pytest.raises(bi_common.BiJobError, match="newest-first"):
        bi_mart_backfill.run_bi_mart_backfill(db_session, job)
    assert builder.backfill_calls == []


# ---------------------------------------------------------------------------
# Tenant and bank resolution
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("bi_on")
def test_a_bank_outside_the_jobs_organization_is_not_found(
    db_session: Session, builder: StubBuilder
) -> None:
    """The job's organization is the tenant binding; a sibling tenant's bank is invisible."""
    other = Bank(
        organization_id=ORG_2,
        name="Other Bank",
        short_name="other",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(other)
    db_session.commit()
    payload = _refresh_payload(other, organization_id=ORG_1)
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=other)

    with pytest.raises(bi_common.BiJobError, match="not found for organization"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert builder.refresh_calls == []


@pytest.mark.usefixtures("bi_on")
def test_a_payload_naming_another_organization_is_refused(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    payload = _refresh_payload(bank, organization_id=ORG_2)
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=bank)

    with pytest.raises(bi_common.BiJobError, match="organization_id"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert builder.refresh_calls == []


@pytest.mark.usefixtures("bi_on")
def test_a_payload_naming_another_bank_than_the_row_is_refused(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    payload = _refresh_payload(bank, bank_id="BK-OTHER001")
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=bank)

    with pytest.raises(bi_common.BiJobError, match="bank_id"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)


@pytest.mark.usefixtures("bi_on")
def test_a_job_with_no_bank_is_a_payload_error(db_session: Session, builder: StubBuilder) -> None:
    payload = {"organization_id": ORG_1, "as_of_date": "2026-06-30", "builder_version": 3}
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=None)

    with pytest.raises(bi_common.BiJobError, match="no bank_id"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert builder.refresh_calls == []


@pytest.mark.usefixtures("bi_on")
def test_a_missing_as_of_is_a_payload_error(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    payload = _refresh_payload(bank)
    del payload["as_of_date"]
    job = _claimed(db_session, "bi_mart_refresh", payload, bank=bank)

    with pytest.raises(bi_common.BiJobError, match="as_of_date"):
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)


# ---------------------------------------------------------------------------
# Error classification: builder failures are transient, payload errors are not
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("bi_on")
def test_a_builder_failure_is_classified_transient_for_the_queue(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    builder.raise_on_dispatch = RuntimeError("connection reset")
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    with pytest.raises(bi_common.TransientBiBuildError, match="refresh: connection reset") as info:
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert info.value.stage == "refresh"
    assert isinstance(info.value.__cause__, RuntimeError)
    # The worker's retry machinery treats it like any raising handler.
    job_queue.fail_with_retry(db_session, job, str(info.value))
    assert job.status == "queued"
    assert job.attempts == 1


@pytest.mark.usefixtures("bi_on")
def test_a_builder_failure_in_a_backfill_hop_does_not_enqueue_a_successor(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    """The retry re-runs THIS hop; a successor would race it."""
    builder.raise_on_dispatch = RuntimeError("deadlock detected")
    job = _claimed(db_session, "bi_mart_backfill", _backfill_payload(bank), bank=bank)

    with pytest.raises(bi_common.TransientBiBuildError, match="backfill"):
        bi_mart_backfill.run_bi_mart_backfill(db_session, job)
    assert _count(db_session, "bi_mart_backfill", status="queued") == 0


@pytest.mark.usefixtures("bi_on")
def test_a_structural_error_raised_by_the_builder_is_not_relabelled(
    db_session: Session, bank: Bank, builder: StubBuilder
) -> None:
    builder.raise_on_dispatch = bi_common.BiJobError("mart schema not migrated")
    job = _claimed(db_session, "bi_mart_refresh", _refresh_payload(bank), bank=bank)

    with pytest.raises(bi_common.BiJobError, match="not migrated") as info:
        bi_mart_refresh.run_bi_mart_refresh(db_session, job)
    assert not isinstance(info.value, bi_common.TransientBiBuildError)


# ---------------------------------------------------------------------------
# The seam itself
# ---------------------------------------------------------------------------


def test_the_builder_is_imported_lazily_from_the_named_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``load_builder`` is an importlib call at run time, so the worker boots
    without the module and a test can bind a stub via ``sys.modules``."""
    import sys  # noqa: PLC0415

    stub = SimpleNamespace(BUILDER_VERSION=42)
    monkeypatch.setitem(sys.modules, bi_common.BUILDER_MODULE, stub)
    assert bi_common.load_builder() is stub
    assert bi_common.BUILDER_MODULE == "app.services.bi.mart_builder"


def _runtime_import_nodes(tree: ast.AST) -> list[ast.stmt]:
    """Every import that EXECUTES when the module is imported.

    An import inside ``if TYPE_CHECKING:`` is not one: Python never evaluates
    that branch, so such an import cannot pull a service into a worker's boot and
    cannot couple two deployments — which is the entire property this guard
    protects. Excluding it makes the guard precise rather than weaker; a runtime
    import is still convicted, which ``test_the_module_level_import_guard_still_bites``
    proves against a constructed violation.
    """
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        names = {test.id} if isinstance(test, ast.Name) else set()
        if isinstance(test, ast.Attribute):
            names = {test.attr}
        if "TYPE_CHECKING" not in names:
            continue
        for inner in node.body:
            for descendant in ast.walk(inner):
                skipped.add(id(descendant))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom) and id(node) not in skipped
    ]


def _offending_bi_imports(source: str) -> list[str]:
    offenders: list[str] = []
    for node in _runtime_import_nodes(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.services.bi"):
            offenders.append(str(node.module))
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if alias.name.startswith("app.services.bi")
            )
    return offenders


def test_no_bi_handler_imports_the_builder_at_module_level() -> None:
    """The registration must ship a release before the builder is enabled."""
    from pathlib import Path  # noqa: PLC0415

    jobs_dir = Path(bi_common.__file__).parent
    for path in sorted(jobs_dir.glob("bi_*.py")):
        offenders = _offending_bi_imports(path.read_text(encoding="utf-8"))
        if offenders:
            pytest.fail(f"{path.name} imports {', '.join(offenders)} at module level")


def test_the_module_level_import_guard_still_bites() -> None:
    """The negative control. Narrowing a guard without one is how it stops working."""
    convicted = _offending_bi_imports("from app.services.bi.mart_builder import BUILDER_VERSION\n")
    assert convicted == ["app.services.bi.mart_builder"], convicted

    acquitted = _offending_bi_imports(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.services.bi.mart_builder import BUILDER_VERSION\n"
    )
    assert acquitted == [], acquitted

    # A FUNCTION-LEVEL import is convicted too, and that is deliberate rather than
    # over-strict: `sys.modules` cannot stand in for a name bound by an import
    # statement, so a test could not prove the handler boots without the service.
    # The sanctioned form is `importlib.import_module` on a named constant, which
    # is why `bi_common.load_builder` and `bi_commentary.load_commentary` exist and
    # why neither appears here.
    deferred = _offending_bi_imports(
        "def run():\n    from app.services.bi.mart_builder import BUILDER_VERSION\n"
    )
    assert deferred == ["app.services.bi.mart_builder"], deferred

    # And the sanctioned form is acquitted, because it is not an import statement.
    sanctioned = _offending_bi_imports(
        "import importlib\n"
        "_M = 'app.services.bi.mart_builder'\n"
        "def load():\n    return importlib.import_module(_M)\n"
    )
    assert sanctioned == [], sanctioned
