from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.contracts import CanonicalRecords, PositionData
from app.models import Bank, CanonicalPosition, IngestionBatch, LineageRecord
from app.services.ingestion import _lock_position_identities, _reserve_position_identities
from tests.api.helpers import ORG_1
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID


def test_postgresql_lock_statement_has_bounded_parameters_for_large_batches() -> None:
    """The emitted PostgreSQL statement binds 70,000 references as one protocol parameter."""
    db = MagicMock(spec=Session)
    db.get_bind.return_value.dialect = postgresql.dialect()
    references = {f"LN-{index:06d}" for index in range(70_000)}
    _lock_position_identities(
        db,
        TenantContext(organization_id=ORG_1),
        Bank(id=SAMPLE_BANK_ID),
        "EXCEL_CSV",
        references,
    )
    db.scalars.assert_called_once()
    statement = db.scalars.call_args.args[0]
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    assert len(compiled.params) == 4
    assert set(compiled.params["position_references"]) == references
    assert len(compiled.params["position_references"]) == 70_000
    db.scalars.return_value.all.assert_called_once_with()


def test_sqlite_skips_the_position_lock_query() -> None:
    db = MagicMock(spec=Session)
    db.get_bind.return_value.dialect = sqlite.dialect()
    _lock_position_identities(
        db,
        TenantContext(organization_id=ORG_1),
        Bank(id=SAMPLE_BANK_ID),
        "EXCEL_CSV",
        {"LN-0001"},
    )
    db.scalars.assert_not_called()


@pytest.mark.parametrize("release", [False, True])
def test_identity_reservation_preserves_conflicts_and_follows_savepoint_outcome(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, release: bool
) -> None:
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
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="reservation-test",
    )
    db_session.add(lineage)
    db_session.flush()
    ctx = TenantContext(organization_id=ORG_1)

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
