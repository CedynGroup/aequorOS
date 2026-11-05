"""Tests for test_ingestion_position_locks."""

from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import String, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.data_engine.domain.ingestion.contracts import CanonicalRecords, PositionData
from app.data_engine.service.ingestion import _reserve_position_identities
from app.models import Bank, CanonicalPosition, IngestionBatch, LineageRecord
from tests.support.helpers import ORG_1


@pytest.fixture
def reservation_context(
    db_session: Session,
) -> tuple[TenantContext, Bank, IngestionBatch, LineageRecord]:
    bank = Bank(
        organization_id=ORG_1,
        name="Position reservation test",
        short_name="position-reservation",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db_session.add(bank)
    db_session.flush()
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        as_of_date=date(2026, 7, 31),
        adapter_version="1.0",
        extraction_mode="full",
        status="validating",
    )
    db_session.add(batch)
    db_session.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        bank_id=batch.bank_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="reservation-test",
    )
    db_session.add(lineage)
    db_session.flush()
    return TenantContext(organization_id=ORG_1), bank, batch, lineage


@pytest.mark.parametrize("release", [False, True])
def test_identity_reservation_preserves_conflicts_and_follows_savepoint_outcome(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    release: bool,
    reservation_context: tuple[TenantContext, Bank, IngestionBatch, LineageRecord],
) -> None:
    ctx, bank, batch, lineage = reservation_context

    def records(currency: str) -> CanonicalRecords:
        return CanonicalRecords(
            positions=[
                PositionData(
                    source_reference="NEW-IDENTITY",
                    source_locator="Loans:2",
                    position_type="LOAN",
                    currency=currency,
                    balance=Decimal("100"),
                    origination_date=date(2024, 1, 10),
                )
            ]
        )

    savepoint = db_session.begin_nested()
    _reserve_position_identities(db_session, ctx, bank, batch, lineage, records("GHS"))
    held = db_session.scalars(
        select(CanonicalPosition).where(CanonicalPosition.bank_id == bank.id)
    ).one()
    assert held.validation_status == "pending"
    assert held.currency == "GHS"
    with monkeypatch.context() as stale_lookup:
        stale_lookup.setattr(db_session, "scalars", lambda _statement: [])
        _reserve_position_identities(db_session, ctx, bank, batch, lineage, records("USD"))
    db_session.refresh(held)
    assert held.currency == "GHS"
    assert held.origination_date == date(2024, 1, 10)
    if release:
        savepoint.commit()
    else:
        savepoint.rollback()
    assert len(
        db_session.scalars(
            select(CanonicalPosition.id).where(CanonicalPosition.bank_id == bank.id)
        ).all()
    ) == int(release)


@pytest.mark.parametrize(
    "unrepresentable",
    [
        {"currency": "GHSS"},
        {
            "source_reference": "X"
            * (
                cast(int, cast(String, CanonicalPosition.__table__.c.source_reference.type).length)
                + 1
            )
        },
        {"position_type": "UNKNOWN"},
    ],
)
def test_reservations_skip_unrepresentable_identities_without_changing_input_records(
    db_session: Session,
    reservation_context: tuple[TenantContext, Bank, IngestionBatch, LineageRecord],
    unrepresentable: dict[str, str],
) -> None:
    ctx, bank, batch, lineage = reservation_context
    valid = PositionData(
        source_reference="V"
        * cast(int, cast(String, CanonicalPosition.__table__.c.source_reference.type).length),
        source_locator="Loans:2",
        position_type="LOAN",
        currency="GHZ",
        balance=Decimal("100"),
    )
    invalid = valid.model_copy(update={"source_reference": "INVALID", **unrepresentable})
    records = CanonicalRecords(positions=[valid, invalid])
    before = records.model_dump()
    _reserve_position_identities(db_session, ctx, bank, batch, lineage, records)
    (held,) = db_session.scalars(
        select(CanonicalPosition).where(CanonicalPosition.bank_id == bank.id)
    ).all()
    assert held.source_reference == valid.source_reference
    assert held.currency == valid.currency
    assert held.validation_status == "pending"
    assert records.model_dump() == before
