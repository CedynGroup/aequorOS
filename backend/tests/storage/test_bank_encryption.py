from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, cast

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope, RetainedBankKey
from app.core.key_management.schemas import BankKeyRotate
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.db.base import utc_now
from app.identity.public import Bank
from app.operator.services.bank_encryption import rotate_key
from app.storage.access_log import HashChainedAccessLog
from app.storage.api import router
from app.storage.client import (
    StorageAccessError,
    StorageError,
    StorageLocation,
    StorageNotFoundError,
    Tier,
)
from app.storage.config import StorageEngineSettings, get_storage_settings
from app.storage.downloads import verify
from app.storage.encryption import ObjectEncryption
from app.storage.factory import get_storage_client
from app.storage.provisioning import ProvisioningClient
from app.storage.s3_compatible import S3CompatibleStorageClient
from scripts.backup_storage import StorageBackupReport, inventory_bucket, restore_storage_backup
from tests.storage.contract import metadata_for
from tests.support.helpers import ORG_1

KEY = KeyReference(
    "aws_kms", "arn:aws:kms:us-east-1:123456789012:key/first", "us-east-1", "123456789012"
)
NEXT_KEY = KeyReference(
    "aws_kms", "arn:aws:kms:us-east-1:123456789012:key/next", "us-east-1", "123456789012"
)
SLUG = "bk-samp0001"


class _Body(Protocol):
    def read(self) -> bytes: ...


class _S3(ProvisioningClient, Protocol):
    def list_buckets(self) -> dict[str, object]: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...

    def put_object(self, **kwargs: object) -> object: ...

    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


@dataclass
class BankStorage:
    storage: S3CompatibleStorageClient
    s3: _S3
    provider: LocalKeyProvider
    log: HashChainedAccessLog


@pytest.fixture
def bank_storage(db_session: Session) -> Iterator[BankStorage]:
    db_session.add(
        Bank(
            id="BK-SAMP0001",
            organization_id=ORG_1,
            name="Synthetic Bank",
            short_name="Synthetic",
            jurisdiction_code="GH",
            currency="GHS",
            license_type="universal_bank",
            institution_type="universal_bank",
            storage_slug=SLUG,
        )
    )
    db_session.flush()
    provider = LocalKeyProvider()
    provider.add_key(KEY)
    provider.add_key(NEXT_KEY)
    registry.register(
        db_session,
        bank_id="BK-SAMP0001",
        organization_id=ORG_1,
        storage_slug=SLUG,
        key=KEY,
        providers=lambda _name: provider,
    )
    db_session.commit()
    with mock_aws():
        factory = cast(Callable[..., BaseClient], boto3.client)
        s3 = factory("s3", region_name="us-east-1")
        store = registry.DatabaseEnvelopeStore(
            lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
            lambda _name: provider,
        )
        settings = StorageEngineSettings(
            _env_file=None,  # type: ignore[call-arg] - pydantic-settings runtime option
            STORAGE_BACKEND="s3",
            STORAGE_ENV="dev",
            S3_ENDPOINT=None,  # Moto emulates AWS S3, even when CI exports a MinIO endpoint.
            S3_REGION="us-east-1",
        )  # type: ignore[call-arg] - pydantic-settings runtime constructor options
        log = HashChainedAccessLog(identity="synthetic-storage")
        storage = S3CompatibleStorageClient(
            settings,
            client_factory=lambda *_args, **_kwargs: s3,
            encryption=ObjectEncryption(store),
            access_log=log,
        )
        storage.ensure_institution(SLUG)
        yield BankStorage(storage, cast(_S3, cast(object, s3)), provider, log)


