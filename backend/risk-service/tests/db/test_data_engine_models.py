from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.ids import new_uuid7
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    IngestionBatch,
    LineageRecord,
    MappingConfigRecord,
)
from tests.api.helpers import ORG_1

AS_OF = date(2026, 6, 30)


def make_bank(session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Sample Bank Limited",
        short_name="SBL",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    session.add(bank)
    session.flush()
    return bank


def make_batch(session: Session, bank: Bank, *, as_of: date = AS_OF) -> IngestionBatch:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="created",
        as_of_date=as_of,
    )
    session.add(batch)
    session.flush()
    return batch


def make_lineage(
    session: Session,
    batch: IngestionBatch,
    *,
    operation_type: str = "ADAPTER_EXTRACT",
    inputs: tuple[UUID, ...] = (),
) -> LineageRecord:
    record = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type=operation_type,
        operation_ref="excel_csv_v1.0/positions",
        input_lineage_ids=[str(input_id) for input_id in inputs],
    )
    session.add(record)
    session.flush()
    return record


def make_position(
    session: Session,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    source_reference: str = "POS-0001",
    position_type: str = "LOAN",
) -> CanonicalPosition:
    position = CanonicalPosition(
        organization_id=ORG_1,
        bank_id=batch.bank_id,
        as_of_date=batch.as_of_date,
        source_system="EXCEL_CSV",
        source_reference=source_reference,
        ingestion_batch_id=batch.id,
        lineage_id=lineage.id,
        position_type=position_type,
        currency="GHS",
    )
    session.add(position)
    session.flush()
    return position


