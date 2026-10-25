from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import BinaryIO, Protocol, cast
from uuid import UUID, uuid4

import pytest
from alembic.script import ScriptDirectory
from pydantic import TypeAdapter
from sqlalchemy import delete
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.key_management import reencryption, registry, sdk
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyIntegrityError, KeyUnavailableError
from app.db.base import utc_now
from app.identity.public import Bank
from app.models.job import Job
from app.models.risk import StoredObject
from app.services import job_queue
from app.storage import reencryption as objects
from app.storage.client import (
    EncryptionCursor,
    EncryptionPage,
    StorageAccessError,
    StorageBackendError,
    StorageLocation,
    Tier,
)
from app.storage.encryption import ENCRYPTION_FORMAT, object_context
from tests.db.test_postgres_migrations import alembic_config_for_app
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import (
    KEY,
    NEXT_KEY,
    SLUG,
    BankStorage,
    bank_storage,
)
from tests.support.helpers import ORG_1
from tests.support.reencryption import drain

__all__ = ["bank_storage"]

ClientError = cast(
    Callable[[dict[str, object], str], Exception],
    import_module("botocore.exceptions").ClientError,
)


def claim_large_object_job(db_session: Session, bank_storage: BankStorage) -> Job:
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    registry.rotate(db_session, key, NEXT_KEY, lambda _: bank_storage.provider)
    db_session.commit()
    _ = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    while True:
        job = job_queue.claim_next(db_session, utc_now(), (reencryption.JOB_TYPE,))
        assert job is not None
        progress = reencryption.Progress.model_validate(job.progress)
        if progress.phase == "large_object":
            break
        reencryption.run(db_session, job, bank_storage.storage)
        job_queue.complete(db_session, job)
    return job