@pytest.mark.parametrize("tier", ["raw", "canonical", "outputs", "temp"])
def test_all_bank_object_tiers_encrypt_at_rest_and_refuse_revocation(
    bank_storage: BankStorage,
    tier: Tier,
) -> None:
    location = StorageLocation(SLUG, tier, "fixture/report.csv")
    content = b"synthetic bank financial data"
    bank_storage.storage.write(location, io.BytesIO(content), metadata_for(SLUG, tier, content))
    response = bank_storage.s3.get_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path
    )
    raw = cast(_Body, response["Body"]).read()
    assert content not in raw
    _, decrypted = bank_storage.storage.read(location)
    assert decrypted.read() == content
    decrypted.close()
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(location)
    with pytest.raises(StorageAccessError):
        bank_storage.storage.write(location, io.BytesIO(content), metadata_for(SLUG, tier, content))


def test_rotation_changes_only_wrappers_and_keeps_old_versions_readable(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    location = StorageLocation(SLUG, "outputs", "filed/package.pdf")
    original = bank_storage.storage.write(
        location, io.BytesIO(b"first"), metadata_for(SLUG, "outputs", b"first")
    )
    bank_storage.storage.write(
        location, io.BytesIO(b"second"), metadata_for(SLUG, "outputs", b"second")
    )
    before = cast(
        _Body,
        bank_storage.s3.get_object(Bucket=location.bucket_name("dev"), Key=location.object_path)[
            "Body"
        ],
    ).read()
    wrappers = list(db_session.scalars(select(ObjectKeyEnvelope)))
    old_wrappers = [row.wrapped_key for row in wrappers]
    row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
    assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider) == 2
    db_session.commit()
    db_session.expire_all()
    assert [row.wrapped_key for row in wrappers] != old_wrappers
    after = cast(
        _Body,
        bank_storage.s3.get_object(Bucket=location.bucket_name("dev"), Key=location.object_path)[
            "Body"
        ],
    ).read()
    assert after == before
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    _, current = bank_storage.storage.read(location)
    assert current.read() == b"second"
    current.close()
    _, historical = bank_storage.storage.read(location, original.version_id)
    assert historical.read() == b"first"
    historical.close()


def test_object_substitution_and_legacy_plaintext_are_refused(bank_storage: BankStorage) -> None:
    location = StorageLocation(SLUG, "raw", "upload.csv")
    bank_storage.storage.write(
        location, io.BytesIO(b"original"), metadata_for(SLUG, "raw", b"original")
    )
    response = bank_storage.s3.get_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path
    )
    encrypted = cast(_Body, response["Body"]).read()
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key="substitute.csv",
        Body=encrypted,
        Metadata=response["Metadata"],
    )
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(StorageLocation(SLUG, "raw", "substitute.csv"))
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key="legacy.csv",
        Body=b"legacy",
        Metadata=metadata_for(SLUG, "raw", b"legacy").to_object_metadata(),
    )
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(StorageLocation(SLUG, "raw", "legacy.csv"))


