from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.attestation.service import register_state
from app.attestation.service.digests import register_state_digest
from app.bi.domain.catalogue import catalogue
from app.bi.models.content import BiDashboard, BiDashboardVersion
from app.bi.models.marts import BiDimCounterparty, BiFactPositionDaily, BiFactPositionEom
from app.bi.models.notifications import BiSubscription
from app.bi.schemas.content import BiDashboardSpec
from app.bi.schemas.query import BiFilter, BiPackQuery, BiQuery, BiTime
from app.bi.service import content
from app.bi.service.compiler import compile_query
from app.bi.service.errors import BiQueryError
from app.bi.service.execution import execute
from app.bi.service.identity_policy import IDENTITY_REASON
from app.bi.service.mart_builder import fingerprint_for
from app.core.key_management import registry
from app.core.key_management.backfill import backfill_bank
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.core.tenancy import TenantContext
from app.data_engine.service.field_search import find_counterparties
from app.db.base import utc_now
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.identity.models.institution_profile import RelatedParty, RelatedPartyRole, Shareholding
from app.identity.public import Bank
from app.identity.schemas.institution_profile import (
    RelatedPartyCreate,
    RelatedPartyRoleInput,
    RelatedPartyUpdate,
)
from app.identity.service import institution_profile
from app.ml.behavioral.history import load_deposit_month_aggregates
from app.models.audit_event import AuditEvent
from app.models.canonical import CanonicalCounterparty, CanonicalPosition, CanonicalPositionSnapshot
from app.models.ingestion import IngestionBatch, LineageRecord, TranslationFailure
from app.reporting.models.packages import RegulatoryPackage
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services import ingestion
from tests.bi.fixtures import materialize_bi_plane
from tests.support.helpers import ORG_1, USER_1
from tests.support.inmemory_storage import InMemoryStorageClient

BANK_A = "BK-SAMP0001"
BANK_B = "BK-SAMP0002"
AS_OF = date(2026, 6, 30)
KEY_A = KeyReference("aws_kms", "synthetic-bank-a", "us-east-1", "111122223333")
KEY_B = KeyReference("aws_kms", "synthetic-bank-b", "us-east-1", "111122223333")
NEXT = KeyReference("aws_kms", "synthetic-next-a", "us-east-1", "111122223333")
NAME = "Synthetic Borrower Ñ"
IDENTIFIER = "SYNTHETIC-CARD-17"
CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, roles=("admin",))


