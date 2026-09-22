from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from tests.api.helpers import ORG_1, headers


def _create_sdi_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="SDI Diagnostics",
            short_name="SDID",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def _create_universal_bank() -> str:
    session = get_sessionmaker()()
    try:
        bank = Bank(
            organization_id=ORG_1,
            name="Universal Loan Diagnostics",
            short_name="ULD",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


#: One facility FIRST seen at the end of May, then re-observed month by month.
#: Only the June observation is a current, accepted snapshot: July is superseded,
#: August withdrawn, September rejected. The book's honest business date is June.
FIRST_SEEN = date(2026, 5, 31)
LATEST_CURRENT = date(2026, 6, 30)
SUPERSEDED_LATER = date(2026, 7, 31)
WITHDRAWN_LATER = date(2026, 8, 31)
REJECTED_LATER = date(2026, 9, 30)


def _seed_snapshot_history(bank_id: str) -> None:
    """A LOAN whose position row is dated at its first sighting while its
    current snapshot is a month later, plus three LATER snapshots that no
    calculation may read (superseded / withdrawn / validation error)."""
    session = get_sessionmaker()()
    try:
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=bank_id,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=FIRST_SEEN,
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="sdi-as-of-history",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        common = {
            "organization_id": ORG_1,
            "bank_id": bank_id,
            "source_system": "EXCEL_CSV",
            "source_reference": "LN-HISTORY",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
        }
        position = CanonicalPosition(
            **common,
            as_of_date=FIRST_SEEN,
            validation_status="accepted",
            position_type="LOAN",
            currency="GHS",
        )
        session.add(position)
        session.flush()
        observations = (
            (FIRST_SEEN, "accepted", None, None),
            (LATEST_CURRENT, "accepted", None, None),
            (SUPERSEDED_LATER, "accepted", uuid4(), None),
            (WITHDRAWN_LATER, "accepted", None, utc_now()),
            (REJECTED_LATER, "error", None, None),
        )
        for as_of, validation_status, superseded_by, withdrawn_at in observations:
            session.add(
                CanonicalPositionSnapshot(
                    **common,
                    as_of_date=as_of,
                    validation_status=validation_status,
                    position_id=position.id,
                    balance=Decimal("250000"),
                    ifrs9_stage=1,
                    superseded_by=superseded_by,
                    withdrawn_at=withdrawn_at,
                    withdrawn_by_batch_id=batch.id if withdrawn_at else None,
                    withdrawal_reason="duplicate feed" if withdrawn_at else None,
                    attributes={"balance_ghs": "250000", "days_past_due": 0},
                )
            )
        session.commit()
    finally:
        session.close()


def test_default_as_of_is_the_latest_current_snapshot_not_first_seen_or_a_retired_row(
    db_client,  # noqa: ANN001
) -> None:
    """With no ``as_of`` the diagnostics key on the newest date the CURRENT book
    carries: later than the position's first-seen date (May), and never a
    superseded, withdrawn or rejected snapshot (July, August, September)."""
    bank_id = _create_sdi_bank()
    _seed_snapshot_history(bank_id)

    classification = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/loan-classification", headers=headers()
    )
    assert classification.status_code == 200, classification.text
    assert classification.json()["as_of"] == LATEST_CURRENT.isoformat()
    # The June book is what gets classified, not an empty future date.
    assert classification.json()["loan_count"] == 1

    readiness = db_client.get(f"/api/v1/banks/{bank_id}/sdi/readiness", headers=headers())
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["as_of"] == LATEST_CURRENT.isoformat()

    # An explicit as-of is still honoured verbatim.
    explicit = db_client.get(
        f"/api/v1/banks/{bank_id}/sdi/readiness",
        headers=headers(),
        params={"as_of": FIRST_SEEN.isoformat()},
    )
    assert explicit.json()["as_of"] == FIRST_SEEN.isoformat()


def test_sdi_liquidity_position_returns_typed_unavailable_controls(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/liquidity-position", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["ratios"]) == 8
    assert all(row["status"] == "not_computable" for row in body["ratios"])
    assert len(body["reserves"]) == 2
    assert all(row["status"] == "not_computable" for row in body["reserves"])
    assert body["maturity_ladder"]


def test_sdi_large_exposures_returns_empty_book_without_false_breach(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/large-exposures", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exposures"] == []
    assert body["findings"] == []


def test_sdi_capital_assurance_returns_explicit_filing_blockers(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/capital-assurance", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filing_status"] == "blocked"
    assert body["current"]["assessment_status"] == "not_computable"
    assert body["filing_blockers"]


def test_sdi_loan_classification_returns_raw_dpd_buckets(db_client) -> None:  # noqa: ANN001
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dpd_covered_count"] == 0
    assert len(body["delinquency_buckets"]) == 7
    assert {metric["code"] for metric in body["portfolio_at_risk"]} == {
        "par_30",
        "par_60",
        "par_90",
        "par_180",
        "par_360",
    }
    # An empty book states no provisions: held is null and coverage is null —
    # the wire never fabricates a zero for an unstated figure.
    assert body["provisions_held"] is None
    assert body["provision_coverage_pct"] is None


def test_universal_bank_loan_classification_uses_neutral_endpoint(db_client) -> None:  # noqa: ANN001
    bank_id = _create_universal_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/loan-classification", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_class"] == "bank"
    assert "olem" in {bucket["grade"] for bucket in body["buckets"]}
    assert len(body["delinquency_buckets"]) == 7


def test_sdi_capital_summary_declares_the_risk_classes_it_omits(db_client) -> None:  # noqa: ANN001
    """The scope of the ratio reaches the wire, not just the service.

    A CAR computed on credit risk alone has to say so on the surface that
    presents it (forensic audit "DIVERGENCE #1"), so the payload carries every
    known risk class, in scope or not, and the one-line disclosure.
    """
    bank_id = _create_sdi_bank()
    response = db_client.get(f"/api/v1/banks/{bank_id}/sdi/capital-summary", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["composition_source"] == "code_default"
    by_class = {row["risk_class"]: row for row in body["risk_classes"]}
    assert set(by_class) == {"credit", "market", "operational"}
    assert by_class["credit"]["in_scope"] is True
    assert by_class["market"]["in_scope"] is False
    assert by_class["operational"]["in_scope"] is False
    assert "credit risk only" in body["rwa_scope_note"]
    # Reader-facing copy, no parameter codes or raw enums.
    assert "no charge is assumed" in by_class["operational"]["note"]
