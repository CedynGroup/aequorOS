"""Absence is not acceptance (enterprise audit 2026-08-20, P0-11).

``ingestion._persist_canonical`` resolved each record's validation status with
``record_statuses.get(key, "accepted")``. A canonical record for which
validation produced NO status was therefore persisted as *accepted* and was
thereafter indistinguishable — in every engine and every filed return — from a
record that genuinely passed.

The default is now ``pending``: a status every calculation reader excludes (all
of them admit only ``("accepted", "warning")``), so an unvalidated row is
persisted, visible and traceable, but cannot reach a regulatory number.

This module's other-suite cases live in

``test_validation_status_fail_closed_integration.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data_engine.domain.ingestion.constants import (
    INCLUDED_VALIDATION_STATUSES,
    VALIDATION_STATUSES,
)
from app.data_engine.service import ingestion
from app.live.service.fact_derivation import _INCLUDED_VALIDATION_STATUSES


def test_unvalidated_status_is_a_real_status_that_no_engine_reads() -> None:
    assert ingestion.UNVALIDATED_STATUS in VALIDATION_STATUSES
    assert ingestion.UNVALIDATED_STATUS not in INCLUDED_VALIDATION_STATUSES
    # The derivation's own constant must BE the shared scope, not a copy that
    # happens to agree with it today (re-audit D-4: the literal was duplicated
    # into eighteen module-private constants and the reporting layer simply
    # omitted it).
    assert _INCLUDED_VALIDATION_STATUSES == INCLUDED_VALIDATION_STATUSES


@dataclass(frozen=True)
class _Record:
    source_reference: str


@dataclass(frozen=True)
class _Records:
    gl_accounts: tuple[_Record, ...] = ()
    counterparties: tuple[_Record, ...] = ()
    products: tuple[_Record, ...] = ()
    positions: tuple[_Record, ...] = ()
    loan_events: tuple[_Record, ...] = ()


def test_unvalidated_records_are_counted_per_entity_type() -> None:
    records = _Records(
        positions=(_Record("P1"), _Record("P2"), _Record("P3")),
        products=(_Record("PR1"),),
    )
    statuses = {("position", "P1"): "accepted"}

    missing = ingestion._unvalidated_records(records, statuses)  # type: ignore[arg-type]

    assert missing == {"position": 2, "product": 1}


def test_a_fully_validated_batch_reports_no_unvalidated_records() -> None:
    """The healthy path: ``run_validation`` seeds a status for every record."""
    records = _Records(positions=(_Record("P1"),), gl_accounts=(_Record("G1"),))
    statuses = {("position", "P1"): "accepted", ("gl_account", "G1"): "accepted"}

    assert ingestion._unvalidated_records(records, statuses) == {}  # type: ignore[arg-type]