@pytest.fixture
def provider(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> LocalKeyProvider:
    provider = LocalKeyProvider()
    for key in (KEY_A, KEY_B, NEXT):
        provider.add_key(key)

    def resolve(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", resolve)
    for bank_id in (BANK_A, BANK_B):
        db_session.add(
            Bank(
                id=bank_id,
                organization_id=ORG_1,
                name="Synthetic test bank",
                short_name="Synthetic",
                jurisdiction_code="GH",
                currency="GHS",
                license_type="universal_bank",
                institution_type="universal_bank",
                storage_slug=bank_id.lower(),
            )
        )
    db_session.flush()
    return provider


def connect(db: Session, provider: LocalKeyProvider, bank_id: str = BANK_A) -> None:
    registry.register(
        db,
        bank_id=bank_id,
        organization_id=ORG_1,
        storage_slug=bank_id.lower(),
        key=KEY_A if bank_id == BANK_A else KEY_B,
        providers=lambda _name: provider,
    )
    db.commit()


def counterparty(
    db: Session, bank_id: str = BANK_A, reference: str = IDENTIFIER, *, include_package: bool = True
) -> CanonicalCounterparty:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        adapter_version="synthetic",
        extraction_mode="full",
        status="accepted",
        as_of_date=AS_OF,
        validation_report={"name": NAME},
        etl_report={"name": NAME},
        error_message=NAME,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        bank_id=bank_id,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="synthetic-test",
        input_lineage_ids=[],
        details={"name": NAME},
    )
    db.add(lineage)
    db.flush()
    row = CanonicalCounterparty(
        organization_id=ORG_1,
        bank_id=bank_id,
        as_of_date=AS_OF,
        source_system="EXCEL_CSV",
        source_reference=reference,
        ingestion_batch_id=batch.id,
        lineage_id=lineage.id,
        validation_status="accepted",
        name=NAME,
        counterparty_type="RETAIL_INDIVIDUAL",
        group_reference="SYNTHETIC-GROUP-17",
        external_identifiers={"national_id": IDENTIFIER},
        attributes={"contact_name": NAME},
    )
    db.add(row)
    db.add(
        TranslationFailure(
            organization_id=ORG_1,
            bank_id=bank_id,
            ingestion_batch_id=batch.id,
            entity_type="counterparty",
            source_locator="synthetic-row-1",
            raw_record={"name": NAME, "national_id": IDENTIFIER},
            error_code="synthetic",
            error_message=NAME,
        )
    )
    if include_package:
        db.add(
            RegulatoryPackage(
                organization_id=ORG_1,
                bank_id=bank_id,
                return_family="large_exposures",
                return_code=reference,
                reporting_date=AS_OF,
                frequency="monthly",
                status="generated",
                version=1,
                snapshot={"counterparty_name": NAME, "balance": "100"},
                validation_report={"message": NAME},
                notes=NAME,
                generated_by=USER_1,
            )
        )
    db.flush()
    return row


def raw_fields(db: Session) -> dict[str, list[tuple[object, ...]]]:
    # This explicit catalogue is the coverage contract, rather than a reflection
    # of whichever columns happen to have an encryption implementation today.
    catalogue = {
        "canonical_counterparties": (
            "name, external_identifiers, group_reference, attributes, source_reference"
        ),
        "related_parties": "full_name, contact",
        "related_party_roles": "other_responsibilities, term_of_appointment, icag_registration",
        "ingestion_batches": "validation_report, etl_report, error_message",
        "lineage_records": "details",
        "translation_failures": "raw_record, error_message",
        "regulatory_packages": "snapshot, validation_report, notes",
    }
    return {
        table: [
            tuple(row) for row in db.execute(text(f"SELECT {columns} FROM {table} ORDER BY id"))
        ]
        for table, columns in catalogue.items()
    }


def test_every_covered_field_is_ciphertext_and_entity_reads_round_trip(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    cp = counterparty(db_session)
    cp_id = cp.id
    party = RelatedParty(
        organization_id=ORG_1,
        bank_id=BANK_A,
        party_type="individual",
        full_name=NAME,
        contact={"email": "synthetic@example.test", "national_id": IDENTIFIER},
    )
    db_session.add(party)
    db_session.flush()
    db_session.add(
        RelatedPartyRole(
            organization_id=ORG_1,
            party_id=party.id,
            role="director",
            other_responsibilities=NAME,
            term_of_appointment=NAME,
            icag_registration=IDENTIFIER,
            sitting_allowance=Decimal("12.34"),
        )
    )
    db_session.add(
        Shareholding(
            organization_id=ORG_1,
            party_id=party.id,
            share_type="ordinary",
            shareholder_rights="voting",
            number_of_shares=Decimal("100"),
            pct_shareholding=Decimal("25"),
            ubo_party_id=party.id,
        )
    )
    db_session.commit()
    stored = raw_fields(db_session)
    assert all(rows for rows in stored.values())
    for table, rows in stored.items():
        for row in rows:
            for value in row:
                encoded = json.dumps(value, ensure_ascii=False, default=str)
                assert "aqs-bank-field-v1" in encoded, (table, encoded)
                assert NAME not in encoded
                assert IDENTIFIER not in encoded
                assert "synthetic@example.test" not in encoded
    allowance = cast(
        object,
        db_session.execute(text("SELECT sitting_allowance FROM related_party_roles")).scalar_one(),
    )
    assert Decimal(str(allowance)) == Decimal("12.34")
    assert (
        db_session.execute(text("SELECT number_of_shares FROM shareholdings")).scalar_one() == 100
    )
    db_session.expire_all()
    loaded = db_session.get(CanonicalCounterparty, cp_id)
    assert loaded is not None and loaded.name == NAME and loaded.source_reference == IDENTIFIER
    assert loaded.external_identifiers == {"national_id": IDENTIFIER}
    loaded_party = db_session.scalar(select(RelatedParty))
    assert loaded_party is not None and loaded_party.full_name == NAME
    assert loaded_party.contact["national_id"] == IDENTIFIER
    loaded_role = db_session.scalar(select(RelatedPartyRole))
    assert loaded_role is not None and loaded_role.icag_registration == IDENTIFIER
    assert loaded_role.other_responsibilities == NAME and loaded_role.term_of_appointment == NAME
    batch = db_session.scalar(select(IngestionBatch))
    lineage = db_session.scalar(select(LineageRecord))
    failure = db_session.scalar(select(TranslationFailure))
    assert batch is not None and batch.validation_report == {"name": NAME}
    assert lineage is not None and lineage.details == {"name": NAME}
    assert failure is not None and failure.raw_record["name"] == NAME
    package = db_session.scalar(select(RegulatoryPackage))
    assert package is not None and package.snapshot["counterparty_name"] == NAME
    assert package.validation_report == {"message": NAME} and package.notes == NAME


def test_bank_scoped_exact_search_and_master_key_rotation(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    connect(db_session, provider, BANK_B)
    a = counterparty(db_session)
    b = counterparty(db_session, BANK_B)
    a_id, b_id = a.id, b.id
    db_session.commit()
    before = raw_fields(db_session)
    indexes = cast(
        list[object],
        list(
            db_session.execute(
                text("SELECT name_blind_index FROM canonical_counterparties ORDER BY bank_id")
            ).scalars()
        ),
    )
    assert len(set(indexes)) == 2
    assert all(NAME not in str(token) and len(str(token)) == 64 for token in indexes)
    db_session.expunge_all()
    assert [
        row.id
        for row in find_counterparties(
            db_session, CTX, BANK_A, field="name", value="  SYNTHETIC   borrower ñ  "
        )
    ] == [a_id]
    assert [
        row.id
        for row in find_counterparties(
            db_session, CTX, BANK_B, field="source_reference", value=IDENTIFIER
        )
    ] == [b_id]
    assert [
        row.id
        for row in find_counterparties(
            db_session,
            CTX,
            BANK_A,
            field="external_identifier",
            namespace="national_id",
            value=IDENTIFIER,
        )
    ] == [a_id]
    assert (
        find_counterparties(
            db_session, CTX, BANK_A, field="source_reference", value=IDENTIFIER.lower()
        )
        == []
    )
    assert (
        find_counterparties(
            db_session,
            CTX,
            BANK_A,
            field="external_identifier",
            namespace="tax_id",
            value=IDENTIFIER,
        )
        == []
    )
    row = registry.scoped_key(db_session, bank_id=BANK_A, organization_id=ORG_1)
    registry.rotate(db_session, row, NEXT, lambda _provider: provider)
    db_session.commit()
    provider.set_status(KEY_A, KeyStatus.DISABLED)
    db_session.expunge_all()
    assert [
        row.id for row in find_counterparties(db_session, CTX, BANK_A, field="name", value=NAME)
    ] == [a_id]
    assert raw_fields(db_session) == before


@pytest.mark.parametrize("substitution", ["bank", "record", "field"])
def test_ciphertext_cannot_be_moved_between_banks_records_or_fields(
    db_session: Session,
    provider: LocalKeyProvider,
    substitution: str,
) -> None:
    connect(db_session, provider)
    connect(db_session, provider, BANK_B)
    a = counterparty(db_session)
    b = counterparty(db_session, BANK_B if substitution == "bank" else BANK_A, "SYNTHETIC-OTHER")
    a_id, b_id = a.id, b.id
    db_session.commit()
    column = "group_reference" if substitution == "field" else "name"
    source = cast(
        object,
        db_session.execute(
            text(f"SELECT {column} FROM canonical_counterparties WHERE id=:id"),
            {"id": b_id if db_session.get_bind().dialect.name == "postgresql" else b_id.hex},
        ).scalar_one(),
    )
    assert isinstance(source, str)
    db_session.execute(
        text("UPDATE canonical_counterparties SET name=:value WHERE id=:id"),
        {
            "id": a_id if db_session.get_bind().dialect.name == "postgresql" else a_id.hex,
            "value": source,
        },
    )
    db_session.commit()
    db_session.expunge_all()
    with pytest.raises(KeyUnavailableError):
        db_session.get(CanonicalCounterparty, a_id)


def test_bank_a_key_cannot_unwrap_bank_b_and_revocation_refuses_reads(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    connect(db_session, provider, BANK_B)
    b_id = counterparty(db_session, BANK_B).id
    db_session.commit()
    envelope = db_session.scalar(
        select(ObjectKeyEnvelope).where(ObjectKeyEnvelope.bank_id == BANK_B)
    )
    assert envelope is not None
    with pytest.raises(KeyUnavailableError):
        provider.unwrap(
            KEY_A, envelope.wrapped_key, registry.wrapping_context(BANK_B, envelope.context_digest)
        )
    provider.set_status(KEY_B, KeyStatus.DISABLED)
    db_session.expunge_all()
    with pytest.raises(KeyUnavailableError):
        db_session.get(CanonicalCounterparty, b_id)
    db_session.expunge_all()
    with pytest.raises(KeyUnavailableError):
        find_counterparties(db_session, CTX, BANK_B, field="name", value=NAME)


def test_backfill_encrypts_legacy_rows_and_is_idempotent(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    cp_id = counterparty(db_session, include_package=False).id
    db_session.commit()
    assert NAME in json.dumps(raw_fields(db_session), ensure_ascii=False)
    connect(db_session, provider)
    db_session.expunge_all()
    with pytest.raises(KeyUnavailableError, match="backfill"):
        db_session.get(CanonicalCounterparty, cp_id)
    db_session.expunge_all()
    first = backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A, batch_size=1)
    assert first.encrypted == first.checked == 4
    stored = raw_fields(db_session)
    count = db_session.scalar(select(func.count()).select_from(ObjectKeyEnvelope))
    again = backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A, batch_size=1)
    assert again.checked == first.checked and again.encrypted == 0
    assert raw_fields(db_session) == stored
    assert db_session.scalar(select(func.count()).select_from(ObjectKeyEnvelope)) == count
    assert [
        row.id
        for row in find_counterparties(
            db_session, CTX, BANK_A, field="source_reference", value=IDENTIFIER
        )
    ] == [cp_id]


def test_register_crud_search_and_new_audits_omit_names(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    payload = RelatedPartyCreate(
        reason="Synthetic privacy test",
        party_type="individual",
        full_name=NAME,
        contact={"national_id": IDENTIFIER},
        roles=[
            RelatedPartyRoleInput(role="director"),
            RelatedPartyRoleInput(role="ultimate_beneficial_owner"),
        ],
    )
    created = institution_profile.create_related_party(db_session, CTX, BANK_A, payload)
    assert created.full_name == NAME
    assert (
        institution_profile.find_related_parties_by_name(db_session, CTX, BANK_A, NAME.lower())[
            0
        ].id
        == created.id
    )
    assert institution_profile.find_related_parties_by_name(db_session, CTX, BANK_B, NAME) == []
    db_session.expire_all()
    profile = institution_profile.get_full_profile(db_session, CTX, BANK_A)
    assert profile.related_parties[0].contact == {"national_id": IDENTIFIER}
    audits = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "related_party.created")
        )
    )
    assert len(audits) == 1 and "full_name" not in audits[0].details
    assert NAME not in json.dumps(audits[0].details, ensure_ascii=False)
    updated = institution_profile.update_related_party(
        db_session,
        CTX,
        BANK_A,
        created.id,
        RelatedPartyUpdate.model_validate(
            {**payload.model_dump(), "full_name": "Synthetic replacement"}
        ),
    )
    assert updated.full_name == "Synthetic replacement"
    db_session.expunge_all()
    assert institution_profile.find_related_parties_by_name(db_session, CTX, BANK_A, NAME) == []
    assert (
        institution_profile.find_related_parties_by_name(
            db_session, CTX, BANK_A, "SYNTHETIC REPLACEMENT"
        )[0].id
        == created.id
    )
    event = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "related_party.updated")
    )
    assert event is not None and "full_name" not in event.details