@pytest.mark.parametrize("head_denied", [False, True])
def test_download_link_checks_key_at_redemption_and_disallows_direct_upload(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    head_denied: bool,
) -> None:
    location = StorageLocation(SLUG, "outputs", "exports/report.csv")
    original = bank_storage.storage.write(
        location, io.BytesIO(b"export"), metadata_for(SLUG, "outputs", b"export")
    )

    def refuse_body_read(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("Issuing a capability must not read an object body.")

    class UnreadBody(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("Issuing a capability must not consume the object body.")

    headers_body = UnreadBody(b"synthetic body")
    get_object = bank_storage.s3.get_object

    def denied_head(**_kwargs: object) -> dict[str, object]:
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

    def headers_only(**kwargs: object) -> dict[str, object]:
        response = get_object(Bucket=str(kwargs["Bucket"]), Key=str(kwargs["Key"]))
        cast(BinaryIO, response["Body"]).close()
        response["Body"] = headers_body
        return response

    with monkeypatch.context() as issuing:
        if head_denied:
            issuing.setattr(bank_storage.s3, "head_object", denied_head)
            issuing.setattr(bank_storage.s3, "get_object", headers_only)
        else:
            issuing.setattr(bank_storage.s3, "get_object", refuse_body_read)
        link = bank_storage.storage.presigned_url(location, "read", 120)
    if head_denied:
        assert headers_body.closed
    else:
        headers_body.close()
    assert verify(link.split("token=", 1)[1]).version == original.version_id
    bank_storage.storage.write(
        location, io.BytesIO(b"new export"), metadata_for(SLUG, "outputs", b"new export")
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_storage_client] = lambda: bank_storage.storage
    with TestClient(app) as client:
        response = client.get(link)
        assert response.status_code == 200
        assert response.content == b"export"
        assert response.headers["cache-control"] == "private, no-store"
        bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
        assert client.get(link).status_code == 503
    with pytest.raises(StorageAccessError):
        bank_storage.storage.presigned_url(location, "write")


def test_sibling_banks_have_independent_keys_and_cannot_substitute_envelopes(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    sibling_slug = "bk-samp0002"
    db_session.add(
        Bank(
            id="BK-SAMP0002",
            organization_id=ORG_1,
            name="Sibling Bank",
            short_name="Sibling",
            jurisdiction_code="GH",
            currency="GHS",
            license_type="universal_bank",
            institution_type="universal_bank",
            storage_slug=sibling_slug,
        )
    )
    db_session.flush()
    registry.register(
        db_session,
        bank_id="BK-SAMP0002",
        organization_id=ORG_1,
        storage_slug=sibling_slug,
        key=NEXT_KEY,
        providers=lambda _name: bank_storage.provider,
    )
    db_session.commit()
    bank_storage.storage.ensure_institution(sibling_slug)
    first = StorageLocation(SLUG, "raw", "upload.csv")
    second = StorageLocation(sibling_slug, "raw", "upload.csv")
    bank_storage.storage.write(
        first, io.BytesIO(b"first bank"), metadata_for(SLUG, "raw", b"first bank")
    )
    bank_storage.storage.write(
        second, io.BytesIO(b"second bank"), metadata_for(sibling_slug, "raw", b"second bank")
    )
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    _, body = bank_storage.storage.read(second)
    assert body.read() == b"second bank"
    body.close()
    response = bank_storage.s3.get_object(Bucket=first.bucket_name("dev"), Key=first.object_path)
    _ = bank_storage.s3.put_object(
        Bucket=second.bucket_name("dev"),
        Key=second.object_path,
        Body=cast(_Body, response["Body"]).read(),
        Metadata=response["Metadata"],
    )
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(second)


@pytest.mark.parametrize("failure", ["replacement-decrypt", "second-wrapper"])
def test_failed_rotation_rolls_back_every_wrapper_and_keeps_reads_available(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    location = StorageLocation(SLUG, "outputs", "filed/package.pdf")
    first = bank_storage.storage.write(
        location, io.BytesIO(b"first"), metadata_for(SLUG, "outputs", b"first")
    )
    bank_storage.storage.write(
        location, io.BytesIO(b"second"), metadata_for(SLUG, "outputs", b"second")
    )
    wrappers = {row.id: row.wrapped_key for row in db_session.scalars(select(ObjectKeyEnvelope))}
    unwrap = bank_storage.provider.unwrap
    rotate = bank_storage.provider.rotate
    rotated = 0

    def refuse_target(key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        if key == NEXT_KEY:
            raise KeyUnavailableError("Synthetic target decrypt grant was denied.")
        return unwrap(key, wrapped, context)

    def refuse_second(
        source: KeyReference, target: KeyReference, wrapped: bytes, context: dict[str, str]
    ) -> bytes:
        nonlocal rotated
        rotated += 1
        if rotated == 2:
            raise KeyUnavailableError("Synthetic mid-rotation outage.")
        return rotate(source, target, wrapped, context)

    if failure == "replacement-decrypt":
        monkeypatch.setattr(bank_storage.provider, "unwrap", refuse_target)
    else:
        monkeypatch.setattr(bank_storage.provider, "rotate", refuse_second)
    with pytest.raises(HTTPException) as denied:
        rotate_key(
            db_session,
            ORG_1,
            "BK-SAMP0001",
            BankKeyRotate(
                key_id=NEXT_KEY.key_id,
                region=NEXT_KEY.region,
                owner_account=NEXT_KEY.owner_account,
                reason="Synthetic rotation rollback check",
            ),
            providers=lambda _name: bank_storage.provider,
        )
    assert denied.value.status_code == 503
    db_session.expire_all()
    row = db_session.scalar(
        select(BankEncryptionKey).where(BankEncryptionKey.bank_id == "BK-SAMP0001")
    )
    assert row is not None and row.key_id == KEY.key_id
    assert {
        row.id: row.wrapped_key for row in db_session.scalars(select(ObjectKeyEnvelope))
    } == wrappers
    _, historical = bank_storage.storage.read(location, first.version_id)
    assert historical.read() == b"first"
    historical.close()
    _, current = bank_storage.storage.read(location)
    assert current.read() == b"second"
    current.close()


def test_backup_wipe_restore_reapplies_encryption_headers_and_reads(
    bank_storage: BankStorage,
    tmp_path: Path,
    db_session: Session,
) -> None:
    location = StorageLocation(SLUG, "raw", "fixture/report.csv")
    content = b"synthetic recovery fixture"
    bank_storage.storage.write(location, io.BytesIO(content), metadata_for(SLUG, "raw", content))
    bucket = location.bucket_name("dev")
    record = inventory_bucket(bank_storage.s3, bucket, out_dir=tmp_path)
    assert record.ok and record.objects == 1
    assert record.keys[0].metadata["key-envelope-id"]
    assert record.keys[0].metadata["encryption-format"]
    assert record.keys[0].metadata["checksum-sha256"]
    manifest = tmp_path / "manifest.json"
    StorageBackupReport("synthetic", "moto", True, [record], format_version=2).write(manifest)
    archived = {row.id: row.wrapped_key for row in db_session.scalars(select(ObjectKeyEnvelope))}
    row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
    registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider)
    db_session.commit()
    _ = bank_storage.s3.delete_object(Bucket=bucket, Key=location.object_path)
    with pytest.raises(StorageNotFoundError):
        bank_storage.storage.read(location)
    row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
    row.key_id = KEY.key_id
    for envelope in db_session.scalars(select(ObjectKeyEnvelope)):
        envelope.wrapped_key = archived[envelope.id]
    _ = db_session.execute(delete(RetainedBankKey))
    db_session.commit()
    assert restore_storage_backup(bank_storage.s3, manifest, out_dir=tmp_path) == 1
    _, body = bank_storage.storage.read(location)
    assert body.read() == content
    body.close()


def test_rotation_preserves_archived_envelopes_until_retention_expires(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "7")
    get_key_settings.cache_clear()
    try:
        now = utc_now()
        monkeypatch.setattr(registry, "utc_now", lambda: now)
        context = {"purpose": "synthetic-backup"}
        store = registry.DatabaseEnvelopeStore(
            lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
            lambda _name: bank_storage.provider,
        )
        original = store.prepare(SLUG, context)
        envelope = db_session.get(ObjectKeyEnvelope, original.envelope_id)
        assert envelope is not None
        archived_wrapper = envelope.wrapped_key
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider) == 1
        db_session.commit()
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        with pytest.raises(KeyUnavailableError, match="retention expires"):
            registry.authorize_retirement(db_session, row, KEY)
        assert (
            bank_storage.provider.unwrap(
                KEY,
                archived_wrapper,
                registry.wrapping_context(row.bank_id, registry.context_digest(context)),
            )
            == original.plaintext
        )
        assert store.open(SLUG, original.envelope_id, context).plaintext == original.plaintext
        held = db_session.scalar(select(RetainedBankKey))
        assert held is not None and held.decrypt_until is not None
        monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "14")
        get_key_settings.cache_clear()
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=8))
        with pytest.raises(KeyUnavailableError, match="retention expires"):
            registry.authorize_retirement(db_session, row, KEY)
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=13, hours=18))
        with pytest.raises(KeyUnavailableError, match="retention expires"):
            registry.authorize_retirement(db_session, row, KEY)
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=15))
        registry.authorize_retirement(db_session, row, KEY)
        bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
        assert bank_storage.provider.describe(KEY).status == KeyStatus.DISABLED
        assert store.open(SLUG, original.envelope_id, context).plaintext == original.plaintext
    finally:
        get_key_settings.cache_clear()