@pytest.mark.committing_db
@pytest.mark.parametrize("concurrent_write", [False, True])
def test_large_object_copy_runs_alone_and_rechecks_at_atomic_publication(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_write: bool,
) -> None:
    monkeypatch.setattr(reencryption, "LARGE_OBJECT_BYTES", 1024 * 1024)

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    large = StorageLocation(SLUG, "temp", "a-large.csv")
    small = StorageLocation(SLUG, "temp", "z-small.csv")
    content = b"synthetic large content\n" * 60000
    replacement = b"synthetic concurrent writer"
    for location, data in ((large, content), (small, b"synthetic small")):
        bank_storage.storage.write(location, io.BytesIO(data), metadata_for(SLUG, "temp", data))
    job = claim_large_object_job(db_session, bank_storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.large_object is not None and progress.large_object.location == large
    assert progress.objects_rewritten == 0
    assert job.payload["reencryption_bytes"] == progress.large_object.size_bytes
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    upload_part, get_object = client.upload_part, client.get_object
    copies: list[str] = []
    reads: list[str] = []

    def get(**kwargs: object) -> dict[str, object]:
        reads.append(str(kwargs["Key"]))
        return get_object(**kwargs)

    def part(**kwargs: object) -> dict[str, object]:
        assert not db_session.in_transaction()
        assert kwargs["Key"] == large.object_path
        if not copies:
            copies.append(str(kwargs["Key"]))
            assert small.object_path not in reads
            if concurrent_write:
                bank_storage.storage.write(
                    large, io.BytesIO(replacement), metadata_for(SLUG, "temp", replacement)
                )
        return upload_part(**kwargs)

    monkeypatch.setattr(client, "get_object", get)
    monkeypatch.setattr(client, "upload_part", part)
    reencryption.run(db_session, job, bank_storage.storage)
    result = reencryption.Progress.model_validate(job.progress)
    assert copies == [large.object_path]
    assert result.objects_rewritten == (0 if concurrent_write else 1)
    assert result.phase == "objects" and result.next_job_id is not None
    assert small.object_path not in reads
    job_queue.complete(db_session, job)
    monkeypatch.setattr(client, "upload_part", upload_part)
    last = drain(db_session, job, bank_storage.storage)
    final = reencryption.Progress.model_validate(last.progress)
    assert final.summary == "complete"
    assert final.objects_rewritten == (1 if concurrent_write else 2)
    _, stream = bank_storage.storage.read(large)
    try:
        assert stream.read() == (replacement if concurrent_write else content)
    finally:
        stream.close()
    [current] = [
        item for item in bank_storage.storage.encryption_inventory(SLUG) if item.location == large
    ]
    assert current.envelope_id == final.target_version


def test_inventory_metadata_budget_persists_each_exact_version_cursor(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for number in range(3):
        location = StorageLocation(SLUG, "temp", f"{number}.csv")
        bank_storage.s3.put_object(
            Bucket=location.bucket_name("dev"),
            Key=location.object_path,
            Body=b"synthetic",
            Metadata=metadata_for(SLUG, "temp", b"synthetic").to_object_metadata(),
        )

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    list_versions = client.list_object_versions
    calls: list[dict[str, object]] = []
    elapsed = 0.0
    monkeypatch.setattr(reencryption.time, "monotonic", lambda: elapsed)

    def page(**kwargs: object) -> dict[str, object]:
        nonlocal elapsed
        assert kwargs["MaxKeys"] == 1
        calls.append(kwargs)
        elapsed += reencryption.HOP_SECONDS + 1
        return list_versions(**kwargs)

    monkeypatch.setattr(client, "list_object_versions", page)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    object_cursors: list[EncryptionCursor] = []
    while True:
        claimed = job_queue.claim_next(db_session, utc_now(), (reencryption.JOB_TYPE,))
        if claimed is None:
            break
        before = len(calls)
        reencryption.run(db_session, claimed, bank_storage.storage)
        assert len(calls) - before <= 1
        progress = reencryption.Progress.model_validate(claimed.progress)
        if progress.phase == "objects" and progress.object_cursor.key is not None:
            object_cursors.append(progress.object_cursor)
        job_queue.complete(db_session, claimed)
        job = claimed
    assert [cursor.key for cursor in object_cursors] == ["0.csv", "1.csv"]
    assert all(cursor.version == "null" for cursor in object_cursors)
    assert any(call.get("VersionIdMarker") == "null" for call in calls)
    assert reencryption.Progress.model_validate(job.progress).objects_rewritten == 3


def read_ciphertext(bank: BankStorage, location: StorageLocation, version_id: str) -> bytes:
    response = bank.s3.get_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path, VersionId=version_id
    )
    source = cast(BinaryIO, response["Body"])
    try:
        return source.read()
    finally:
        source.close()


def minio_metadata(
    bank: BankStorage, monkeypatch: pytest.MonkeyPatch, *, head_denied: bool
) -> list[io.BytesIO]:
    client = cast(objects.S3MaintenanceClient, bank.s3)
    head_object, get_object = bank.s3.head_object, client.get_object
    bodies: list[io.BytesIO] = []

    def title_case(response: dict[str, object]) -> dict[str, object]:
        metadata = TypeAdapter(dict[str, str]).validate_python(response.get("Metadata", {}))
        response["Metadata"] = {key.title(): value for key, value in metadata.items()}
        return response

    def head(**kwargs: object) -> dict[str, object]:
        if head_denied:
            raise ClientError({"Error": {"Code": "403"}}, "HeadObject")
        return title_case(head_object(**kwargs))

    def get(**kwargs: object) -> dict[str, object]:
        response = get_object(**kwargs)
        source = cast(BinaryIO, response["Body"])
        try:
            body = io.BytesIO(source.read())
        finally:
            source.close()
        response["Body"] = body
        bodies.append(body)
        return title_case(response)

    monkeypatch.setattr(bank.s3, "head_object", head)
    monkeypatch.setattr(client, "get_object", get)
    return bodies


@pytest.mark.parametrize("head_denied", [False, True])
def test_title_cased_metadata_migrates_editable_ciphertext(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    head_denied: bool,
) -> None:
    location = StorageLocation(SLUG, "temp", "synthetic/editable.csv")
    content = b"synthetic encrypted editable content"
    bank_storage.storage.write(location, io.BytesIO(content), metadata_for(SLUG, "temp", content))
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    original_envelope = key.current_version_id

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.rotate(db_session, key, NEXT_KEY, providers)
    db_session.commit()
    bodies = minio_metadata(bank_storage, monkeypatch, head_denied=head_denied)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete" and progress.objects_rewritten == 1
    [current] = list(bank_storage.storage.encryption_inventory(SLUG))
    assert current.envelope_id == progress.target_version
    assert current.envelope_id != original_envelope and not current.retained
    descriptor, plaintext = bank_storage.storage.read(location)
    try:
        assert plaintext.read() == content
    finally:
        plaintext.close()
    assert (
        descriptor.metadata.checksum_sha256 == metadata_for(SLUG, "temp", content).checksum_sha256
    )
    assert bodies and all(body.closed for body in bodies)


@pytest.mark.parametrize("head_denied", [False, True])
def test_title_cased_deleted_history_keeps_its_envelope_and_retirement_hold(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    head_denied: bool,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
    get_key_settings.cache_clear()
    location = StorageLocation(SLUG, "raw", "synthetic/deleted.csv")
    content = b"synthetic protected deleted history"
    stored = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "raw", content)
    )
    assert stored.version_id is not None
    ciphertext = read_ciphertext(bank_storage, location, stored.version_id)
    bank_storage.storage.delete(location)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    original_envelope = key.current_version_id
    assert original_envelope is not None
    rotated_at = datetime.now(UTC)

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    registry.rotate(db_session, key, NEXT_KEY, providers)
    db_session.commit()
    bodies = minio_metadata(bank_storage, monkeypatch, head_denied=head_denied)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete except 1 retained immutable records"
    assert progress.retained == {"stored_object_versions:raw": 1}
    assert progress.objects_rewritten == 0
    assert db_session.get(ObjectKeyEnvelope, original_envelope) is not None
    [retained] = list(bank_storage.storage.encryption_inventory(SLUG))
    assert retained.retained and retained.envelope_id == original_envelope
    assert retained.version_id == stored.version_id
    assert not bank_storage.storage.exists(location)
    descriptor, historical = bank_storage.storage.read(location, version_id=stored.version_id)
    try:
        assert historical.read() == content
    finally:
        historical.close()
    assert descriptor.version_id == stored.version_id
    assert read_ciphertext(bank_storage, location, stored.version_id) == ciphertext
    assert bodies and all(body.closed for body in bodies)
    monkeypatch.setattr(registry, "utc_now", lambda: rotated_at + timedelta(days=3))
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    index = db_session.get(ObjectKeyEnvelope, key.index_version_id)
    assert index is not None and registry.reference(index) == registry.reference(key)
    with pytest.raises(KeyUnavailableError, match="retained encryption versions"):
        registry.authorize_retirement(db_session, key, KEY)


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