def test_numeric_sql_aggregation_preserves_decrypted_concentration_labels(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    first = counterparty(db_session)
    second = counterparty(db_session, reference="SYNTHETIC-SECOND")
    second.name = "Synthetic second borrower"
    for n, party in enumerate((first, second), start=1):
        common = dict(
            organization_id=ORG_1,
            bank_id=BANK_A,
            as_of_date=AS_OF,
            source_system="EXCEL_CSV",
            ingestion_batch_id=party.ingestion_batch_id,
            lineage_id=party.lineage_id,
            validation_status="accepted",
        )
        position = CanonicalPosition(
            **common,
            source_reference=f"synthetic-deposit-{n}",
            position_type="DEPOSIT",
            currency="GHS",
        )
        db_session.add(position)
        db_session.flush()
        db_session.add(
            CanonicalPositionSnapshot(
                **common,
                source_reference=f"synthetic-snapshot-{n}",
                position_id=position.id,
                counterparty_id=party.id,
                balance=Decimal(100 * n),
            )
        )
    db_session.commit()
    db_session.expunge_all()
    aggregates = load_deposit_month_aggregates(
        db_session, CTX, BANK_A, AS_OF, 1, non_maturing_only=False
    )
    assert len(aggregates) == 1
    assert aggregates[0].concentration_group == "SYNTHETIC-GROUP-17"
    assert aggregates[0].balance_ghs == 300 and aggregates[0].n_accounts == 2


def test_partial_refresh_and_update_preserve_other_encrypted_fields(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    row_id = counterparty(db_session).id
    db_session.commit()
    db_session.expunge_all()
    row = db_session.get(CanonicalCounterparty, row_id)
    assert row is not None
    db_session.refresh(row, ["name"])
    assert row.name == NAME
    db_session.expire(row, ["attributes", "external_identifiers", "group_reference"])
    row.name = "Synthetic replacement borrower"
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, row_id)
    assert loaded is not None and loaded.name == "Synthetic replacement borrower"
    assert loaded.source_reference == IDENTIFIER
    assert loaded.attributes == {"contact_name": NAME}
    assert loaded.group_reference == "SYNTHETIC-GROUP-17"
    assert loaded.external_identifiers == {"national_id": IDENTIFIER}
    assert find_counterparties(db_session, CTX, BANK_A, field="name", value=NAME) == []


def test_failed_sensitive_write_rolls_back_without_a_platform_fallback(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    connect(db_session, provider)
    row = counterparty(db_session)
    row_id = row.id
    db_session.commit()
    before = raw_fields(db_session)
    provider.set_status(KEY_A, KeyStatus.DISABLED)
    row.name = "Synthetic refused write"
    with pytest.raises(KeyUnavailableError):
        db_session.flush()
    db_session.rollback()
    provider.set_status(KEY_A, KeyStatus.ACTIVE)
    loaded = db_session.get(CanonicalCounterparty, row_id)
    assert loaded is not None and loaded.name == NAME
    assert raw_fields(db_session) == before


def test_interrupted_backfill_resumes_and_preserves_completed_ciphertext(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counterparty(db_session, include_package=False)
    counterparty(db_session, reference="SYNTHETIC-SECOND", include_package=False)
    db_session.commit()
    connect(db_session, provider)
    db_session.expunge_all()
    original = provider.generate_data_key
    calls = 0

    def interrupt(key: KeyReference, context: dict[str, str]):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyUnavailableError("Synthetic provider outage.")
        return original(key, context)

    monkeypatch.setattr(provider, "generate_data_key", interrupt)
    with pytest.raises(KeyUnavailableError, match="outage"):
        backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A, batch_size=1)
    names = cast(
        list[object],
        list(
            db_session.execute(
                text("SELECT name FROM canonical_counterparties ORDER BY id")
            ).scalars()
        ),
    )
    assert str(names[0]).startswith("aqs-bank-field-v1:") and names[1] == NAME
    monkeypatch.setattr(provider, "generate_data_key", original)
    resumed = backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A, batch_size=1)
    assert resumed.encrypted == 7 and resumed.checked == 8
    completed = cast(
        object,
        db_session.execute(
            text("SELECT name FROM canonical_counterparties ORDER BY id LIMIT 1")
        ).scalar_one(),
    )
    assert completed == names[0]
    assert backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A).encrypted == 0


