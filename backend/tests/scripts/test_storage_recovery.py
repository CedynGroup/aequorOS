from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import hashlib
from pathlib import Path
from typing import BinaryIO

import pytest
from botocore.exceptions import ClientError

from app.storage.config import StorageEngineSettings
from app.storage.provisioning import TEMP_EXPIRY_DAYS, ensure_audit_bucket, provision_institution
from scripts import backup_storage
from scripts.backup_storage import (
    BucketRecord,
    ObjectRecord,
    StorageBackupReport,
    restore_storage_backup,
)
from scripts.dr_common import DisasterRecoveryError


class RecoveryClient:
    def __init__(self, location_constraint: str | None = None) -> None:
        self.location_constraint = location_constraint
        self.configuration: dict[str, dict[str, object]] = {}
        self.at_upload: dict[str, dict[str, object]] = {}
        self.buckets: list[str] = []
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}

    def head_bucket(self, *, Bucket: str) -> object:
        if Bucket not in self.buckets:
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")
        return {}

    def create_bucket(self, **kwargs: object) -> object:
        configuration = kwargs.get("CreateBucketConfiguration")
        expected = (
            {"LocationConstraint": self.location_constraint} if self.location_constraint else None
        )
        assert configuration == expected
        bucket = str(kwargs["Bucket"])
        self.buckets.append(bucket)
        self.configuration[bucket] = {}
        return {}

    def put_bucket_versioning(self, **kwargs: object) -> object:
        self.configuration[str(kwargs["Bucket"])]["versioning"] = kwargs["VersioningConfiguration"]
        return {}

    def put_bucket_lifecycle_configuration(self, **kwargs: object) -> object:
        self.configuration[str(kwargs["Bucket"])]["lifecycle"] = kwargs["LifecycleConfiguration"]
        return {}

    def put_bucket_encryption(self, **kwargs: object) -> object:
        self.configuration[str(kwargs["Bucket"])]["encryption"] = kwargs[
            "ServerSideEncryptionConfiguration"
        ]
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        raise NotImplementedError

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: BinaryIO,
        Metadata: dict[str, str],
        ContentType: str,
    ) -> object:
        self.at_upload[Bucket] = dict(self.configuration[Bucket])
        self.objects[Bucket, Key] = Body.read(), Metadata, ContentType
        return {}


def _manifest(root: Path, key: str = "object", bucket: str = "synthetic-bucket") -> Path:
    source = root / bucket / "object"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic ciphertext")
    entry = ObjectRecord(
        key,
        20,
        "etag",
        "synthetic",
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        metadata={"key-envelope-id": "synthetic-envelope", "encryption-format": "synthetic"},
        content_type="application/octet-stream",
    )
    path = root / "manifest.json"
    StorageBackupReport(
        "synthetic",
        "synthetic",
        True,
        [
            BucketRecord(bucket, objects=1, bytes=20, keys=[entry]),
        ],
        format_version=2,
    ).write(path)
    return path


def test_restore_recreates_missing_bucket_and_reapplies_headers(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    client = RecoveryClient()
    assert restore_storage_backup(client, manifest, out_dir=tmp_path) == 1
    assert client.buckets == ["synthetic-bucket"]
    data, headers, content_type = client.objects["synthetic-bucket", "object"]
    assert data == b"synthetic ciphertext"
    assert headers["key-envelope-id"] == "synthetic-envelope"
    assert content_type == "application/octet-stream"


@pytest.mark.parametrize("failure", ["checksum", "traversal"])
def test_restore_refuses_corrupt_or_escaping_objects_before_upload(
    tmp_path: Path,
    failure: str,
) -> None:
    manifest = _manifest(tmp_path, "../object" if failure == "traversal" else "object")
    if failure == "checksum":
        (tmp_path / "synthetic-bucket" / "object").write_bytes(b"corrupt")
    client = RecoveryClient()
    with pytest.raises(DisasterRecoveryError):
        restore_storage_backup(client, manifest, out_dir=tmp_path)
    assert client.objects == {}


@pytest.mark.parametrize("endpoint", [None, "https://minio.example.test"])
@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1"])
@pytest.mark.parametrize("tier", ["raw", "canonical", "outputs", "temp", "audit-logs"])
def test_restore_applies_authoritative_bucket_configuration_before_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str | None,
    region: str,
    tier: str,
) -> None:
    settings = StorageEngineSettings.model_validate(
        {
            "STORAGE_BACKEND": "s3",
            "STORAGE_ENV": "dev",
            "S3_ENDPOINT": endpoint,
            "S3_REGION": region,
            "STORAGE_KMS_KEY_ID": "synthetic-sse-key",
        }
    )
    monkeypatch.setattr(backup_storage, "get_storage_settings", lambda: settings)
    bucket = f"aequoros-dev-synthetic-{tier}"
    manifest = _manifest(tmp_path, bucket=bucket)
    client = RecoveryClient(region if endpoint is not None or region != "us-east-1" else None)
    assert restore_storage_backup(client, manifest, out_dir=tmp_path) == 1
    configured = client.at_upload[bucket]
    assert configured["encryption"] == {
        "Rules": [
            {
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "aws:kms",
                    "KMSMasterKeyID": "synthetic-sse-key",
                }
            }
        ]
    }
    if tier == "temp":
        assert "versioning" not in configured
        assert configured["lifecycle"] == {
            "Rules": [
                {
                    "ID": f"temp-expiry-{TEMP_EXPIRY_DAYS}d",
                    "Status": "Enabled",
                    "Filter": {},
                    "Expiration": {"Days": TEMP_EXPIRY_DAYS},
                }
            ]
        }
    else:
        assert configured["versioning"] == {"Status": "Enabled"}
        assert "lifecycle" not in configured


def test_provisioning_and_restore_share_configuration_for_existing_buckets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = StorageEngineSettings.model_validate(
        {
            "STORAGE_BACKEND": "s3",
            "STORAGE_ENV": "dev",
            "S3_ENDPOINT": None,
            "STORAGE_KMS_KEY_ID": "synthetic-sse-key",
        }
    )
    monkeypatch.setattr(backup_storage, "get_storage_settings", lambda: settings)
    client = RecoveryClient()
    result = provision_institution(client, settings, "synthetic")
    audit = "aequoros-dev-audit-logs"
    ensure_audit_bucket(client, settings, audit)
    assert len(result.created_buckets) == 4
    before = {bucket: dict(config) for bucket, config in client.configuration.items()}
    manifest = _manifest(tmp_path, bucket=result.created_buckets[0])
    assert restore_storage_backup(client, manifest, out_dir=tmp_path) == 1
    assert client.configuration == before
    again = provision_institution(client, settings, "synthetic")
    assert again.created_buckets == [] and sorted(again.existing_buckets) == sorted(
        result.created_buckets
    )