def assert_reencrypted_versions(
    bank_storage: BankStorage,
    location: StorageLocation,
    content: bytes,
    version_id: str,
) -> None:
    descriptor, body = bank_storage.storage.read(location)
    try:
        assert body.read() == content
    finally:
        body.close()
    assert (
        descriptor.metadata.checksum_sha256
        == metadata_for(SLUG, location.tier, content).checksum_sha256
    )
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    current = next(version for version in versions if not version.retained)
    assert current.envelope_id is not None
    assert len(versions) == (2 if location.tier == "raw" else 1)
    if location.tier == "raw":
        assert any(version.version_id == version_id and version.retained for version in versions)


@pytest.mark.parametrize("tier", ["raw", "temp"])
@pytest.mark.parametrize("multipart", [False, True])
def test_reencryption_uses_head_denied_fallback_and_closes_metadata_streams(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    tier: Tier,
    multipart: bool,
) -> None:
    location = StorageLocation(SLUG, tier, "synthetic/customers.csv")
    content = b"x" * (objects.MULTIPART_SIZE + 1) if multipart else b"synthetic customer data"
    stored = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=content,
        Metadata=metadata_for(SLUG, tier, content).to_object_metadata(),
    )
    version_id = str(stored.get("VersionId", "null"))
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    get_object = client.get_object

    class TrackedBody(io.BytesIO):
        bytes_read: int = 0

        def read(self, size: int | None = -1) -> bytes:
            value = super().read(size)
            self.bytes_read += len(value)
            return value

    responses: list[tuple[str | None, TrackedBody]] = []
    denied: list[str | None] = []

    def denied_head(**kwargs: object) -> dict[str, object]:
        assert str(kwargs["Key"]).endswith(".csv")
        denied.append(cast(str | None, kwargs.get("VersionId")))
        raise ClientError({"Error": {"Code": "403"}}, "HeadObject")

    def tracked_get(**kwargs: object) -> dict[str, object]:
        response = get_object(**kwargs)
        source = cast(BinaryIO, response["Body"])
        try:
            body = TrackedBody(source.read())
        finally:
            source.close()
        response["Body"] = body
        responses.append((cast(str | None, kwargs.get("VersionId")), body))
        return response

    monkeypatch.setattr(bank_storage.s3, "head_object", denied_head)
    monkeypatch.setattr(client, "get_object", tracked_get)
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    assert item.version_id == version_id and item.envelope_id is None and not item.retained
    assert denied == [version_id]
    assert len(responses) == 1
    assert responses[0][0] == version_id
    assert responses[0][1].closed and responses[0][1].bytes_read == 0
    responses.clear()
    denied.clear()
    assert bank_storage.storage.reencrypt(item)
    assert denied == [None]
    assert responses[0][0] is None
    assert responses[0][1].closed and responses[0][1].bytes_read == 0
    assert responses[1][0] == version_id
    assert all(body.closed for _, body in responses)
    assert_reencrypted_versions(bank_storage, location, content, version_id)
    assert all(body.closed for _, body in responses)


