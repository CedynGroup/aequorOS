from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import Literal, cast

import pytest
from openpyxl import Workbook
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.key_management import registry
from app.core.key_management.models import ObjectKeyEnvelope
from app.core.key_management.types import KeyStatus
from app.data_engine.service.field_search import find_counterparties
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.identity.public import Bank
from app.models.canonical import CanonicalCounterparty
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import attachments, exports
from app.reporting.service.registry import get_definition
from app.reporting.service.templates import get_template
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.schemas.push import PushBatchOpen, PushRecordsPage
from app.services import ingestion, push_ingestion
from app.storage.client import StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.encryption import ObjectEncryption
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.core.test_bank_field_encryption import AS_OF, BANK_A, CTX, NAME, counterparty
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
from tests.storage.test_key_rotation_concurrency import _observed_session, _wait_for_row_lock
from tests.support.helpers import ORG_1, USER_1

__all__ = ["bank_storage"]
pytestmark = pytest.mark.committing_db
SIBLING = "BK-SAMP0002"
REFERENCE = "SYNTHETIC-TRANSACTION-BORROWER"
type Operation = Literal["ingestion", "push", "export", "attachment"]


def _workbook(db: Session, path: Path) -> None:
    ingestion.create_mapping_config(
        db,
        CTX,
        BANK_A,
        MappingConfigCreate(
            source_system="EXCEL_CSV",
            name="Synthetic transaction mapping",
            activate=True,
            config=MappingConfig(
                field_mappings={
                    "counterparty": EntityMapping(
                        source_table="Customers",
                        fields={
                            "source_reference": "Reference",
                            "name": "Name",
                            "counterparty_type": "Type",
                        },
                    ),
                }
            ),
            reason="Synthetic storage transaction regression",
        ),
    )
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Customers"
    sheet.append(["Reference", "Name", "Type"])
    sheet.append([REFERENCE, NAME, "RETAIL_INDIVIDUAL"])
    workbook.save(path)


def _package(db: Session) -> RegulatoryPackage:
    definition = get_definition("IRRBB-PILOT")
    assert definition is not None
    template = get_template(definition.template_id)
    assert template is not None
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=BANK_A,
        return_family=definition.family,
        return_code=definition.code,
        reporting_date=AS_OF,
        frequency="monthly",
        status="generated",
        version=1,
        generated_by=USER_1,
        snapshot={
            "institution": {"name": "Synthetic bank"},
            "sections": [
                {"code": section.section_code, "rows": []} for section in template.sections
            ],
        },
        source_runs=[],
    )
    db.add(package)
    db.commit()
    return package


def _exercise(
    operation: Operation,
    db: Session,
    storage: S3CompatibleStorageClient,
    path: Path,
    package: RegulatoryPackage,
    bank: Bank,
) -> None:
    if operation == "ingestion":
        result = ingestion.start_ingestion(
            db,
            CTX,
            BANK_A,
            IngestionBatchCreate(
                source_system="EXCEL_CSV",
                as_of_date=AS_OF,
                location=str(path),
                reason="Synthetic transaction test",
            ),
            storage,
            commit=False,
        )
        assert result.batch.status in {"accepted", "accepted_with_warnings"}
    elif operation == "push":
        opened = push_ingestion.open_push_batch(
            db,
            CTX,
            BANK_A,
            PushBatchOpen(
                as_of_date=AS_OF,
                idempotency_key="synthetic-unit-of-work",
                reason="Synthetic transaction test",
            ),
            storage,
        )
        staged = push_ingestion.stage_push_records(
            db,
            CTX,
            BANK_A,
            opened.push_batch_id,
            PushRecordsPage(
                entities={
                    "counterparty": [
                        {
                            "source_reference": REFERENCE,
                            "name": NAME,
                            "counterparty_type": "RETAIL_INDIVIDUAL",
                        }
                    ]
                },
            ),
            storage,
        )
        assert staged.total_records_staged == 1
        result = push_ingestion.commit_push_batch(db, CTX, BANK_A, opened.push_batch_id, storage)
        assert result.batch.status in {"accepted", "accepted_with_warnings"}
        assert (
            push_ingestion.get_push_batch(db, CTX, BANK_A, opened.push_batch_id, storage).status
            == "committed"
        )
    elif operation == "export":
        artifact = exports.export_package(db, CTX, package, "csv")
        assert artifact.size_bytes > 0
    else:
        attachment = attachments.upload_package_attachment(
            db,
            CTX,
            bank,
            package,
            storage,
            kind="supporting_document",
            title="Synthetic evidence",
            filename="synthetic.pdf",
            content=b"%PDF-1.7\nSynthetic evidence\n%%EOF\n",
            max_bytes=1024,
        )
        assert attachment.byte_size > 0