def make_snapshot(
    session: Session,
    position: CanonicalPosition,
    batch: IngestionBatch,
    lineage: LineageRecord,
    *,
    balance: Decimal = Decimal("1000000.000001"),
) -> CanonicalPositionSnapshot:
    snapshot = CanonicalPositionSnapshot(
        organization_id=ORG_1,
        bank_id=position.bank_id,
        as_of_date=AS_OF,
        source_system="EXCEL_CSV",
        source_reference=position.source_reference,
        ingestion_batch_id=batch.id,
        lineage_id=lineage.id,
        position_id=position.id,
        balance=balance,
        interest_rate=Decimal("0.2450000000"),
        rate_type="FIXED",
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def build_chain(
    session: Session,
) -> tuple[Bank, IngestionBatch, LineageRecord, CanonicalPosition]:
    bank = make_bank(session)
    batch = make_batch(session, bank)
    lineage = make_lineage(session, batch)
    position = make_position(session, batch, lineage)
    return bank, batch, lineage, position


class TestCheckConstraints:
    def test_position_type_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        lineage = make_lineage(db_session, batch)
        with pytest.raises(IntegrityError):
            make_position(db_session, batch, lineage, position_type="PENSION_LIABILITY")

    def test_validation_status_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        lineage = make_lineage(db_session, batch)
        position = make_position(db_session, batch, lineage)
        position.validation_status = "APPROVED"
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_source_system_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        with pytest.raises(IntegrityError):
            batch = IngestionBatch(
                organization_id=ORG_1,
                bank_id=bank.id,
                source_system="SPREADSHEET",
                adapter_version="1.0",
                extraction_mode="full",
                status="created",
                as_of_date=AS_OF,
            )
            db_session.add(batch)
            db_session.flush()

    def test_snapshot_rate_type_and_ifrs9_stage_are_constrained(self, db_session: Session) -> None:
        bank, batch, lineage, position = build_chain(db_session)
        snapshot = make_snapshot(db_session, position, batch, lineage)
        snapshot.ifrs9_stage = 4
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_lineage_operation_type_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        with pytest.raises(IntegrityError):
            make_lineage(db_session, batch, operation_type="GUESSWORK")

    def test_counterparty_type_is_constrained(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        lineage = make_lineage(db_session, batch)
        with pytest.raises(IntegrityError):
            counterparty = CanonicalCounterparty(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                source_system="EXCEL_CSV",
                source_reference="CPTY-1",
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                name="Aunt Ama Enterprises",
                counterparty_type="FRIEND",
            )
            db_session.add(counterparty)
            db_session.flush()


class TestProvenanceIsMandatory:
    def test_position_requires_lineage(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        with pytest.raises(IntegrityError):
            position = CanonicalPosition(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                source_system="EXCEL_CSV",
                source_reference="POS-NOLINEAGE",
                ingestion_batch_id=batch.id,
                lineage_id=None,
                position_type="LOAN",
                currency="GHS",
            )
            db_session.add(position)
            db_session.flush()

    def test_lineage_walks_back_to_extract(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        batch = make_batch(db_session, bank)
        extract = make_lineage(db_session, batch, operation_type="ADAPTER_EXTRACT")
        translate = make_lineage(
            db_session, batch, operation_type="ADAPTER_TRANSLATE", inputs=(extract.id,)
        )
        position = make_position(db_session, batch, translate)

        node = db_session.get(LineageRecord, position.lineage_id)
        assert node is not None
        assert node.operation_type == "ADAPTER_TRANSLATE"
        parent_ids = [UUID(value) for value in node.input_lineage_ids]
        assert parent_ids == [extract.id]
        parent = db_session.get(LineageRecord, parent_ids[0])
        assert parent is not None
        assert parent.operation_type == "ADAPTER_EXTRACT"
        assert parent.input_lineage_ids == []


class TestSupersession:
    def test_duplicate_current_position_identity_is_rejected(self, db_session: Session) -> None:
        bank, batch, lineage, _position = build_chain(db_session)
        with pytest.raises(IntegrityError):
            make_position(db_session, batch, lineage)

    def test_superseded_position_frees_the_natural_key(self, db_session: Session) -> None:
        bank, batch, lineage, position = build_chain(db_session)

        replacement_batch = make_batch(db_session, bank)
        replacement_lineage = make_lineage(db_session, replacement_batch)
        # Column defaults fire at flush, so mint the replacement id up front:
        # the old row must point at its successor before the successor lands.
        replacement = CanonicalPosition(
            id=new_uuid7(),
            organization_id=ORG_1,
            bank_id=bank.id,
            as_of_date=AS_OF,
            source_system="EXCEL_CSV",
            source_reference=position.source_reference,
            ingestion_batch_id=replacement_batch.id,
            lineage_id=replacement_lineage.id,
            position_type="LOAN",
            currency="GHS",
        )
        position.superseded_by = replacement.id
        db_session.flush()
        db_session.add(replacement)
        db_session.flush()

        assert position.superseded_by == replacement.id
        assert replacement.superseded_by is None

    def test_snapshot_restatement_preserves_history(self, db_session: Session) -> None:
        bank, batch, lineage, position = build_chain(db_session)
        original = make_snapshot(
            db_session, position, batch, lineage, balance=Decimal("500.000000")
        )

        with pytest.raises(IntegrityError), db_session.begin_nested():
            make_snapshot(db_session, position, batch, lineage)

        restatement_batch = make_batch(db_session, bank)
        restatement_lineage = make_lineage(db_session, restatement_batch)
        restated = CanonicalPositionSnapshot(
            id=new_uuid7(),
            organization_id=ORG_1,
            bank_id=bank.id,
            as_of_date=AS_OF,
            source_system="EXCEL_CSV",
            source_reference=position.source_reference,
            ingestion_batch_id=restatement_batch.id,
            lineage_id=restatement_lineage.id,
            position_id=position.id,
            balance=Decimal("600.000000"),
        )
        original.superseded_by = restated.id
        db_session.flush()
        db_session.add(restated)
        db_session.flush()
        db_session.refresh(original)

        assert original.balance == Decimal("500.000000")
        assert original.superseded_by == restated.id
        assert restated.superseded_by is None


class TestMappingConfigs:
    def make_config(
        self,
        session: Session,
        bank: Bank,
        *,
        version: int = 1,
        status: str = "draft",
    ) -> MappingConfigRecord:
        config = MappingConfigRecord(
            organization_id=ORG_1,
            bank_id=bank.id,
            source_system="EXCEL_CSV",
            version=version,
            status=status,
            name="SBL positions workbook",
            config={"field_mappings": {}, "enum_mappings": {}, "product_mappings": {}},
        )
        session.add(config)
        session.flush()
        return config

    def test_versions_are_unique_per_scope(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        self.make_config(db_session, bank, version=1)
        with pytest.raises(IntegrityError):
            self.make_config(db_session, bank, version=1)

    def test_at_most_one_active_config_per_scope(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        self.make_config(db_session, bank, version=1, status="active")
        with pytest.raises(IntegrityError):
            self.make_config(db_session, bank, version=2, status="active")

    def test_retired_configs_do_not_block_a_new_active_one(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        retired = self.make_config(db_session, bank, version=1, status="active")
        retired.status = "retired"
        db_session.flush()
        replacement = self.make_config(db_session, bank, version=2, status="active")
        assert replacement.status == "active"

    def test_version_zero_is_rejected(self, db_session: Session) -> None:
        bank = make_bank(db_session)
        with pytest.raises(IntegrityError):
            self.make_config(db_session, bank, version=0)