def test_backfill_preserves_historical_test_audit_source_bytes(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    counterparty(db_session, include_package=False)
    db_session.add(
        AuditEvent(
            organization_id=ORG_1,
            event_type="related_party.created",
            details={"full_name": NAME, "reason": "Historical synthetic test event"},
        )
    )
    db_session.commit()
    before = cast(object, db_session.execute(text("SELECT details FROM audit_events")).scalar_one())
    connect(db_session, provider)
    backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A)
    after = cast(object, db_session.execute(text("SELECT details FROM audit_events")).scalar_one())
    assert before == after
    event = db_session.scalar(select(AuditEvent))
    assert event is not None and event.details["full_name"] == NAME


def test_ingestion_and_replacement_keep_identity_reads_and_exact_search_working(
    db_session: Session,
    provider: LocalKeyProvider,
    tmp_path: Path,
) -> None:
    connect(db_session, provider)
    mapping = MappingConfig(
        field_mappings={
            "counterparty": EntityMapping(
                source_table="Customers",
                fields={
                    "source_reference": "CustomerId",
                    "name": "CustomerName",
                    "counterparty_type": "Type",
                },
            ),
            "position": EntityMapping(
                source_table="Loans",
                fields={
                    "source_reference": "Reference",
                    "position_type": "Type",
                    "currency": "Currency",
                    "balance": "Balance",
                    "counterparty_reference": "CustomerId",
                },
            ),
        }
    )
    ingestion.create_mapping_config(
        db_session,
        CTX,
        BANK_A,
        MappingConfigCreate(
            source_system="EXCEL_CSV",
            name="Synthetic mapping",
            config=mapping,
            activate=True,
            reason="Synthetic encryption integration",
        ),
    )
    storage = InMemoryStorageClient()
    for name in (NAME, "Synthetic replacement borrower"):
        workbook = Workbook()
        customers = workbook.active
        assert customers is not None
        customers.title = "Customers"
        customers.append(["CustomerId", "CustomerName", "Type"])
        customers.append([IDENTIFIER, name, "RETAIL_INDIVIDUAL"])
        loans = cast(Worksheet, workbook.create_sheet("Loans"))
        loans.append(["Reference", "Type", "Currency", "Balance", "CustomerId"])
        loans.append(["synthetic-loan-1", "LOAN", "GHS", 123, IDENTIFIER])
        path = tmp_path / "synthetic.xlsx"
        workbook.save(path)
        result = ingestion.start_ingestion(
            db_session,
            CTX,
            BANK_A,
            IngestionBatchCreate(
                source_system="EXCEL_CSV",
                as_of_date=AS_OF,
                location=str(path),
                mapping_config_id=None,
                reason="Synthetic encrypted ingestion",
            ),
            storage,
        )
        assert result.batch.status in {"accepted", "accepted_with_warnings"}
        db_session.expunge_all()
        matches = find_counterparties(
            db_session, CTX, BANK_A, field="source_reference", value=IDENTIFIER
        )
        assert len(matches) == 1 and matches[0].name == name
        raw = cast(
            object,
            db_session.execute(
                text("SELECT name FROM canonical_counterparties WHERE superseded_by IS NULL")
            ).scalar_one(),
        )
        assert name not in str(raw) and str(raw).startswith("aqs-bank-field-v1:")
    assert (
        db_session.execute(
            text("SELECT balance FROM canonical_position_snapshots WHERE superseded_by IS NULL")
        ).scalar_one()
        == 123
    )


