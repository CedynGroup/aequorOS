"""An excluded row must be a stated exclusion, not just a smaller number.

Forensic re-audit 2026-08-22, D-4. The first half of that defect was that the
BoG return layer read canonical rows every calculation engine refuses; it is
closed, and ``tests/services/test_validation_status_fail_closed.py`` holds the
rule that keeps it closed.

This is the second half. Once the resolvers exclude an unvalidated row, the
filed return simply reports a smaller figure and says nothing — the same silence
in the other direction. A BSD balance sheet compiled while forty loan rows sit
in ``error`` understates by those forty rows, the template's own formulas
subtotal the understated figure faithfully, and no artifact, finding or note
tells the officer who signs it. So the generator measures what it refused and
states it:

* as a WARNING generation finding, folded into the package validation report the
  approver must clear (``reporting.unvalidated_canonical_rows``);
* in the immutable ``bog_form`` payload, so the export path — which never
  recomputes — carries it;
* on the "Completion notes" sheet of the xlsx audit twin.

WARNING and not ERROR on purpose: the resolvers exclude exactly what the engines
exclude, so refusing to generate here would refuse a filing that the internal
ratio it must agree with computes happily — D-4's divergence, rebuilt backwards.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services.regulatory_reporting.common import UNVALIDATED_BOOK_RULE
from app.services.regulatory_reporting.exports import render_bog_form_xlsx
from tests.api.helpers import headers
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

PERIOD_END = date(2026, 3, 31)
#: A position-sourced official form that depends on no other form, so the
#: disclosure is proved on the shortest path through the real package pipeline.
FORM_CODE = "BSD2"
NOTES_HEADING = "Unvalidated canonical rows excluded from every line"


def _seed_loan(session: Session, *, validation_status: str, withdrawn: bool = False) -> None:
    """One LOAN position + snapshot in a status no calculation reader admits."""
    batch = IngestionBatch(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=PERIOD_END,
    )
    session.add(batch)
    session.flush()
    lineage = LineageRecord(
        organization_id=DEMO_ORG_ID,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="unvalidated-disclosure-test",
        input_lineage_ids=[],
    )
    session.add(lineage)
    session.flush()

    common: dict[str, Any] = {
        "organization_id": DEMO_ORG_ID,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": PERIOD_END,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": validation_status,
        "withdrawn_at": datetime.now(UTC) if withdrawn else None,
    }
    position_id = uuid4()
    session.add(
        CanonicalPosition(
            id=position_id,
            source_reference="LOAN/NEVER-VALIDATED",
            position_type="LOAN",
            currency="GHS",
            **common,
        )
    )
    session.flush()
    session.add(
        CanonicalPositionSnapshot(
            position_id=position_id,
            source_reference="LOAN/NEVER-VALIDATED",
            balance=Decimal("777000000"),
            attributes={"balance_ghs": "777000000"},
            **common,
        )
    )
    session.flush()


def _materialize(*, unvalidated: str | None = None, withdrawn: bool = False) -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        if unvalidated is not None:
            _seed_loan(session, validation_status=unvalidated, withdrawn=withdrawn)
        session.commit()
    finally:
        session.close()


def _generate(db_client: TestClient) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(),
        json={"return_code": FORM_CODE, "reporting_date": PERIOD_END.isoformat()},
    )
    assert response.status_code == 201, f"{response.status_code} {response.text[:400]}"
    package_id = response.json()["id"]
    detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}", headers=headers()
    ).json()
    return {"id": package_id, "snapshot": detail["snapshot"]}


def _validate(db_client: TestClient, package_id: str) -> dict[str, Any]:
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/validate",
        headers=headers(),
    )
    assert response.status_code == 200, f"{response.status_code} {response.text[:400]}"
    report = response.json()["validation_report"]
    assert report is not None
    return report


def _completion_notes_text(snapshot: dict[str, Any]) -> str:
    session = get_sessionmaker()()
    try:
        bank = session.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        payload = render_bog_form_xlsx(FORM_CODE, snapshot, bank, datetime(2026, 4, 5, tzinfo=UTC))
    finally:
        session.close()
    workbook = openpyxl.load_workbook(io.BytesIO(payload))
    sheet = workbook["Completion notes"]
    return "\n".join(
        str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None
    )


@pytest.mark.parametrize("unvalidated_status", ["pending", "error", "blocked"])
def test_a_return_states_the_rows_it_refused_to_read(
    db_client: TestClient, unvalidated_status: str
) -> None:
    """Every status outside the admitted scope is disclosed, not only ``error``.

    ``pending`` matters most: P0-11 made it the PERSISTED DEFAULT for a record
    validation never enumerated, so quiet understatement is now the path an
    merely incomplete validation pass takes, not an exotic one.
    """
    _materialize(unvalidated=unvalidated_status)
    generated = _generate(db_client)
    snapshot = generated["snapshot"]

    counts = snapshot["bog_form"]["unvalidated_rows"]
    assert counts["canonical_position_snapshots"][unvalidated_status] >= 1

    findings = snapshot["metadata"]["generation_findings"]
    disclosure = [f for f in findings if f["rule"] == UNVALIDATED_BOOK_RULE]
    assert len(disclosure) == 1, findings
    assert disclosure[0]["severity"] == "WARNING"
    assert unvalidated_status in disclosure[0]["detail"]
    assert "have NOT passed validation" in disclosure[0]["detail"]

    # The approver's surface: the same sentence, in the validation report that
    # must be cleared before the package can be approved.
    report = _validate(db_client, generated["id"])
    reported = [f for f in report["findings"] if f["rule"] == UNVALIDATED_BOOK_RULE]
    assert len(reported) == 1, report["findings"]
    # Deliberately not an ERROR: it must not refuse a filing the calculation
    # engines would compute from exactly the same admitted book. Asserted on
    # THIS rule rather than on the report's ``error_count`` so the assertion
    # stays about the disclosure and nothing else — an unrelated ERROR from
    # another rule must fail its own test, not this one. (Until 2026-08-22 every
    # BSD package did carry exactly such an unrelated ERROR: the completeness
    # rule demanded a headline ``totals`` block from a template-driven return
    # whose roll-ups are the template's own formula cells. That is closed —
    # tests/services/test_reporting_totals_authority.py.)
    assert [f["severity"] for f in reported] == ["WARNING"]
    assert report["warning_count"] >= 1

    # The artifact's own face, rendered from the SEALED snapshot — the export
    # path never recomputes, so the disclosure has to survive the round trip.
    assert NOTES_HEADING in _completion_notes_text(snapshot)


def test_a_fully_validated_book_makes_no_claim_at_all(db_client: TestClient) -> None:
    """The negative control, and the reason there is no all-clear line.

    Absence of the finding IS the statement that nothing was excluded. Emitting
    "0 rows were excluded" on every return would be the blanket reassurance
    P0-14 removed from the prior-period movement rule — a sentence readers stop
    reading, which is worse than no sentence.
    """
    _materialize()
    snapshot = _generate(db_client)["snapshot"]

    assert snapshot["bog_form"]["unvalidated_rows"] == {}
    assert snapshot["bog_form"]["unvalidated_note"] == ""
    assert snapshot["metadata"]["generation_findings"] == []
    assert NOTES_HEADING not in _completion_notes_text(snapshot)


def test_a_withdrawn_row_is_not_reported_as_a_validation_problem(
    db_client: TestClient,
) -> None:
    """Retired rows are already out of the book under another rule.

    Reporting them here would tell an officer that a return is understated when
    the row was deliberately withdrawn under maker-checker — a false alarm on
    the artifact an examiner reads, which erodes the disclosure exactly where it
    has to be believed.
    """
    _materialize(unvalidated="error", withdrawn=True)
    snapshot = _generate(db_client)["snapshot"]

    assert snapshot["bog_form"]["unvalidated_rows"] == {}
    assert snapshot["metadata"]["generation_findings"] == []