def test_unconfigured_backup_retention_refuses_source_retirement(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENCRYPTION_BACKUP_RETENTION_DAYS", raising=False)
    get_key_settings.cache_clear()
    try:
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider)
        with pytest.raises(KeyUnavailableError, match="indefinite"):
            registry.authorize_retirement(db_session, row, KEY)
        assert bank_storage.provider.describe(KEY).status == KeyStatus.ACTIVE
    finally:
        get_key_settings.cache_clear()


@pytest.mark.parametrize("repeat_retention, deadline_days", [(1, 7), (7, 10), (None, 100)])
def test_reusing_rotation_destinations_preserves_per_bank_backup_holds(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    repeat_retention: int | None,
    deadline_days: int,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "7")
    get_key_settings.cache_clear()
    try:
        now = utc_now()
        monkeypatch.setattr(registry, "utc_now", lambda: now)
        location = StorageLocation(SLUG, "outputs", "fixture/reused-key.csv")
        content = b"synthetic reusable key content"
        bank_storage.storage.write(
            location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
        )
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider) == 1
        db_session.commit()
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=1))
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        assert registry.rotate(db_session, row, KEY, lambda _name: bank_storage.provider) == 1
        db_session.commit()
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        with pytest.raises(KeyUnavailableError, match="still connected"):
            registry.authorize_retirement(db_session, row, KEY)
        bank_storage.storage.write(
            StorageLocation(SLUG, "temp", "fixture/reused-key.csv"),
            io.BytesIO(content),
            metadata_for(SLUG, "temp", content),
        )
        if repeat_retention is None:
            monkeypatch.delenv("ENCRYPTION_BACKUP_RETENTION_DAYS")
        else:
            monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", str(repeat_retention))
        get_key_settings.cache_clear()
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=3))
        assert registry.rotate(db_session, row, NEXT_KEY, lambda _name: bank_storage.provider) == 2
        db_session.commit()
        holds = list(db_session.scalars(select(RetainedBankKey)))
        assert len(holds) == 2
        _, body = bank_storage.storage.read(location)
        assert body.read() == content
        body.close()
        row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        if repeat_retention is None:
            monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
            get_key_settings.cache_clear()
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=deadline_days - 1))
        with pytest.raises(KeyUnavailableError, match="retention expires|indefinite"):
            registry.authorize_retirement(db_session, row, KEY)
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=deadline_days + 1))
        if repeat_retention is None:
            with pytest.raises(KeyUnavailableError, match="indefinite"):
                registry.authorize_retirement(db_session, row, KEY)
        else:
            registry.authorize_retirement(db_session, row, KEY)
    finally:
        get_key_settings.cache_clear()


