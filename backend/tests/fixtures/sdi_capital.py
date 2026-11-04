"""Shared canonical SDI capital and position inputs for service tests."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    Bank,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
)
from tests.support.helpers import ORG_1

SDI_AS_OF = date(2026, 6, 30)


def create_sdi_bank(db: Session, *, institution_type: str = "savings_and_loans") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="s29 tenant",
        short_name="S29",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def create_sdi_batch_lineage(
    db: Session, bank: Bank, ref: str, *, as_of: date = SDI_AS_OF
) -> tuple[IngestionBatch, LineageRecord]:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        bank_id=batch.bank_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref=ref,
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    return batch, lineage


def seed_sdi_capital(
    db: Session,
    bank: Bank,
    rows: list[tuple[str, str, str]],
    *,
    as_of: date = SDI_AS_OF,
) -> None:
    """rows: (component, amount_ghs, tier)."""
    batch, lineage = create_sdi_batch_lineage(db, bank, "s29-capital", as_of=as_of)
    for i, (component, amount, tier) in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=bank.id,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=as_of,
                row_index=i,
                source_reference=f"CS/{i}",
                payload={"capital_component": component, "amount_ghs": amount, "tier": tier},
            )
        )
    db.flush()


def seed_sdi_positions(
    db: Session, bank: Bank, rows: list[tuple[str, str, str]], *, status: str = "accepted"
) -> None:
    """rows: (source_reference, position_type, balance_ghs)."""
    batch, lineage = create_sdi_batch_lineage(db, bank, "s29-positions")
    common = {
        "organization_id": ORG_1,
        "bank_id": bank.id,
        "as_of_date": SDI_AS_OF,
        "source_system": "EXCEL_CSV",
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage.id,
    }
    for ref, ptype, balance in rows:
        position = CanonicalPosition(
            **common, source_reference=ref, position_type=ptype, currency="GHS"
        )
        db.add(position)
        db.flush()
        db.add(
            CanonicalPositionSnapshot(
                **common,
                validation_status=status,
                source_reference=ref,
                position_id=position.id,
                balance=Decimal(balance),
                attributes={"balance_ghs": balance},
            )
        )
    db.flush()
