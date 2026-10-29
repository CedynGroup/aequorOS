"""Status observation, date transitions and reminder delivery contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from queue import Queue
from threading import Event
from time import monotonic, sleep
from typing import cast
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

from app import worker
from app.api.deps import TenantContext
from app.identity.public import Bank
from app.models import Notification
from app.policy.domain.regulatory_instruments import InstrumentStatus, instrument_status_on
from app.reporting.models.packages import RegulatoryReportingSettings
from app.reporting.schemas.packages import ReportingSettingsPut
from app.reporting.service.instrument_notifications import (
    instrument_events,
    scan_instrument_statuses,
)
from app.reporting.service.registry import REGISTRY
from app.reporting.service.reporting_settings import put_reporting_settings
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.stateful import run_database_machine

EFFECTIVE = date(2027, 1, 1)
TODAY = date(2026, 12, 2)


@given(st.dates(), st.sampled_from(["exposure_draft", "unpublished"]))
def test_preparation_status_never_earns_commencement_reminders(
    today: date, status: InstrumentStatus
) -> None:
    assert instrument_events(None, f"{status}:2027-01-01", EFFECTIVE, today) == ()


class StatusObservationMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.previous: str | None = None
        self.status = "exposure_draft"
        self.today = date(2026, 10, 1)

    @rule(
        status=st.sampled_from(["exposure_draft", "unpublished", "final_not_in_force", "in_force"]),
        today=st.dates(min_value=date(2026, 10, 1), max_value=date(2027, 3, 1)),
    )
    def observe(self, status: InstrumentStatus, today: date) -> None:
        resolved = instrument_status_on(status, EFFECTIVE, today)
        current = f"{resolved}:2027-01-01"
        events = instrument_events(self.previous, current, EFFECTIVE, today)
        assert ("changed" in events) == (self.previous is not None and self.previous != current)
        if resolved in ("exposure_draft", "unpublished", "in_force"):
            assert not any(event.startswith("commencement") for event in events)
        self.previous, self.status, self.today = current, resolved, today

    @invariant()
    def unchanged_observations_do_not_announce_status_changes(self) -> None:
        if self.previous is not None:
            assert "changed" not in instrument_events(
                self.previous, self.previous, EFFECTIVE, self.today
            )


def test_status_observations() -> None:
    run_database_machine(StatusObservationMachine)


def test_scan_emits_once_and_detects_finalisation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    ctx = TenantContext(organization_id=DEMO_ORG_ID)
    definition = REGISTRY["LE-MONTHLY"]
    monkeypatch.setitem(
        REGISTRY, "LE-MONTHLY", replace(definition, instrument_status="exposure_draft")
    )
    scan_instrument_statuses(db_session, ctx, bank, TODAY)
    db_session.flush()
    monkeypatch.setitem(REGISTRY, "LE-MONTHLY", definition)
    scan_instrument_statuses(db_session, ctx, bank, TODAY)
    db_session.flush()
    rows = list(
        db_session.scalars(
            select(Notification).where(
                Notification.organization_id == DEMO_ORG_ID,
                Notification.type.startswith("reporting.instrument."),
            )
        )
    )
    relevant = [row for row in rows if ":LE-MONTHLY:" in row.type]
    assert len(relevant) == 2
    assert all(bank.name in row.title and definition.title in row.title for row in relevant)
    assert all(bank.id not in row.title and definition.code not in row.title for row in relevant)
    assert all(
        bank.name in row.body and bank.id not in row.body and row.entity_id == bank.id
        for row in relevant
    )
    assert {row.type.split(":")[0] for row in relevant} == {
        "reporting.instrument.changed",
        "reporting.instrument.commencement_30",
    }
    assert scan_instrument_statuses(db_session, ctx, bank, TODAY) == 0
    scan_instrument_statuses(db_session, ctx, bank, EFFECTIVE)
    db_session.flush()
    changes = list(
        db_session.scalars(
            select(Notification).where(
                Notification.type == "reporting.instrument.changed:LE-MONTHLY:",
                Notification.organization_id == DEMO_ORG_ID,
            )
        )
    )
    assert len(changes) == 2
    assert any("in force" in change.body for change in changes)


def test_worker_wires_status_observer_into_scheduled_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = Mock()
    monkeypatch.setattr(worker.reporting_deadline_scan, "run_reporting_deadline_scan", handler)
    db, job = Mock(), Mock()
    worker.HANDLERS["reporting_deadline_scan"](db, job)
    handler.assert_called_once_with(db, job, instrument_scan=scan_instrument_statuses)


@pytest.mark.parametrize("scan_first", [True, False])
def test_settings_and_scan_preserve_each_others_state(
    db_session: Session, scan_first: bool
) -> None:
    materialize_canonical_test_book(db_session)
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    if scan_first:
        scan_instrument_statuses(db_session, ctx, bank, TODAY)
    put_reporting_settings(
        db_session, ctx, bank.id, ReportingSettingsPut(deadline_overrides={"CAR-RWA": 21})
    )
    scan_instrument_statuses(db_session, ctx, bank, TODAY)
    db_session.flush()
    settings = db_session.scalar(
        select(RegulatoryReportingSettings).where(
            RegulatoryReportingSettings.organization_id == ctx.organization_id,
            RegulatoryReportingSettings.bank_id == bank.id,
        )
    )
    assert settings is not None
    observations = dict(settings.instrument_observations)
    assert observations
    put_reporting_settings(
        db_session, ctx, bank.id, ReportingSettingsPut(deadline_overrides={"CAR-RWA": 22})
    )
    db_session.refresh(settings)
    assert settings.deadline_overrides == {"CAR-RWA": 22}
    assert settings.instrument_observations == observations
    assert scan_instrument_statuses(db_session, ctx, bank, TODAY) == 0


@pytest.mark.committing_db
@pytest.mark.parametrize("scan_first", [True, False])
def test_concurrent_settings_initialization_preserves_scan_and_overrides(
    db_session: Session, scan_first: bool
) -> None:
    bind = db_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("Requires disposable PostgreSQL and independent connections.")
    assert isinstance(bind, Engine)
    materialize_canonical_test_book(db_session)
    db_session.commit()
    ctx = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
    held, release = Event(), Event()
    waiting_pid: Queue[int] = Queue()

    def pause_commit(_db: Session) -> None:
        held.set()
        assert release.wait(30)

    def create_settings(scan: bool, first: bool) -> None:
        with Session(bind) as db:
            if first:
                event.listen(db, "before_commit", pause_commit, once=True)
            else:
                pid = cast(object, db.scalar(text("SELECT pg_backend_pid()")))
                assert isinstance(pid, int)
                waiting_pid.put(pid)
            if scan:
                bank = db.get(Bank, SAMPLE_BANK_ID)
                assert bank is not None
                scan_instrument_statuses(db, ctx, bank, TODAY)
                db.commit()
            else:
                put_reporting_settings(
                    db,
                    ctx,
                    SAMPLE_BANK_ID,
                    ReportingSettingsPut(deadline_overrides={"CAR-RWA": 21}),
                )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(create_settings, scan_first, True)
        try:
            assert held.wait(10)
            second = executor.submit(create_settings, not scan_first, False)
            pid = waiting_pid.get(timeout=10)
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline and not second.done():
                with Session(bind) as observer:
                    blocked = (
                        observer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                        )
                        is True
                    )
                if blocked:
                    break
                sleep(0.01)
            assert blocked and not second.done()
        finally:
            release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    _assert_settings_and_observations(db_session, ctx)


def _assert_settings_and_observations(db_session: Session, ctx: TenantContext) -> None:
    rows = list(
        db_session.scalars(
            select(RegulatoryReportingSettings).where(
                RegulatoryReportingSettings.organization_id == ctx.organization_id,
                RegulatoryReportingSettings.bank_id == SAMPLE_BANK_ID,
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].deadline_overrides == {"CAR-RWA": 21}
    assert rows[0].instrument_observations
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    assert scan_instrument_statuses(db_session, ctx, bank, TODAY) == 0
