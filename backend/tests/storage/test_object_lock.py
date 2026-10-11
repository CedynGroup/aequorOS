from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from moto import mock_aws
from pydantic import TypeAdapter

from app.core.key_management.types import KeyStatus
from app.storage.backups import BackupClient, publish_backup
from app.storage.client import (
    StorageAccessError,
    StorageBackendError,
    StorageLocation,
    StorageValidationError,
)
from app.storage.config import get_storage_settings
from app.storage.provisioning import ProvisioningClient, provision_institution
from app.storage.retention import RetentionClient, retain_version
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import (
    KEY,
    SLUG,
    BankStorage,
)
from tests.storage.test_bank_encryption import (
    bank_storage as bank_storage,  # noqa: PLC0414 - register shared pytest fixture
)


class LockClient(RetentionClient, Protocol):
    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, object]: ...
    def put_object_lock_configuration(
        self, *, Bucket: str, ObjectLockConfiguration: dict[str, str]
    ) -> object: ...

    def delete_object(self, *, Bucket: str, Key: str, VersionId: str) -> object: ...

    def head_object(self, *, Bucket: str, Key: str, VersionId: str) -> dict[str, object]: ...


def _moto_retention(client: LockClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Moto implements retention writes/deletion guards and HEAD retention
    # headers, but not GetObjectRetention. Adapt that read action only.
    def read_retention(*, Bucket: str, Key: str, VersionId: str) -> dict[str, object]:
        result = client.head_object(Bucket=Bucket, Key=Key, VersionId=VersionId)
        return {
            "Retention": {
                "Mode": result.get("ObjectLockMode"),
                "RetainUntilDate": result.get("ObjectLockRetainUntilDate"),
            }
        }

    monkeypatch.setattr(client, "get_object_retention", read_retention)


def _enable(bank: BankStorage, monkeypatch: pytest.MonkeyPatch) -> LockClient:
    settings = bank.storage._settings.model_copy(update={"object_lock_enabled": True})  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(bank.storage, "_settings", settings)
    client = cast(LockClient, bank.s3)
    _moto_retention(client, monkeypatch)
    _ = client.put_object_lock_configuration(
        Bucket=StorageLocation(SLUG, "outputs", "").bucket_name("dev"),
        ObjectLockConfiguration={"ObjectLockEnabled": "Enabled"},
    )
    return client


def test_retained_filing_stays_encrypted_and_revocation_refuses_read(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enable(bank_storage, monkeypatch)
    location = StorageLocation(SLUG, "outputs", "bog_returns/synthetic/report.pdf")
    content = b"synthetic filed return"
    stored = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    assert stored.version_id is not None
    retention = client.get_object_retention(
        Bucket=location.bucket_name("dev"), Key=location.object_path, VersionId=stored.version_id
    )
    assert isinstance(retention["Retention"], dict)
    assert retention["Retention"]["Mode"] == "COMPLIANCE"
    repeated = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    assert repeated.version_id == stored.version_id
    with pytest.raises(ClientError):
        client.delete_object(
            Bucket=location.bucket_name("dev"),
            Key=location.object_path,
            VersionId=stored.version_id,
        )
    bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)
    with pytest.raises(StorageAccessError):
        bank_storage.storage.read(location, stored.version_id)


def test_output_lock_provisioning_preserves_unversioned_temp_cleanup(
    bank_storage: BankStorage,
) -> None:
    settings = bank_storage.storage._settings.model_copy(update={"object_lock_enabled": True})  # pyright: ignore[reportPrivateUsage]
    slug = "synthetic-retention-only"
    provision_institution(cast(ProvisioningClient, bank_storage.s3), settings, slug)
    client = cast(LockClient, bank_storage.s3)
    output = StorageLocation(slug, "outputs", "").bucket_name("dev")
    config = TypeAdapter(dict[str, object]).validate_python(
        client.get_object_lock_configuration(Bucket=output)["ObjectLockConfiguration"]
    )
    assert config["ObjectLockEnabled"] == "Enabled"
    temp = StorageLocation(slug, "temp", "").bucket_name("dev")
    assert client.get_bucket_versioning(Bucket=temp).get("Status") is None


def test_retention_never_shortens_existing_compliance_lock(
    bank_storage: BankStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enable(bank_storage, monkeypatch)
    location = StorageLocation(SLUG, "outputs", "bog_returns/synthetic/second.pdf")
    content = b"synthetic version"
    stored = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    assert stored.version_id is not None
    bucket = location.bucket_name("dev")
    original = client.get_object_retention(
        Bucket=bucket, Key=location.object_path, VersionId=stored.version_id
    )
    retain_version(
        client,
        bucket,
        location.object_path,
        stored.version_id,
        datetime.now(UTC) + timedelta(days=1),
    )
    assert (
        client.get_object_retention(
            Bucket=bucket, Key=location.object_path, VersionId=stored.version_id
        )
        == original
    )
    with pytest.raises(StorageValidationError, match="concrete"):
        retain_version(client, bucket, location.object_path, None, datetime.now(UTC))


def test_backup_pair_is_published_with_compliance_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "STORAGE_OBJECT_LOCK_ENABLED": "true",
        "BACKUP_WORM_BUCKET": "synthetic-backups",
        "STORAGE_KMS_KEY_ID": "alias/synthetic-key",
    }.items():
        monkeypatch.setenv(name, value)
    get_storage_settings.cache_clear()
    archive, manifest = tmp_path / "fixture.dump", tmp_path / "fixture.manifest.json"
    archive.write_bytes(b"synthetic dump")
    manifest.write_text("{}")
    with mock_aws():
        factory = cast(Callable[..., BaseClient], boto3.client)
        s3 = factory("s3", region_name="us-east-1")
        _ = cast(ProvisioningClient, cast(object, s3)).create_bucket(
            Bucket="synthetic-backups", ObjectLockEnabledForBucket=True
        )
        client = cast(BackupClient, cast(object, s3))
        _moto_retention(cast(LockClient, cast(object, s3)), monkeypatch)

        def backup_factory(*_args: object, **_kwargs: object) -> BackupClient:
            return client

        monkeypatch.setattr("app.storage.backups.boto3.client", backup_factory)
        versions = publish_backup(archive, manifest)
        assert len(versions) == 2
        for key, version in versions.items():
            retention = client.get_object_retention(
                Bucket="synthetic-backups", Key=key, VersionId=version
            )
            assert isinstance(retention["Retention"], dict)
            assert retention["Retention"]["Mode"] == "COMPLIANCE"
    get_storage_settings.cache_clear()


def test_local_backup_does_not_require_object_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORAGE_OBJECT_LOCK_ENABLED", "false")
    get_storage_settings.cache_clear()
    assert publish_backup(tmp_path / "absent.dump", tmp_path / "absent.json") == {}
    get_storage_settings.cache_clear()


def test_retained_presigned_uploads_cannot_bypass_application_storage(
    bank_storage: BankStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(bank_storage, monkeypatch)

    # A bank without connected keys still cannot bypass retention checks.
    def bank_key_optional(_location: StorageLocation) -> bool:
        return False

    monkeypatch.setattr(bank_storage.storage._encryption, "bank_key_required", bank_key_optional)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(StorageAccessError, match="Retained uploads"):
        bank_storage.storage.presigned_url(
            StorageLocation(SLUG, "outputs", "synthetic.pdf"), "write"
        )


def test_retention_vendor_errors_are_safe(
    bank_storage: BankStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _enable(bank_storage, monkeypatch)

    def fail(**_: object) -> object:
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private vendor detail"}},
            "GetObjectRetention",
        )

    monkeypatch.setattr(client, "get_object_retention", fail)
    with pytest.raises(
        StorageBackendError, match="^Compliance retention could not be verified or applied\\.$"
    ):
        retain_version(client, "synthetic", "synthetic.pdf", "version", datetime.now(UTC))


def test_existing_unretained_version_is_locked_on_idempotent_write(
    bank_storage: BankStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = StorageLocation(SLUG, "outputs", "pre-rollout.pdf")
    content = b"synthetic historical filing"
    client = _enable(bank_storage, monkeypatch)
    # Moto cannot enable Object Lock on a nonempty bucket. Provision the
    # capability first, then model a writer before retention was switched on.
    settings = bank_storage.storage._settings  # pyright: ignore[reportPrivateUsage]
    with monkeypatch.context() as before_rollout:
        before_rollout.setattr(
            bank_storage.storage,
            "_settings",
            settings.model_copy(update={"object_lock_enabled": False}),
        )
        stored = bank_storage.storage.write(
            location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
        )
    read = client.get_object_retention

    def absent(**_: object) -> dict[str, object]:
        raise ClientError(
            {"Error": {"Code": "NoSuchObjectLockConfiguration"}}, "GetObjectRetention"
        )

    monkeypatch.setattr(client, "get_object_retention", absent)
    repeated = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    assert repeated.version_id == stored.version_id
    assert stored.version_id is not None
    retention = read(
        Bucket=location.bucket_name("dev"), Key=location.object_path, VersionId=stored.version_id
    )
    assert isinstance(retention["Retention"], dict)
    assert retention["Retention"]["Mode"] == "COMPLIANCE"