@pytest.mark.parametrize("hold", ["legal_hold", "retention"])
def test_head_denied_fallback_preserves_new_object_holds(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    hold: str,
) -> None:
    location = StorageLocation(SLUG, "raw", "synthetic/held.csv")
    stored = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=b"synthetic held data",
        Metadata=metadata_for(SLUG, "raw", b"synthetic held data").to_object_metadata(),
    )
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    get_object = client.get_object
    bodies: list[BinaryIO] = []

    def denied_head(**_kwargs: object) -> dict[str, object]:
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")

    def held_get(**kwargs: object) -> dict[str, object]:
        response = get_object(**kwargs)
        source = cast(BinaryIO, response["Body"])
        try:
            body = io.BytesIO(source.read())
        finally:
            source.close()
        response["Body"] = body
        if hold == "legal_hold":
            response["ObjectLockLegalHoldStatus"] = "ON"
        else:
            response["ObjectLockMode"] = "COMPLIANCE"
            response["ObjectLockRetainUntilDate"] = datetime.now(UTC) + timedelta(days=1)
        bodies.append(body)
        return response

    monkeypatch.setattr(bank_storage.s3, "head_object", denied_head)
    monkeypatch.setattr(client, "get_object", held_get)
    assert not bank_storage.storage.reencrypt(item)
    [retained] = list(bank_storage.storage.encryption_inventory(SLUG))
    assert retained.retained and retained.version_id == stored["VersionId"]
    assert retained.envelope_id is None
    assert len(bodies) == 2 and all(body.closed for body in bodies)


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
        job = drain(db_session, job, bank_storage.storage)
    db_session.rollback()
    monkeypatch.setattr(objects, "reencrypt", real_reencrypt)
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete except 1 retained immutable records"
    assert progress.retained == {"stored_object_versions:canonical": 1}
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    assert len(versions) == 2 and any(v.envelope_id == original and v.retained for v in versions)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    with pytest.raises(KeyUnavailableError, match="retention"):
        registry.authorize_retirement(db_session, key, KEY)


@pytest.mark.parametrize("multipart", [False, True])
def test_conditional_publication_preserves_concurrent_new_content(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
    multipart: bool,
) -> None:
    location = StorageLocation(SLUG, "temp", "synthetic/race.csv")
    original = b"old" * (objects.MULTIPART_SIZE // 3 + 1) if multipart else b"old"
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=original,
        Metadata=metadata_for(SLUG, "temp", original).to_object_metadata(),
    )
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    aborted: list[object] = []
    original_etag = bank_storage.s3.head_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path
    )["ETag"]

    put_object = client.put_object
    abort_multipart_upload = client.abort_multipart_upload

    def put_with_concurrent_write(**kwargs: object) -> dict[str, object]:
        with monkeypatch.context() as writing:
            writing.setattr(client, "put_object", put_object)
            bank_storage.storage.write(
                location, io.BytesIO(b"new"), metadata_for(SLUG, "temp", b"new")
            )
        return put_object(**kwargs)

    def complete_with_concurrent_write(**kwargs: object) -> dict[str, object]:
        assert kwargs["IfMatch"] == original_etag
        bank_storage.storage.write(location, io.BytesIO(b"new"), metadata_for(SLUG, "temp", b"new"))
        current = bank_storage.s3.head_object(Bucket=kwargs["Bucket"], Key=kwargs["Key"])
        assert current["ETag"] != kwargs["IfMatch"]
        raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "CompleteMultipartUpload")

    def record_abort(**kwargs: object) -> dict[str, object]:
        aborted.append(kwargs["UploadId"])
        return abort_multipart_upload(**kwargs)

    if multipart:
        monkeypatch.setattr(client, "complete_multipart_upload", complete_with_concurrent_write)
    else:
        monkeypatch.setattr(client, "put_object", put_with_concurrent_write)
    monkeypatch.setattr(client, "abort_multipart_upload", record_abort)
    assert not bank_storage.storage.reencrypt(item)
    _, body = bank_storage.storage.read(location)
    assert body.read() == b"new"
    body.close()
    assert len(aborted) == int(multipart)