def test_connected_bank_bi_keeps_numeric_facts_without_identity_copies(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:

    party = counterparty(db_session, include_package=False)
    common = dict(
        organization_id=ORG_1,
        bank_id=BANK_A,
        as_of_date=AS_OF,
        source_system="EXCEL_CSV",
        ingestion_batch_id=party.ingestion_batch_id,
        lineage_id=party.lineage_id,
        validation_status="accepted",
    )
    position = CanonicalPosition(
        **common,
        source_reference="synthetic-deposit",
        position_type="DEPOSIT",
        currency="GHS",
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        CanonicalPositionSnapshot(
            **common,
            source_reference="synthetic-snapshot",
            position_id=position.id,
            counterparty_id=party.id,
            balance=Decimal("123"),
        )
    )
    db_session.commit()
    before = fingerprint_for(db_session, organization_id=ORG_1, bank_id=BANK_A, as_of=AS_OF)
    outcome = materialize_bi_plane(db_session, organization_id=ORG_1, bank_id=BANK_A, as_of=AS_OF)
    assert outcome is not None and outcome.status == "succeeded"
    assert db_session.scalar(select(BiDimCounterparty.name)) == NAME
    connect(db_session, provider)
    assert fingerprint_for(db_session, organization_id=ORG_1, bank_id=BANK_A, as_of=AS_OF) != before
    db_session.expunge_all()
    backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A)
    for rebuild in (False, True):
        if rebuild:
            outcome = materialize_bi_plane(
                db_session, organization_id=ORG_1, bank_id=BANK_A, as_of=AS_OF
            )
            assert outcome is not None and outcome.status == "succeeded"
        identity = db_session.execute(
            text("SELECT name, source_reference, group_reference FROM bi_dim_counterparty")
        ).one()
        assert NAME not in str(identity) and IDENTIFIER not in str(identity)
        assert identity[2] is None
        assert db_session.scalar(select(BiFactPositionDaily.counterparty_group)) is None
        assert db_session.scalar(select(BiFactPositionEom.counterparty_group)) is None
        q = BiQuery(measures=["deposits.balance_rc"], time=BiTime(as_of=AS_OF))
        compiled = compile_query(db_session, catalogue(), q, organization_id=ORG_1, bank_id=BANK_A)
        result = execute(db_session, compiled, timeout_ms=1000, row_cap=10)
        assert result.rows == [(Decimal("123"),)]
        for restricted in (
            q.model_copy(update={"dimensions": ["counterparty.name"]}),
            q.model_copy(update={"dimensions": ["counterparty"]}),
            q.model_copy(
                update={"filters": [BiFilter(member="counterparty.name", op="eq", values=[NAME])]}
            ),
        ):
            with pytest.raises(BiQueryError, match="bank-held encryption") as refused:
                compile_query(
                    db_session, catalogue(), restricted, organization_id=ORG_1, bank_id=BANK_A
                )
            assert refused.value.code == "bank_key_identity_bi_unavailable"
    assert backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A).encrypted == 0