@pytest.mark.parametrize("operation", ["ingestion", "push", "export", "attachment"])
@pytest.mark.parametrize("rotation_bank", [BANK_A, SIBLING])
def test_transactional_storage_never_waits_on_its_own_queued_rotation(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    rotation_bank: str,
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock queues and independent connections are required.")
    provider = bank_storage.provider
    monkeypatch.setattr(registry, "provider_for", lambda _name: provider)
    db_session.add(
        Bank(
            id=SIBLING,
            organization_id=ORG_1,
            name="Synthetic sibling",
            short_name="Synthetic",
            jurisdiction_code="GH",
            currency="GHS",
            license_type="universal_bank",
            institution_type="universal_bank",
            storage_slug="bk-samp0002",
        )
    )
    db_session.flush()
    registry.register(
        db_session,
        bank_id=SIBLING,
        organization_id=ORG_1,
        storage_slug="bk-samp0002",
        key=KEY,
        providers=lambda _name: provider,
    )
    db_session.commit()
    path = tmp_path / "synthetic.xlsx"
    if operation == "ingestion":
        _workbook(db_session, path)
    package = _package(db_session)
    opened_sessions = 0

    def budget(_db: Session, _transaction: SessionTransaction, connection: Connection) -> None:
        _ = connection.exec_driver_sql("SET LOCAL lock_timeout='1s'")

    def independent_session() -> Session:
        nonlocal opened_sessions
        opened_sessions += 1
        db = Session(database)
        event.listen(db, "after_begin", budget)
        return db

    storage = S3CompatibleStorageClient(
        StorageEngineSettings.model_validate(
            {
                "STORAGE_BACKEND": "s3",
                "STORAGE_ENV": "dev",
                "S3_ENDPOINT": None,
                "S3_REGION": "us-east-1",
            }
        ),
        client_factory=lambda *_args, **_kwargs: bank_storage.s3,
        encryption=ObjectEncryption(
            registry.DatabaseEnvelopeStore(independent_session, lambda _name: provider)
        ),
    )
    monkeypatch.setattr(exports, "get_storage_client", lambda: storage)
    event.listen(db_session, "after_begin", budget)
    db_session.commit()
    original = counterparty(db_session, include_package=False)
    original_id = original.id
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    caller_pid = cast(object, db_session.scalar(text("SELECT pg_backend_pid()")))
    waiting_pids: Queue[int] = Queue()

    def rotate() -> int:
        with _observed_session(database, waiting_pids) as writer, writer.begin():
            _ = writer.execute(text("SET LOCAL lock_timeout='15s'"))
            _ = writer.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            row = registry.scoped_key(writer, bank_id=rotation_bank, organization_id=ORG_1)
            return registry.rotate(writer, row, NEXT_KEY, lambda _name: provider)

    with ThreadPoolExecutor(max_workers=1) as pool:
        rotating = pool.submit(rotate)
        try:
            writer_pid = waiting_pids.get(timeout=10)
            assert writer_pid != caller_pid
            _wait_for_row_lock(database, writer_pid)
            assert not rotating.done()
            if rotation_bank == SIBLING:
                with Session(database) as observer:
                    assert (
                        cast(
                            object,
                            observer.scalar(
                                text("SELECT wait_event FROM pg_stat_activity WHERE pid=:pid"),
                                {"pid": writer_pid},
                            ),
                        )
                        == "advisory"
                    )
            _exercise(operation, db_session, storage, path, package, bank)
            assert opened_sessions == 0
            db_session.commit()
        finally:
            db_session.rollback()
        rotating.result(timeout=10)
    if rotation_bank == SIBLING:
        row = registry.scoped_key(db_session, bank_id=BANK_A, organization_id=ORG_1)
        assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: provider) > 0
        db_session.commit()
    provider.set_status(KEY, KeyStatus.DISABLED)
    db_session.expunge_all()
    restored = db_session.get(CanonicalCounterparty, original_id)
    assert restored is not None and restored.name == NAME
    if operation in ("ingestion", "push"):
        assert (
            find_counterparties(db_session, CTX, BANK_A, field="source_reference", value=REFERENCE)[
                0
            ].name
            == NAME
        )
    db_session.commit()
    encrypted_objects = [
        *storage.list(SLUG, "raw"),
        *storage.list(SLUG, "outputs"),
        *storage.list(SLUG, "temp"),
    ]
    assert encrypted_objects
    for descriptor in encrypted_objects:
        _, body = storage.read(descriptor.location, descriptor.version_id)
        with body:
            assert body.read()
    assert (
        db_session.scalar(
            select(ObjectKeyEnvelope.id).where(ObjectKeyEnvelope.bank_id == BANK_A).limit(1)
        )
        is not None
    )


def test_bank_key_detection_uses_the_unit_of_work_session(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.storage import encryption

    def no_secondary_factory() -> None:
        pytest.fail("A session-bound storage read must not obtain another session factory.")

    monkeypatch.setattr(encryption.database, "get_sessionmaker", no_secondary_factory)
    bound = ObjectEncryption().for_session(db_session)
    assert bound.bank_key_required(StorageLocation(SLUG, "raw", "synthetic.csv"))
    assert not bound.bank_key_required(
        StorageLocation("synthetic-optional", "raw", "synthetic.csv")
    )


def test_bound_envelopes_follow_caller_commit_and_rollback(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    bound = store.for_session(db_session)
    context = {"purpose": "synthetic-owned-transaction"}
    pending = bound.prepare(SLUG, context)
    assert bound.open(SLUG, pending.envelope_id, context).plaintext == pending.plaintext
    with Session(database) as peer:
        assert peer.get(ObjectKeyEnvelope, pending.envelope_id) is None
    db_session.commit()
    assert store.open(SLUG, pending.envelope_id, context).plaintext == pending.plaintext
    rolled_back = bound.prepare(SLUG, {"purpose": "synthetic-rollback"})
    db_session.rollback()
    with Session(database) as peer:
        assert peer.get(ObjectKeyEnvelope, rolled_back.envelope_id) is None
    assert store.open(SLUG, pending.envelope_id, context).plaintext == pending.plaintext