def test_large_object_reencryption_publishes_bounded_parts(
    bank_storage: BankStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = StorageLocation(SLUG, "temp", "synthetic/large.csv")
    content = b"x" * (objects.MULTIPART_SIZE + 1024)
    _ = bank_storage.s3.put_object(
        Bucket=location.bucket_name("dev"),
        Key=location.object_path,
        Body=content,
        Metadata=metadata_for(SLUG, "temp", content).to_object_metadata(),
    )
    [item] = list(bank_storage.storage.encryption_inventory(SLUG))
    client = cast(objects.S3MaintenanceClient, bank_storage.s3)
    parts: list[int] = []

    upload_part = client.upload_part
    complete_multipart_upload = client.complete_multipart_upload

    def refuse_put(**_kwargs: object) -> dict[str, object]:
        pytest.fail("Large objects must use bounded multipart publication")

    def bounded_part(**kwargs: object) -> dict[str, object]:
        body = kwargs["Body"]
        assert isinstance(body, bytes) and len(body) <= objects.MULTIPART_SIZE
        parts.append(len(body))
        return upload_part(**kwargs)

    def conditional_complete(**kwargs: object) -> dict[str, object]:
        assert kwargs["IfMatch"]
        return complete_multipart_upload(**kwargs)

    monkeypatch.setattr(client, "put_object", refuse_put)
    monkeypatch.setattr(client, "upload_part", bounded_part)
    monkeypatch.setattr(client, "complete_multipart_upload", conditional_complete)
    assert bank_storage.storage.reencrypt(item)
    assert len(parts) == 2
    _, body = bank_storage.storage.read(location)
    assert body.read() == content
    body.close()


@pytest.mark.parametrize("rotate_during_pass", [False, True])
def test_object_job_uses_bounded_inventory_pages(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    rotate_during_pass: bool,
) -> None:
    for index in range(12):
        location = StorageLocation(SLUG, "raw", f"synthetic/{index}.csv")
        _ = bank_storage.s3.put_object(
            Bucket=location.bucket_name("dev"),
            Key=location.object_path,
            Body=b"synthetic",
            Metadata=metadata_for(SLUG, "raw", b"synthetic").to_object_metadata(),
        )

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    real_inventory_page = objects.inventory_page
    real_reencrypt = objects.reencrypt
    pages: list[int] = []
    rotated = False

    def observe_reencryption[**P](original: Callable[P, bool]) -> Callable[P, bool]:
        def migrate_object(*args: P.args, **kwargs: P.kwargs) -> bool:
            nonlocal rotated
            result = original(*args, **kwargs)
            if result and rotate_during_pass and not rotated:
                rotated = True
                key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
                registry.rotate(db_session, key, NEXT_KEY, lambda _: bank_storage.provider)
            return result

        return migrate_object

    def observe_inventory[**P](
        original: Callable[P, EncryptionPage],
    ) -> Callable[P, EncryptionPage]:
        def inventory_page(*args: P.args, **kwargs: P.kwargs) -> EncryptionPage:
            limit = cast(int, args[-1])
            page = original(*args, **kwargs)
            assert len(page.items) <= limit == 1
            pages.append(len(page.items))
            return page

        return inventory_page

    monkeypatch.setattr(objects, "inventory_page", observe_inventory(real_inventory_page))
    monkeypatch.setattr(objects, "reencrypt", observe_reencryption(real_reencrypt))
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.objects_rewritten >= 12
    assert progress.retained == {"stored_object_versions:raw": progress.objects_rewritten}
    assert 0 < len(pages) <= 100
    assert sum(pages) <= 100
    monkeypatch.setattr(objects, "inventory_page", real_inventory_page)
    assert all(
        item.envelope_id == progress.target_version
        for item in bank_storage.storage.encryption_inventory(SLUG)
        if not item.retained
    )


class VersionMigration(Protocol):
    def backfill_references(self, connection: Connection) -> None: ...
    def backfill_versions(self, connection: Connection) -> None: ...


def prepare_legacy_bank_upgrade(
    bank_storage: BankStorage, db_session: Session
) -> tuple[StorageLocation, bytes, dict[str, str], UUID]:
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    key.current_version_id = key.index_version_id = None
    db_session.flush()
    _ = db_session.execute(delete(ObjectKeyEnvelope))
    location = StorageLocation(SLUG, "raw", "synthetic/old-envelope.csv")
    content = b"synthetic legacy encrypted object"
    metadata = metadata_for(SLUG, "raw", content)
    context = object_context(location, metadata.checksum_sha256)
    digest = registry.context_digest(context)
    secret = bank_storage.provider.generate_data_key(
        KEY, registry.wrapping_context(key.bank_id, digest)
    )
    envelope_id = uuid4()
    db_session.add(
        ObjectKeyEnvelope(
            id=envelope_id,
            bank_id=key.bank_id,
            organization_id=ORG_1,
            context_digest=digest,
            wrapped_key=secret.wrapped,
            provider="",
            key_id="",
            region="",
            owner_account="",
        )
    )
    db_session.add(
        Bank(
            id="BK-EMPTY001",
            organization_id=ORG_1,
            name="Synthetic empty bank",
            short_name="Empty",
            jurisdiction_code="GH",
            currency="GHS",
            institution_type="universal_bank",
            license_type="universal_bank",
            storage_slug="synthetic-empty",
        )
    )
    db_session.flush()
    db_session.add(
        BankEncryptionKey(
            bank_id="BK-EMPTY001",
            organization_id=ORG_1,
            storage_slug="synthetic-empty",
            provider=KEY.provider,
            key_id=KEY.key_id,
            region=KEY.region,
            owner_account=KEY.owner_account,
            status="active",
        )
    )
    db_session.commit()
    encrypted = sdk.transform_file(io.BytesIO(content), secret.plaintext, context)
    try:
        _ = bank_storage.s3.put_object(
            Bucket=location.bucket_name("dev"),
            Key=location.object_path,
            Body=encrypted,
            Metadata={
                **metadata.to_object_metadata(),
                "encryption-format": ENCRYPTION_FORMAT,
                "key-envelope-id": str(envelope_id),
                "kms-key-id": KEY.key_id,
            },
        )
    finally:
        encrypted.close()
    return location, content, context, envelope_id


def test_existing_bank_upgrade_preserves_legacy_objects_and_initializes_empty_banks(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location, content, context, envelope_id = prepare_legacy_bank_upgrade(bank_storage, db_session)

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    revision = ScriptDirectory.from_config(alembic_config_for_app()).get_revision("202610220102")
    assert revision is not None
    migration = cast(VersionMigration, cast(object, revision.module))
    migration.backfill_references(db_session.connection())
    migration.backfill_versions(db_session.connection())
    db_session.commit()
    db_session.expunge_all()
    for bank in ("BK-SAMP0001", "BK-EMPTY001"):
        config = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=bank)
        assert config.current_version_id is not None
        assert config.current_version_id == config.index_version_id
        assert registry.prepare_envelope(
            db_session, config, {"purpose": "synthetic"}, lambda _: bank_storage.provider
        ).plaintext
    envelope = db_session.get(ObjectKeyEnvelope, envelope_id)
    assert envelope is not None and registry.reference(envelope) == KEY
    _, body = bank_storage.storage.read(location)
    assert body.read() == content
    body.close()
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    with pytest.raises(KeyIntegrityError):
        registry.open_envelope(
            db_session,
            key,
            envelope_id,
            {**context, "object_path": "other"},
            lambda _: bank_storage.provider,
        )
    registry.rotate(db_session, key, NEXT_KEY, lambda _: bank_storage.provider)
    db_session.commit()
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.objects_rewritten == 1
    assert progress.retained == {"stored_object_versions:raw": 1}
    _, body = bank_storage.storage.read(location)
    assert body.read() == content
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
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.retained == {"stored_object_versions:temp": 1}
    assert progress.objects_rewritten == 1
    inventory = {
        item.location.object_path: item for item in bank_storage.storage.encryption_inventory(SLUG)
    }
    assert inventory["pinned.csv"].envelope_id is None
    assert inventory["editable.csv"].envelope_id is not None


def assert_versioning_content(
    bank_storage: BankStorage,
    location: StorageLocation,
    original_content: bytes,
    original_ciphertext: bytes,
    latest_content: bytes,
) -> None:
    descriptor, historical = bank_storage.storage.read(location, version_id="null")
    try:
        assert historical.read() == original_content
    finally:
        historical.close()
    assert descriptor.version_id == "null"
    assert read_ciphertext(bank_storage, location, "null") == original_ciphertext
    _, current_body = bank_storage.storage.read(location)
    try:
        assert current_body.read() == latest_content
    finally:
        current_body.close()


@pytest.mark.parametrize("head_denied", [False, True])
def test_versioning_transition_keeps_null_version_envelope_and_blocks_retirement(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    head_denied: bool,
) -> None:
    monkeypatch.setenv("ENCRYPTION_BACKUP_RETENTION_DAYS", "1")
    get_key_settings.cache_clear()

    def providers(_name: str) -> LocalKeyProvider:
        return bank_storage.provider

    monkeypatch.setattr(registry, "provider_for", providers)
    location = StorageLocation(SLUG, "temp", "synthetic/versioning-transition.csv")
    original_content = b"synthetic protected unversioned predecessor"
    bank_storage.storage.write(
        location,
        io.BytesIO(original_content),
        metadata_for(SLUG, "temp", original_content),
    )
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    original_envelope = key.current_version_id
    assert original_envelope is not None
    original_ciphertext = read_ciphertext(bank_storage, location, "null")
    bank_storage.s3.put_bucket_versioning(
        Bucket=location.bucket_name("dev"), VersioningConfiguration={"Status": "Enabled"}
    )
    rotated_at = datetime.now(UTC)
    registry.rotate(db_session, key, NEXT_KEY, providers)
    db_session.commit()
    latest_content = b"synthetic protected current version"
    latest = bank_storage.storage.write(
        location, io.BytesIO(latest_content), metadata_for(SLUG, "temp", latest_content)
    )
    assert latest.version_id is not None and latest.version_id != "null"
    denied_versions: list[str | None] = []

    def denied_head(**kwargs: object) -> dict[str, object]:
        denied_versions.append(cast(str | None, kwargs.get("VersionId")))
        raise ClientError({"Error": {"Code": "403"}}, "HeadObject")

    if head_denied:
        monkeypatch.setattr(bank_storage.s3, "head_object", denied_head)
    job = reencryption.enqueue(db_session, ORG_1, "BK-SAMP0001")
    db_session.commit()
    job = drain(db_session, job, bank_storage.storage)
    progress = reencryption.Progress.model_validate(job.progress)
    assert progress.summary == "complete except 1 retained immutable records"
    assert progress.retained == {"stored_object_versions:temp": 1}
    assert progress.objects_rewritten == 0
    assert db_session.get(ObjectKeyEnvelope, original_envelope) is not None
    versions = list(bank_storage.storage.encryption_inventory(SLUG))
    assert len(versions) == 2
    predecessor = next(item for item in versions if item.version_id == "null")
    assert predecessor.retained and predecessor.envelope_id == original_envelope
    current = next(item for item in versions if not item.retained)
    assert current.version_id == latest.version_id
    assert current.envelope_id == progress.target_version
    assert_versioning_content(
        bank_storage, location, original_content, original_ciphertext, latest_content
    )
    if head_denied:
        assert "null" in denied_versions and latest.version_id in denied_versions
    monkeypatch.setattr(registry, "utc_now", lambda: rotated_at + timedelta(days=3))
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id="BK-SAMP0001")
    index = db_session.get(ObjectKeyEnvelope, key.index_version_id)
    assert index is not None and registry.reference(index) == registry.reference(key)
    with pytest.raises(KeyUnavailableError, match="retained encryption versions"):
        registry.authorize_retirement(db_session, key, KEY)


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
    job = drain(db_session, job, bank_storage.storage)
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
