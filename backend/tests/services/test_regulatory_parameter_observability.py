"""Control-plane observability (docs/sdi.md §19).

The regulatory-parameter resolver is the single seam every class/type-keyed
number passes through, so it is where the two risk events are emitted as
structured logs (intercepted into the JSON log stream in production):

- an *unconfirmed* (pending) value driving a live calculation → WARNING;
- a mandatory parameter missing for the tenant's scope → ERROR (then fail-loud).

A *confirmed* resolution is intentionally silent — it is the steady state and
would drown the signal. These logs are the runtime counterpart to the persistent
``operator_audit_log`` / ``audit_events`` trail, not a replacement for it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Bank, RegulatoryParameter
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1

AS_OF = date(2026, 6, 30)


@contextmanager
def _capture_parameter_logs(level: int) -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target = logging.getLogger("app.services.regulatory_parameters")
    handler = Capture(level)
    previous_level = target.level
    previous_propagate = target.propagate
    target.setLevel(level)
    target.propagate = False
    target.addHandler(handler)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.propagate = previous_propagate


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Observability Bank",
        short_name="OBSB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def test_pending_value_use_logs_a_warning(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    # `related_party_limit_pct` ships 'pending' (value awaiting BoG confirmation).
    with _capture_parameter_logs(logging.WARNING) as captured:
        resolved = rp.resolve(db_session, bank, "related_party_limit_pct", as_of=AS_OF)
    assert resolved.is_pending
    records = [r for r in captured if "pending_value_used" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert "related_party_limit_pct" in message
    assert bank.id in message


def test_confirmed_value_use_is_silent(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    # `car_min` for an SDI is confirmed (Act 930 s.29) — no observability noise.
    with _capture_parameter_logs(logging.WARNING) as captured:
        resolved = rp.resolve(db_session, bank, "car_min", as_of=AS_OF)
    assert not resolved.is_pending
    assert [r for r in captured if "pending_value_used" in r.getMessage()] == []


def test_unseeded_mandatory_parameter_logs_an_error_and_fails_loud(
    db_session: Session,
) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    db_session.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "car_min",
        )
    )
    db_session.flush()
    with (
        _capture_parameter_logs(logging.ERROR) as captured,
        pytest.raises(rp.RegulatoryParameterError),
    ):
        rp.resolve(db_session, bank, "car_min", as_of=AS_OF)
    records = [r for r in captured if "regulatory_parameter.unseeded" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert "car_min" in records[0].getMessage()