@pytest.mark.parametrize("register_before_rotation", [True, False])
def test_shared_source_retirement_waits_for_last_bank_and_its_backups(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    register_before_rotation: bool,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "7")
    get_key_settings.cache_clear()
    try:
        now = utc_now()
        monkeypatch.setattr(registry, "utc_now", lambda: now)
        db_session.add(
            Bank(
                id="BK-SAMP0002",
                organization_id=ORG_1,
                name="Synthetic sibling",
                short_name="Sibling",
                jurisdiction_code="GH",
                currency="GHS",
                license_type="universal_bank",
                institution_type="universal_bank",
                storage_slug="bk-samp0002",
            )
        )
        db_session.flush()
        if not register_before_rotation:
            first = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(db_session, first, NEXT_KEY, lambda _name: bank_storage.provider)
            db_session.commit()
        registry.register(
            db_session,
            bank_id="BK-SAMP0002",
            organization_id=ORG_1,
            storage_slug="bk-samp0002",
            key=KEY,
            providers=lambda _name: bank_storage.provider,
        )
        store = registry.DatabaseEnvelopeStore(
            lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
            lambda _name: bank_storage.provider,
        )
        context = {"purpose": "shared-key-fixture"}
        archived = store.prepare("bk-samp0002", context)
        if register_before_rotation:
            first = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(db_session, first, NEXT_KEY, lambda _name: bank_storage.provider)
        db_session.commit()
        bank_storage.storage.ensure_institution("bk-samp0002")
        tiers: tuple[Tier, ...] = ("raw", "canonical", "outputs", "temp")
        for tier in tiers:
            location = StorageLocation("bk-samp0002", tier, "fixture/shared-key.csv")
            content = b"synthetic sibling content"
            bank_storage.storage.write(
                location, io.BytesIO(content), metadata_for("bk-samp0002", tier, content)
            )
            _, body = bank_storage.storage.read(location)
            assert body.read() == content
            body.close()
        assert store.prepare("bk-samp0002", context).key_id == KEY.key_id
        assert (
            store.open("bk-samp0002", archived.envelope_id, context).plaintext == archived.plaintext
        )
        first = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=8))
        with pytest.raises(KeyUnavailableError, match="still connected"):
            registry.authorize_retirement(db_session, first, KEY)
        sibling = registry.scoped_key(db_session, bank_id="BK-SAMP0002", organization_id=ORG_1)
        registry.rotate(db_session, sibling, NEXT_KEY, lambda _name: bank_storage.provider)
        db_session.commit()
        first = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
        with pytest.raises(KeyUnavailableError, match="retention expires"):
            registry.authorize_retirement(db_session, first, KEY)
        assert bank_storage.provider.describe(KEY).status == KeyStatus.ACTIVE
        monkeypatch.setattr(registry, "utc_now", lambda: now + timedelta(days=16))
        registry.authorize_retirement(db_session, first, KEY)
        bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
        assert bank_storage.provider.describe(KEY).status == KeyStatus.DISABLED
    finally:
        get_key_settings.cache_clear()


