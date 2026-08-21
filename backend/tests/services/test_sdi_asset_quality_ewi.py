"""Class-aware asset-quality EWI (docs/sdi.md §2.2, Phase G wiring).

The ``earnings_asset_quality`` early-warning signal is regime-aware:

- an SDI's asset quality is the NBFI 4-grade NPL ratio (days-past-due driven),
  the classification regime it actually files — sourced from
  ``loan_classification.classify_loan_book`` on a percentage scale;
- a universal bank keeps the IFRS-9 stage-3 loan share, byte-identical to the
  pre-wiring behaviour (``_stage3_loan_share`` is delegated unchanged);
- an SDI with no ingested loan book returns no signal (never a fabricated 0).

Only the ``_asset_quality_signal`` seam is exercised here — the surrounding EWI
evaluator (severity ladder, ¶74 notification) is covered by the bank goldens,
which this wiring leaves untouched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
)
from app.services import liquidity_ewi as ewi
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 6, 30)
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _make_bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Asset Quality EWI Bank",
        short_name="AQEB",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _period(db: Session, bank: Bank) -> BankReportingPeriod:
    period = BankReportingPeriod(
        organization_id=ORG_1,
        bank_id=bank.id,
        period_start=date(2026, 6, 1),
        period_end=AS_OF,
        label="2026-06",
        status="open",
    )
    db.add(period)
    db.flush()
    return period


def _seed_loans(
    db: Session, bank: Bank, loans: list[tuple[str, str, int | None, int | None]]
) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=AS_OF,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="asset-quality-ewi-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    common = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
        "validation_status": "accepted",
    }
    for ref, balance, dpd, stage in loans:
        position = CanonicalPosition(
            **common, source_reference=ref, position_type="LOAN", currency="GHS"
        )
        db.add(position)
        db.flush()
        attributes: dict[str, object] = {"balance_ghs": balance}
        if dpd is not None:
            attributes["days_past_due"] = dpd
        db.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                ifrs9_stage=stage,
                attributes=attributes,
            )
        )
    db.flush()


def test_sdi_asset_quality_uses_nbfi_npl_ratio(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    period = _period(db_session, bank)
    _seed_loans(
        db_session,
        bank,
        [
            ("LN-1", "2000000", 0, 1),  # standard      (performing)
            ("LN-2", "1000000", 120, 2),  # substandard (NPL)
            ("LN-3", "600000", 200, 3),  # doubtful      (NPL)
            ("LN-4", "400000", 400, 3),  # loss          (NPL)
        ],
    )

    value, note = ewi._asset_quality_signal(db_session, CTX, bank, period, rows=[])

    # NPL = (1M + 0.6M + 0.4M) / 4M = 50% -> percentage scale.
    assert value == Decimal("50.00")
    assert note is not None and "NBFI" in note
    # Currency label is resolved from the bank, not a literal.
    assert "GHS" in note


def test_sdi_asset_quality_without_loans_yields_no_signal(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="savings_and_loans")
    period = _period(db_session, bank)

    value, note = ewi._asset_quality_signal(db_session, CTX, bank, period, rows=[])

    assert value is None
    assert note is not None and "No canonical loan positions" in note


def test_bank_asset_quality_delegates_to_stage3_share(db_session: Session) -> None:
    bank = _make_bank(db_session, institution_type="universal_bank")
    period = _period(db_session, bank)
    # A universal bank must not touch the NBFI classifier — the signal is exactly
    # the IFRS-9 stage-3 share of the passed rows (byte-identical delegation).
    value, note = ewi._asset_quality_signal(db_session, CTX, bank, period, rows=[])
    expected_value, expected_note = ewi._stage3_loan_share([])
    assert value == expected_value
    assert note == expected_note
