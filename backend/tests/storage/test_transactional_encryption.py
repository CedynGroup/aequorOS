from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Literal

import pytest
from botocore.exceptions import EndpointConnectionError  # pyright: ignore[reportMissingTypeStubs]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.orm import Session, SessionTransaction, undefer_group

from app.api import deps
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import ObjectKeyEnvelope
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.features import ingest_data
from app.identity.public import Bank
from app.models.canonical import CanonicalCounterparty
from app.reporting.api import manage_package_attachments, manage_regulatory_reporting
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import attachments, exports
from app.reporting.service.registry import get_definition
from app.reporting.service.templates import get_template
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.schemas.push import PushBatchOpen, PushRecordsPage
from app.services import ingestion, push_ingestion
from app.storage import encryption
from app.storage.client import StorageError, StorageLocation
from app.storage.config import StorageEngineSettings
from app.storage.encryption import ObjectEncryption
from app.storage.s3_compatible import S3CompatibleStorageClient
from tests.core.test_bank_field_encryption import AS_OF, BANK_A, CTX, NAME, counterparty
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
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


def _rotation_storage(db_session: Session, bank_storage: BankStorage) -> S3CompatibleStorageClient:
    database = db_session.get_bind()

    def budget(_db: Session, _transaction: SessionTransaction, connection: Connection) -> None:
        _ = connection.exec_driver_sql("SET LOCAL lock_timeout='1s'")

    def independent_session() -> Session:
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
            registry.DatabaseEnvelopeStore(independent_session, lambda _name: bank_storage.provider)
        ),
    )
    event.listen(db_session, "after_begin", budget)
    return storage


def _exercise(
    operation: Operation,
    db: Session,
    storage: S3CompatibleStorageClient,
    path: Path,
    package: RegulatoryPackage,
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
        bank = db.get(Bank, BANK_A)
        assert bank is not None
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


def _register_sibling(db_session: Session, providers: registry.ProviderFactory) -> None:
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
        providers=providers,
    )
    db_session.commit()


@pytest.mark.parametrize(
    "scenario",
    [
        (bank, operation)
        for bank in (BANK_A, SIBLING)
        for operation in ("ingestion", "push", "export", "attachment")
    ],
)
def test_transactional_storage_runs_during_version_rotation(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: tuple[str, Operation],
) -> None:
    rotation_bank, operation = scenario
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL lock queues and independent connections are required.")

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    _register_sibling(db_session, providers)
    path = tmp_path / "synthetic.xlsx"
    if operation == "ingestion":
        _workbook(db_session, path)
    package = _package(db_session)

    storage = _rotation_storage(db_session, bank_storage)
    monkeypatch.setattr(exports, "get_storage_client", lambda: storage)
    original_id = counterparty(db_session, include_package=False).id
    entered, release = Event(), Event()

    def rotate() -> int:
        with Session(database) as writer, writer.begin():
            _ = writer.execute(text("SET LOCAL lock_timeout='1s'"))
            row = registry.scoped_key(writer, bank_id=rotation_bank, organization_id=ORG_1)
            count = registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)
            entered.set()
            assert release.wait(15)
            return count

    with ThreadPoolExecutor(max_workers=1) as pool:
        rotating = pool.submit(rotate)
        try:
            assert entered.wait(10)
            _exercise(operation, db_session, storage, path, package)
            db_session.commit()
        finally:
            db_session.rollback()
            release.set()
        assert rotating.result(timeout=10) == 0
    if rotation_bank == SIBLING:
        row = registry.scoped_key(db_session, bank_id=BANK_A, organization_id=ORG_1)
        assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider) == 0
        db_session.commit()
    db_session.expunge_all()
    restored = db_session.get(CanonicalCounterparty, original_id)
    assert restored is not None and restored.name == NAME
    if operation in ("ingestion", "push"):
        ingested = list(
            db_session.scalars(
                select(CanonicalCounterparty)
                .options(undefer_group("bank_identity"))
                .where(
                    CanonicalCounterparty.organization_id == ORG_1,
                    CanonicalCounterparty.bank_id == BANK_A,
                    CanonicalCounterparty.superseded_by.is_(None),
                    CanonicalCounterparty.withdrawn_at.is_(None),
                )
            )
        )
        assert any(row.source_reference == REFERENCE and row.name == NAME for row in ingested)
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


