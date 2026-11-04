"""Status observation, date transitions and reminder delivery contracts.

This module's other-suite cases live in

``test_instrument_notifications_integration.py``.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from app import worker
from app.policy.domain.regulatory_instruments import InstrumentStatus, instrument_status_on
from app.reporting.service.instrument_notifications import (
    instrument_events,
    scan_instrument_statuses,
)
from tests.support.stateful import run_database_machine

EFFECTIVE = date(2027, 1, 1)


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


def test_worker_wires_status_observer_into_scheduled_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = Mock()
    monkeypatch.setattr(worker.reporting_deadline_scan, "run_reporting_deadline_scan", handler)
    db, job = Mock(), Mock()
    worker.HANDLERS["reporting_deadline_scan"](db, job)
    handler.assert_called_once_with(db, job, instrument_scan=scan_instrument_statuses)
