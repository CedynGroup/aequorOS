"""Status observation, date transitions and reminder delivery contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import worker
from app.api.deps import TenantContext
from app.domain.regulatory_instruments import InstrumentStatus, instrument_status_on
from app.identity.public import Bank
from app.models import Notification
from app.reporting.service.instrument_notifications import (
    instrument_events,
    scan_instrument_statuses,
)
from app.reporting.service.registry import REGISTRY
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
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
    assert all(bank.id in row.title for row in relevant)
    assert all(
        bank.name in row.body and bank.id in row.body and row.entity_id == bank.id
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
