from __future__ import annotations

import io
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.core.key_management import reencryption, registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.types import KeyUnavailableError
from app.models.risk import StoredObject
from app.storage import reencryption as objects
from app.storage.client import StorageAccessError, StorageBackendError, StorageLocation
from app.storage.encryption import ObjectEncryption
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import (
    KEY,
    NEXT_KEY,
    SLUG,
    BankStorage,
    bank_storage,
)
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]


def test_legacy_object_conversion_authenticates_checksum_and_retains_source(
    bank_storage: BankStorage,
) -> None:
    location = StorageLocation(SLUG, "raw", "synthetic/legacy.csv")
    content = b"synthetic legacy financial data"
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=content,
        Metadata=metadata_for(SLUG, "raw", content).to_object_metadata(),
    )
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(location)
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    assert item.envelope_id is None and not item.retained
    assert bank_storage.storage.reencrypt(item)
    _, body = bank_storage.storage.read(location)
    assert body.read() == content
    body.close()
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    assert len(versions) == 2
    assert sum(v.retained for v in versions) == 1
    current = next(v for v in versions if not v.retained)
    assert isinstance(current.envelope_id, UUID)
    assert not bank_storage.storage.reencrypt(item)


def test_bad_legacy_checksum_is_not_published(bank_storage: BankStorage) -> None:
    location = StorageLocation(SLUG, "temp", "synthetic/corrupt.csv")
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=b"corrupt",
        Metadata=metadata_for(SLUG, "temp", b"original").to_object_metadata(),
    )
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    with pytest.raises(StorageAccessError, match="checksum"):
        bank_storage.storage.reencrypt(item)
    assert len(list(bank_storage.storage.encryption_inventory(SLUG))) == 1


def test_object_resume_after_publication_keeps_retained_version_and_retirement_block(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = StorageLocation(SLUG, "canonical", "synthetic/current.parquet")
    bank_storage.storage.write(
        location, io.BytesIO(b"synthetic"), metadata_for(SLUG, "canonical", b"synthetic")
    )
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    original = key.current_version_id
    registry.rotate(db_session, key, NEXT_KEY, lambda _: bank_storage.provider)
    db_session.commit()

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    real_reencrypt = objects.reencrypt
    interrupted = False

    def crash_after_publication(*args: object, **kwargs: object) -> bool:
        nonlocal interrupted
        result = real_reencrypt(*args, **kwargs)  # type: ignore[arg-type]
        if not interrupted:
            interrupted = True
            raise StorageBackendError("Synthetic interruption after publication")
        return result

    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    monkeypatch.setattr(objects, "reencrypt", crash_after_publication)
    with pytest.raises(StorageBackendError, match="Synthetic interruption"):
        reencryption.run(db_session, job, bank_storage.storage)
    db_session.rollback()
    monkeypatch.setattr(objects, "reencrypt", real_reencrypt)
    reencryption.run(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete except 1 retained immutable records"
    assert progress.retained == {"stored_object_versions:canonical": 1}
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    assert len(versions) == 2 and any(v.envelope_id == original and v.retained for v in versions)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    with pytest.raises(KeyUnavailableError, match="retention"):
        registry.authorize_retirement(db_session, key, KEY)


def test_conditional_publication_preserves_concurrent_new_content(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    location = StorageLocation(SLUG, "temp", "synthetic/race.csv")
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=b"old",
        Metadata=metadata_for(SLUG, "temp", b"old").to_object_metadata(),
    )
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)

    class RacingClient:
        def head_object(self, **kwargs: object) -> dict[str, object]:
            return client.head_object(**kwargs)

        def get_object(self, **kwargs: object) -> dict[str, object]:
            return client.get_object(**kwargs)

        def get_paginator(self, operation: str) -> objects.MaintenancePaginator:
            return client.get_paginator(operation)

        def put_object(self, **kwargs: object) -> dict[str, object]:
            bank_storage.storage.write(
                location, io.BytesIO(b"new"), metadata_for(SLUG, "temp", b"new")
            )
            return client.put_object(**kwargs)

    assert not objects.reencrypt(
        RacingClient(),
        "dev",
        ObjectEncryption(
            registry.DatabaseEnvelopeStore(
                lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
                lambda _: bank_storage.provider,
            )
        ),
        item,
        None,
    )
    _, body = bank_storage.storage.read(location)
    assert body.read() == b"new"
    body.close()


def test_database_pin_retains_only_its_location_with_a_null_version(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("pinned.csv", "editable.csv"):
        location = StorageLocation(SLUG, "temp", name)
        _ = bank_storage.s3.put_object(
            Bucket=location.bucket_name("dev"),
            Key=name,
            Body=b"synthetic",
            Metadata=metadata_for(SLUG, "temp", b"synthetic").to_object_metadata(),
        )
    db_session.add(
        StoredObject(
            organization_id=ORG_1,
            provider="s3",
            bucket=StorageLocation(SLUG, "temp", "").bucket_name("dev"),
            object_key="pinned.csv",
            version_id="null",
            status="available",
        )
    )
    db_session.commit()

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    reencryption.run(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.retained == {"stored_object_versions:temp": 1}
    assert progress.objects_rewritten == 1
    inventory = {
        item.location.object_path: item for item in bank_storage.storage.encryption_inventory(SLUG)
    }
    assert inventory["pinned.csv"].envelope_id is None
    assert inventory["editable.csv"].envelope_id is not None


def test_versioned_pin_keeps_original_while_current_object_moves(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = StorageLocation(SLUG, "raw", "pinned.csv")
    stored = bank_storage.storage.write(
        location, io.BytesIO(b"synthetic"), metadata_for(SLUG, "raw", b"synthetic")
    )
    db_session.add(
        StoredObject(
            organization_id=ORG_1,
            provider="s3",
            bucket=location.bucket_name("dev"),
            object_key=location.object_path,
            version_id=stored.version_id,
            status="available",
        )
    )
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    original = key.current_version_id
    registry.rotate(db_session, key, NEXT_KEY, lambda _: bank_storage.provider)
    db_session.commit()

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    reencryption.run(db_session, job, bank_storage.storage)
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    assert len(versions) == 2
    assert any(v.envelope_id == original and v.retained for v in versions)
    assert all(v.envelope_id != original for v in versions if not v.retained)
    _, historical = bank_storage.storage.read(location, stored.version_id)
    assert historical.read() == b"synthetic"
    historical.close()
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.retained == {"stored_object_versions:raw": 1}
    assert progress.objects_rewritten == 1
