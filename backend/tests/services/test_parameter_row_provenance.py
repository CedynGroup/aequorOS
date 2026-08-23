"""A sealed run must name the parameter ROWS it resolved, not just their values.

Audit 2026-08-22 D-18. ``inputs["parameters"]`` records what a number WAS and is
sealed by the value-based ``input_hash``; it cannot say which approved
``regulatory_parameter`` generation authorised it, so "prove this filed ratio
used the approved parameter rather than one changed afterwards" had no answer on
the governance axis. ``regulatory_runs.parameter_provenance``
(migration ``202608230039``) closes that, and this file pins the three
properties that make it evidence rather than decoration:

* the row IDENTITY is captured — id plus the ``updated_at`` version marker, not
  only the value;
* the ledger is drained per run, so a row is bound to the run that used it;
* **no ``input_hash`` moves.** The identity lives beside the snapshot, never
  inside it. A parameter block still joins the snapshot only when consumed
  (the CRM/ECL precedent), and adding provenance must not change one byte of it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.authority.provenance import CalculationProvenance
from app.models import Bank, RegulatoryParameter
from app.services import regulatory_parameters as rp
from tests.api.helpers import ORG_1


def _bank(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Provenance tenant",
        short_name="PRV",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _seeded_row(db: Session, param_code: str) -> RegulatoryParameter:
    row = db.scalars(
        select(RegulatoryParameter)
        .where(RegulatoryParameter.param_code == param_code)
        .order_by(RegulatoryParameter.scope_type, RegulatoryParameter.scope_key)
    ).first()
    assert row is not None, f"the control plane is not seeded with {param_code!r}"
    return row


def test_resolving_a_parameter_records_its_row_identity(db_session: Session) -> None:
    bank = _bank(db_session)
    rp.consume_parameter_provenance(db_session)  # start from a clean ledger

    resolved = rp.resolve(db_session, bank, "car_min", as_of=date(2026, 6, 30))
    recorded = rp.consume_parameter_provenance(db_session)

    assert [entry["parameter_id"] for entry in recorded] == [resolved.parameter_id]
    entry = recorded[0]
    assert entry["param_code"] == "car_min"
    assert entry["status"] == "approved"
    assert entry["scope_key"] in ("bank", "universal_bank")
    assert entry["value"] == str(resolved.value)
    # The version marker: the control plane has no version column, and since
    # 202608230038 an approved generation cannot be edited in place, so
    # (id, row_version) pins exactly one immutable state of the row.
    assert entry["row_version"], "the row's updated_at must be recorded"
    assert entry["confirmation_status"] in ("confirmed", "pending")
    assert entry["source_citation"]


def test_the_ledger_is_drained_so_a_row_binds_to_one_run(db_session: Session) -> None:
    """A second drain must not re-attribute the same row to the next run."""
    bank = _bank(db_session)
    rp.consume_parameter_provenance(db_session)

    rp.resolve(db_session, bank, "car_min", as_of=date(2026, 6, 30))
    assert rp.consume_parameter_provenance(db_session)
    assert rp.consume_parameter_provenance(db_session) == []


def test_an_unrecorded_run_is_never_read_as_having_used_no_parameter(
    db_session: Session,
) -> None:
    """``None`` (predates the column) and ``[]`` (resolved nothing) are different
    claims, and only the second may be asserted."""
    bank = _bank(db_session)
    row = _seeded_row(db_session, "car_min")

    class _Run:
        id = "11111111-1111-4111-8111-111111111111"
        organization_id = ORG_1
        bank_id = bank.id
        reporting_period_id = "22222222-2222-4222-8222-222222222222"
        module = "capital"
        scenario_code = "baseline"
        status = "succeeded"
        engine_version = "capital-1"
        input_schema_version = "bank-facts-v2"
        output_schema_version = "capital-out-1"
        input_hash = "a" * 64
        inputs: dict[str, object] = {"parameters": {"car_min_pct": "13"}}
        metrics: dict[str, object] = {}
        started_at = None
        completed_at = None
        created_by = "33333333-3333-4333-8333-333333333333"

    unrecorded = _Run()
    assert CalculationProvenance.from_run(unrecorded).parameter_rows is None
    assert CalculationProvenance.from_run(unrecorded).parameter_rows_digest is None

    empty = _Run()
    empty.parameter_provenance = []  # type: ignore[attr-defined]
    empty_provenance = CalculationProvenance.from_run(empty)
    assert empty_provenance.parameter_rows == ()
    assert empty_provenance.parameter_rows_digest is not None

    used = _Run()
    used.parameter_provenance = [rp.parameter_row_provenance(row)]  # type: ignore[attr-defined]
    used_provenance = CalculationProvenance.from_run(used)
    assert used_provenance.parameter_rows is not None
    assert used_provenance.parameter_rows[0]["parameter_id"] == str(row.id)
    assert used_provenance.parameter_rows_digest != empty_provenance.parameter_rows_digest


def test_a_different_row_version_yields_a_different_rows_digest(db_session: Session) -> None:
    """If the same value were re-approved as a new generation, the run's row
    fingerprint must move — that is the whole point of recording identity."""
    row = _seeded_row(db_session, "car_min")
    first = rp.parameter_row_provenance(row)
    second = dict(first, parameter_id="00000000-0000-4000-8000-000000000000")

    class _Run:
        id = "11111111-1111-4111-8111-111111111111"
        organization_id = ORG_1
        bank_id = "BK-SAMP0001"
        reporting_period_id = "22222222-2222-4222-8222-222222222222"
        module = "capital"
        scenario_code = "baseline"
        status = "succeeded"
        engine_version = "capital-1"
        input_schema_version = "bank-facts-v2"
        output_schema_version = "capital-out-1"
        input_hash = "a" * 64
        inputs: dict[str, object] = {}
        metrics: dict[str, object] = {}
        started_at = None
        completed_at = None
        created_by = "33333333-3333-4333-8333-333333333333"
        parameter_provenance: list[dict[str, object]] = []

    left = _Run()
    left.parameter_provenance = [first]
    right = _Run()
    right.parameter_provenance = [second]

    assert (
        CalculationProvenance.from_run(left).parameter_rows_digest
        != CalculationProvenance.from_run(right).parameter_rows_digest
    )
    # The VALUE fingerprint is untouched: identity and value are separate axes.
    assert (
        CalculationProvenance.from_run(left).parameter_digest
        == CalculationProvenance.from_run(right).parameter_digest
    )


def test_recording_provenance_changes_no_snapshot_and_no_input_hash(
    db_session: Session,
) -> None:
    """The hard constraint: identity lands BESIDE the hashed snapshot.

    Resolving a parameter fills the ledger; building the snapshot must not see
    it. This asserts the separation at the seam rather than trusting it.
    """
    from app.services.regulatory_capital import _snapshot_hash  # noqa: PLC0415

    bank = _bank(db_session)
    rp.consume_parameter_provenance(db_session)
    rp.resolve(db_session, bank, "car_min", as_of=date(2026, 6, 30))

    snapshot = {
        "schema_version": "bank-facts-v2",
        "parameters": {"risk_weights_pct": {"sovereign": "0"}, "thresholds_pct": {}},
    }
    before = _snapshot_hash(snapshot)
    recorded = rp.consume_parameter_provenance(db_session)
    assert recorded, "the ledger recorded the resolution"
    assert _snapshot_hash(snapshot) == before
    assert "parameter_provenance" not in snapshot
    assert all("parameter_id" not in str(value) for value in snapshot["parameters"].values())


def test_the_recorded_value_is_the_exact_decimal_the_engine_consumed(
    db_session: Session,
) -> None:
    bank = _bank(db_session)
    rp.consume_parameter_provenance(db_session)
    resolved = rp.resolve(db_session, bank, "car_min", as_of=date(2026, 6, 30))
    entry = rp.consume_parameter_provenance(db_session)[0]

    assert Decimal(entry["value"]) == resolved.decimal