@pytest.mark.parametrize("failure", ["AccessDenied", "NoSuchVersion", "transport"])
@pytest.mark.parametrize("consumer", ["read", "versioned-read", "duplicate-write", "download"])
def test_s3_read_failures_are_audited_without_download_credentials(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    consumer: str,
) -> None:
    location = StorageLocation(SLUG, "outputs", "exports/audit.csv")
    content = b"synthetic export"
    metadata = metadata_for(SLUG, "outputs", content)
    original = bank_storage.storage.write(location, io.BytesIO(content), metadata)
    link = bank_storage.storage.presigned_url(location, "read")

    def refuse(**_kwargs: object) -> dict[str, object]:
        if failure == "transport":
            raise EndpointConnectionError(endpoint_url="https://synthetic.test?token=secret")
        raise ClientError({"Error": {"Code": failure, "Message": "token=secret"}}, "GetObject")

    monkeypatch.setattr(bank_storage.s3, "get_object", refuse)
    bank_storage.log.entries.clear()
    if consumer == "download":
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_storage_client] = lambda: bank_storage.storage
        with TestClient(app) as client:
            assert client.get(link).status_code == 503
    else:
        with pytest.raises(StorageError):
            if consumer == "duplicate-write":
                bank_storage.storage.write(location, io.BytesIO(content), metadata)
            else:
                bank_storage.storage.read(
                    location, original.version_id if consumer == "versioned-read" else None
                )
    assert [(entry.operation, entry.result) for entry in bank_storage.log.entries] == [
        ("read", "backend_error" if failure == "transport" else failure),
    ]
    assert "token=" not in bank_storage.log.export_jsonl()
    assert link.split("token=", 1)[1] not in bank_storage.log.export_jsonl()


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("failure", ["AccessDenied", "transport"])
def test_platform_presigned_failures_are_audited(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["read", "write"],
    failure: str,
) -> None:
    monkeypatch.setattr(ObjectEncryption, "bank_key_required", lambda _self, _location: False)
    location = StorageLocation(SLUG, "outputs", "rollout/signing.bin")

    def refuse(*_args: object, **_kwargs: object) -> str:
        if failure == "transport":
            raise EndpointConnectionError(endpoint_url="https://synthetic.test?token=secret")
        raise ClientError({"Error": {"Code": failure, "Message": "token=secret"}}, "Sign")

    monkeypatch.setattr(bank_storage.s3, "generate_presigned_url", refuse)
    bank_storage.log.entries.clear()
    with pytest.raises(StorageError):
        bank_storage.storage.presigned_url(location, operation)
    assert [(entry.operation, entry.result) for entry in bank_storage.log.entries] == [
        (f"presigned_url.{operation}", "backend_error" if failure == "transport" else failure),
    ]
    assert "secret" not in bank_storage.log.export_jsonl()
    assert "token=" not in bank_storage.log.export_jsonl()


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("failure", ["runtime", "database"])
def test_presigned_configuration_refusals_are_audited(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["read", "write"],
    failure: str,
) -> None:
    def refuse(_self: ObjectEncryption, _location: StorageLocation) -> bool:
        if failure == "runtime":
            raise RuntimeError("token=secret")
        raise SQLAlchemyError("token=secret")

    monkeypatch.setattr(ObjectEncryption, "bank_key_required", refuse)
    location = StorageLocation(SLUG, "outputs", "exports/audit.csv")
    bank_storage.log.entries.clear()
    with pytest.raises(StorageAccessError):
        bank_storage.storage.presigned_url(location, operation)
    assert [(entry.operation, entry.result) for entry in bank_storage.log.entries] == [
        (f"presigned_url.{operation}", "configuration-refused"),
    ]
    assert "secret" not in bank_storage.log.export_jsonl()


