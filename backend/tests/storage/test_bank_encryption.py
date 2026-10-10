from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import boto3
import pytest
from botocore.client import BaseClient
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.schemas import BankKeyRotate
from app.core.key_management.types import KeyReference, KeyStatus, KeyUnavailableError
from app.identity.public import Bank
from app.operator.services.bank_encryption import rotate_key
from app.storage.api import router
from app.storage.client import StorageAccessError, StorageLocation, Tier
from app.storage.config import StorageEngineSettings
from app.storage.encryption import ObjectEncryption
from app.storage.factory import get_storage_client
from app.storage.s3_compatible import S3CompatibleStorageClient
from scripts.backup_storage import inventory_bucket
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


class _S3(Protocol):
    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...

    def put_object(self, **kwargs: object) -> object: ...


@dataclass
class BankStorage:
    storage: S3CompatibleStorageClient
    s3: _S3
    provider: LocalKeyProvider


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
            S3_REGION="us-east-1",
        )  # type: ignore[call-arg] - pydantic-settings runtime constructor options
        storage = S3CompatibleStorageClient(
            settings,
            client_factory=lambda *_args, **_kwargs: s3,
            encryption=ObjectEncryption(store),
        )
        storage.ensure_institution(SLUG)
        yield BankStorage(storage, cast(_S3, cast(object, s3)), provider)


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
    # An idempotent write must also contact the key service.
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


def test_backup_keeps_ciphertext_and_revocation_blocks_restored_copy(
    bank_storage: BankStorage,
    tmp_path: Path,
) -> None:
    location = StorageLocation(SLUG, "outputs", "filed.pdf")
    content = b"filed synthetic package"
    bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    record = inventory_bucket(bank_storage.s3, location.bucket_name("dev"), out_dir=tmp_path)
    assert record.ok
    saved = (tmp_path / location.bucket_name("dev") / location.object_path).read_bytes()
    assert content not in saved
    assert record.keys[0].metadata["key-envelope-id"]
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=saved,
        Metadata=record.keys[0].metadata,
    )
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(location)


def test_download_link_checks_key_at_redemption_and_disallows_direct_upload(
    bank_storage: BankStorage,
) -> None:
    location = StorageLocation(SLUG, "temp", "exports/report.csv")
    bank_storage.storage.write(
        location, io.BytesIO(b"export"), metadata_for(SLUG, "temp", b"export")
    )
    link = bank_storage.storage.presigned_url(location, "read", 120)
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


def test_key_health_recovers_after_outage(bank_storage: BankStorage, db_session: Session) -> None:
    row = db_session.scalar(
        select(BankEncryptionKey).where(BankEncryptionKey.bank_id == "BK-SAMP0001")
    )
    assert row is not None
    bank_storage.provider.set_status(KEY, KeyStatus.UNAVAILABLE)
    assert (
        registry.check_health(row, lambda _name: bank_storage.provider).status
        == KeyStatus.UNAVAILABLE
    )
    bank_storage.provider.set_status(KEY, KeyStatus.ACTIVE)
    assert (
        registry.check_health(row, lambda _name: bank_storage.provider).status == KeyStatus.ACTIVE
    )


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