def test_bank_key_detection_uses_the_unit_of_work_session(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def no_secondary_factory() -> None:
        pytest.fail("A session-bound storage read must not obtain another session factory.")

    monkeypatch.setattr(encryption.database, "get_sessionmaker", no_secondary_factory)
    bound = ObjectEncryption().for_session(db_session)
    assert bound.bank_key_required(StorageLocation(SLUG, "raw", "synthetic.csv"))
    assert not bound.bank_key_required(
        StorageLocation("synthetic-optional", "raw", "synthetic.csv")
    )


def test_committed_key_versions_survive_caller_rollback(
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
        assert peer.get(ObjectKeyEnvelope, pending.envelope_id) is not None
    db_session.commit()
    assert store.open(SLUG, pending.envelope_id, context).plaintext == pending.plaintext
    rolled_back = bound.prepare(SLUG, {"purpose": "synthetic-rollback"})
    db_session.rollback()
    with Session(database) as peer:
        assert peer.get(ObjectKeyEnvelope, rolled_back.envelope_id) is not None
    assert store.open(SLUG, pending.envelope_id, context).plaintext == pending.plaintext


def test_published_push_manifest_remains_readable_after_stat_failure_and_rollback(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    storage = bank_storage.storage
    opened = push_ingestion.open_push_batch(
        db_session,
        CTX,
        BANK_A,
        PushBatchOpen(
            as_of_date=AS_OF,
            idempotency_key="synthetic-publication-rollback",
            reason="Synthetic rollback test",
        ),
        storage,
    )
    db_session.commit()
    page = PushRecordsPage(
        entities={
            "counterparty": [
                {
                    "source_reference": REFERENCE,
                    "name": NAME,
                    "counterparty_type": "RETAIL_INDIVIDUAL",
                }
            ]
        }
    )
    push_ingestion.stage_push_records(db_session, CTX, BANK_A, opened.push_batch_id, page, storage)
    db_session.commit()
    target = f"push/{opened.push_batch_id}/manifest.json"
    head = bank_storage.s3.head_object
    calls = 0

    def fail_second_manifest_stat(**kwargs: object) -> object:
        nonlocal calls
        if kwargs.get("Key") == target:
            calls += 1
            if calls == 2:
                raise EndpointConnectionError(endpoint_url="https://synthetic.invalid")
        return head(**kwargs)

    with monkeypatch.context() as failure:
        failure.setattr(bank_storage.s3, "head_object", fail_second_manifest_stat)
        with pytest.raises(StorageError):
            push_ingestion.stage_push_records(
                db_session, CTX, BANK_A, opened.push_batch_id, page, storage
            )
        db_session.rollback()
    current = push_ingestion.get_push_batch(db_session, CTX, BANK_A, opened.push_batch_id, storage)
    assert current.pages_staged == 2 and current.total_records_staged == 2
    db_session.commit()
    result = push_ingestion.commit_push_batch(
        db_session, CTX, BANK_A, opened.push_batch_id, storage
    )
    assert result.batch.id is not None


def test_bulk_fields_and_objects_unwrap_once_per_version_and_discard_on_boundary(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    provider = bank_storage.provider

    def providers(_name: str) -> LocalKeyProvider:
        return provider

    monkeypatch.setattr(registry, "provider_for", providers)
    for number in range(30):
        counterparty(db_session, reference=f"SYNTHETIC-BULK-{number}", include_package=False)
    location = StorageLocation(SLUG, "raw", "synthetic/bulk.csv")
    bank_storage.storage.for_session(db_session).write(
        location, io.BytesIO(b"synthetic"), metadata_for(SLUG, "raw", b"synthetic")
    )
    db_session.commit()
    db_session.expunge_all()
    unwrap = provider.unwrap
    calls = 0

    def count(key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        nonlocal calls
        calls += 1
        return unwrap(key, wrapped, context)

    monkeypatch.setattr(provider, "unwrap", count)
    loaded = list(db_session.scalars(select(CanonicalCounterparty)))
    assert len(loaded) == 30 and all(row.name == NAME for row in loaded)
    for _ in range(3):
        _, body = bank_storage.storage.for_session(db_session).read(location)
        with body:
            assert body.read() == b"synthetic"
    assert calls == 1
    provider.set_status(KEY, KeyStatus.DISABLED)
    db_session.expunge_all()
    cached = list(db_session.scalars(select(CanonicalCounterparty)))
    assert len(cached) == 30 and all(row.name == NAME for row in cached)
    assert calls == 1
    db_session.commit()
    db_session.expunge_all()
    loaded = list(db_session.scalars(select(CanonicalCounterparty)))
    assert len(loaded) == 30
    with pytest.raises(KeyUnavailableError):
        _ = loaded[0].name
    db_session.rollback()
    with pytest.raises(KeyUnavailableError):
        bank_storage.storage.for_session(db_session).read(location)


@pytest.mark.parametrize("download", ["attachment", "email-fallback"])
def test_package_download_siblings_do_not_wait_for_rotation(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    download: str,
) -> None:

    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent connections are required.")

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    monkeypatch.setattr(ingest_data, "get_storage_client", lambda: bank_storage.storage)
    monkeypatch.setattr(exports, "get_storage_client", lambda: bank_storage.storage)
    package = _package(db_session)
    exports.export_package(db_session, CTX, package, "csv")
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    attached = attachments.upload_package_attachment(
        db_session,
        CTX,
        bank,
        package,
        bank_storage.storage,
        kind="supporting_document",
        title="Synthetic",
        filename="synthetic.pdf",
        content=b"%PDF-1.7\nSynthetic\n%%EOF\n",
        max_bytes=1024,
    )
    package_id = package.id
    db_session.commit()
    db_session.expunge_all()
    loaded, rotating, release = Event(), Event(), Event()
    app = FastAPI()
    app.include_router(manage_package_attachments.router, prefix="/api/v1")
    app.include_router(manage_regulatory_reporting.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_tenant_db_session] = lambda: db_session

    def package_access() -> deps.PackageAccess:
        row = db_session.get(RegulatoryPackage, package_id)
        institution = db_session.get(Bank, BANK_A)
        assert row is not None and institution is not None
        loaded.set()
        assert rotating.wait(10)
        return deps.PackageAccess(ctx=CTX, bank=institution, package=row)

    app.dependency_overrides[deps.require_package_view] = package_access

    def rotate() -> None:
        assert loaded.wait(10)
        with Session(database) as writer, writer.begin():
            _ = writer.execute(text("SET LOCAL lock_timeout='1s'"))
            row = registry.scoped_key(writer, bank_id=BANK_A, organization_id=ORG_1)
            registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)
            rotating.set()
            assert release.wait(10)

    suffix = (
        f"attachments/{attached.id}/download" if download == "attachment" else "email-fallback.eml"
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        rotation = pool.submit(rotate)
        response = pool.submit(
            client.get, f"/api/v1/banks/{BANK_A}/regulatory-packages/{package_id}/{suffix}"
        )
        try:
            result = response.result(timeout=8)
            assert result.status_code == 200, result.text
            assert result.content
        finally:
            release.set()
            db_session.rollback()
        rotation.result(timeout=5)


@pytest.mark.parametrize("boundary", ["rollback", "close"])
def test_cached_material_is_discarded_at_session_boundary(
    bank_storage: BankStorage,
    db_session: Session,
    boundary: str,
) -> None:

    with Session(db_session.get_bind()) as db:
        bound = registry.DatabaseEnvelopeStore(
            lambda: Session(db_session.get_bind()),
            lambda _name: bank_storage.provider,
        ).for_session(db)
        context = {"purpose": "synthetic-cache-lifetime"}
        original = bound.prepare(SLUG, context)
        bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
        assert bound.open(SLUG, original.envelope_id, context) == original
        if boundary == "rollback":
            db.rollback()
        else:
            db.close()
        with pytest.raises(KeyUnavailableError):
            bound.open(SLUG, original.envelope_id, context)


def test_uncommitted_key_version_cannot_publish_objects(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:

    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    location = StorageLocation(SLUG, "raw", "synthetic/uncommitted.csv")
    row = registry.scoped_key(db_session, bank_id=BANK_A, organization_id=ORG_1)
    registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider)
    with pytest.raises(KeyUnavailableError):
        bank_storage.storage.for_session(db_session).write(
            location,
            io.BytesIO(b"synthetic"),
            metadata_for(SLUG, "raw", b"synthetic"),
        )
    db_session.rollback()
    assert not list(bank_storage.storage.list(SLUG, "raw", "synthetic/uncommitted.csv"))