def saved_version(db: Session, spec: dict[str, object]) -> BiDashboardVersion:
    dashboard = BiDashboard(
        organization_id=ORG_1,
        bank_id=BANK_A,
        owner_user_id=USER_1,
        title="Synthetic",
        description="",
        visibility="private",
        badge="personal",
        current_version=1,
    )
    db.add(dashboard)
    db.flush()
    version = BiDashboardVersion(
        organization_id=ORG_1,
        bank_id=BANK_A,
        dashboard_id=dashboard.id,
        version=1,
        title="Synthetic",
        description="",
        spec=spec,
        spec_digest="a" * 64,
        change_note="",
        created_by_user_id=USER_1,
        created_at=utc_now(),
    )
    return version


def test_saved_identity_filter_is_encrypted_and_explicitly_refused(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:

    connect(db_session, provider)
    saved_query = BiPackQuery(
        measures=["deposits.balance_rc"],
        window="as_of",
        filters=[BiFilter(member="counterparty.name", op="eq", values=[NAME])],
    )
    # Historical/storage-boundary representation; the product save gate below
    # refuses this question before accepting a new user-authored canvas.
    version = saved_version(db_session, {"query": saved_query.model_dump(mode="json")})
    db_session.add(version)
    subscription = BiSubscription(
        organization_id=ORG_1,
        bank_id=BANK_A,
        name="Synthetic",
        owner_user_id=USER_1,
        query=saved_query.for_period(AS_OF).model_dump(mode="json"),
        artifact_format="csv",
        cadence="daily",
        hour=7,
        minute=30,
        recipient_user_ids=[],
    )
    db_session.add(subscription)
    db_session.commit()
    version_id = version.id
    raw = cast(
        object, db_session.execute(text("SELECT spec FROM bi_dashboard_versions")).scalar_one()
    )
    assert NAME not in str(raw) and "aqs-bank-field-v1" in str(raw)
    raw_query = cast(
        object, db_session.execute(text("SELECT query FROM bi_subscriptions")).scalar_one()
    )
    assert NAME not in str(raw_query) and "aqs-bank-field-v1" in str(raw_query)
    subscription_id = subscription.id
    db_session.expunge_all()
    restored_subscription = db_session.get(BiSubscription, subscription_id)
    assert restored_subscription is not None
    assert NAME in json.dumps(restored_subscription.query, ensure_ascii=False)
    restored = db_session.get(BiDashboardVersion, version_id)
    assert restored is not None
    assert NAME in json.dumps(restored.spec, ensure_ascii=False)
    spec = BiDashboardSpec.model_validate(
        {
            "widgets": [
                {
                    "id": "identity",
                    "kind": "table",
                    "title": "Synthetic",
                    "query": saved_query.model_dump(mode="json"),
                }
            ],
            "layout": [{"i": "identity", "x": 0, "y": 0, "w": 6, "h": 4}],
        }
    )
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    with pytest.raises(content.MembersDenied) as refused:
        content.authorize_canvas(db_session, CTX, bank, spec, surface="test")
    assert refused.value.reason == IDENTITY_REASON
    authority = content.ViewerAuthority(
        db=db_session,
        ctx=CTX,
        bank=bank,
        cat=catalogue(),
        surface="test",
        _verdicts={("credit", "restricted"): True},
    )
    # A cached grant for another member of the same pair cannot allow identity.
    query = BiQuery(
        measures=["deposits.balance_rc"], time=BiTime(as_of=AS_OF), filters=saved_query.filters
    )
    assert authority.refused_members(query) == ("counterparty.name",)


@pytest.mark.parametrize("legacy", ["dashboard", "filing"])
def test_key_connection_and_backfill_refuse_immutable_plaintext_before_changes(
    db_session: Session,
    provider: LocalKeyProvider,
    legacy: str,
) -> None:
    if legacy == "filing":
        counterparty(db_session)
    else:
        db_session.add(saved_version(db_session, {"filters": [{"value": NAME}]}))
    db_session.commit()
    before = raw_fields(db_session)
    with pytest.raises(
        KeyUnavailableError,
        match="legacy plaintext.*" + ("spec" if legacy == "dashboard" else "snapshot"),
    ):
        connect(db_session, provider)
    db_session.rollback()
    assert db_session.scalar(select(BankEncryptionKey)) is None
    assert raw_fields(db_session) == before
    # Simulate a preexisting connection from an older release; maintenance must
    # detect the same precondition before any batch/scrub commits.
    db_session.add(
        BankEncryptionKey(
            organization_id=ORG_1,
            bank_id=BANK_A,
            storage_slug=BANK_A.lower(),
            provider=KEY_A.provider,
            key_id=KEY_A.key_id,
            region=KEY_A.region,
            owner_account=KEY_A.owner_account,
            status=KeyStatus.ACTIVE.value,
        )
    )
    db_session.commit()
    with pytest.raises(KeyUnavailableError, match="existing content guards remain unchanged"):
        backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A)
    assert raw_fields(db_session) == before


def test_register_digest_tracks_values_across_encryption_backfill(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    party = RelatedParty(
        organization_id=ORG_1,
        bank_id=BANK_A,
        party_type="individual",
        full_name=NAME,
        contact={"national_id": IDENTIFIER},
    )
    db_session.add(party)
    db_session.commit()
    party_id = party.id
    # Compare persisted projections on both sides. SQLite drops timezone info
    # on reload; a freshly-created in-memory timestamp is not that baseline.
    db_session.expunge_all()
    before = register_state_digest(register_state.register_state_rows(db_session, CTX, BANK_A))
    connect(db_session, provider)
    assert backfill_bank(db_session, organization_id=ORG_1, bank_id=BANK_A).encrypted == 1
    after = register_state_digest(register_state.register_state_rows(db_session, CTX, BANK_A))
    assert after == before
    loaded = db_session.get(RelatedParty, party_id)
    assert loaded is not None
    loaded.full_name = "Synthetic changed reportable name"
    db_session.commit()
    assert (
        register_state_digest(register_state.register_state_rows(db_session, CTX, BANK_A)) != before
    )