@pytest.mark.parametrize("outcome", ["success", "upload", "expiry", "secret", "base-url", "metadata"])
def test_bank_presigned_outcomes_are_audited(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    location = StorageLocation(SLUG, "outputs", "exports/audit.csv")
    content = b"synthetic export"
    original = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    monkeypatch.setenv("STORAGE_DOWNLOAD_BASE_URL", "")
    if outcome == "secret":
        monkeypatch.setenv("AUTH_JWT_SECRET", "")
    elif outcome == "base-url":
        monkeypatch.setenv("STORAGE_DOWNLOAD_BASE_URL", "invalid")
    elif outcome == "metadata":
        location = StorageLocation(SLUG, "outputs", "exports/missing.csv")
    get_settings.cache_clear()
    get_storage_settings.cache_clear()
    bank_storage.log.entries.clear()
    try:
        if outcome == "success":
            url = bank_storage.storage.presigned_url(location, "read")
            assert verify(url.split("token=", 1)[1]).version == original.version_id
            assert url.split("token=", 1)[1] not in bank_storage.log.export_jsonl()
            expected = ("presigned_url.read", "success")
            assert bank_storage.log.entries[0].version_id == original.version_id
        else:
            with pytest.raises((StorageError, RuntimeError)):
                bank_storage.storage.presigned_url(
                    location,
                    "write" if outcome == "upload" else "read",
                    901 if outcome == "expiry" else 900,
                )
            result = "signing-refused"
            if outcome == "upload":
                result = "key-refused"
            elif outcome == "metadata":
                result = "404"
            expected = ("presigned_url.write" if outcome == "upload" else "presigned_url.read", result)
        assert [(entry.operation, entry.result) for entry in bank_storage.log.entries] == [expected]
        assert "token=" not in bank_storage.log.export_jsonl()
    finally:
        get_settings.cache_clear()
        get_storage_settings.cache_clear()
