"""Publish logical backup/manifest pairs with version-specific compliance retention."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from uuid import uuid4

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.core.tls import require_boto_tls, require_https
from app.storage.client import StorageValidationError
from app.storage.config import get_storage_settings
from app.storage.retention import (
    RetentionClient,
    require_object_lock,
    retain_version,
    retention_errors,
    retention_until,
)


class BackupClient(RetentionClient, Protocol):
    def upload_fileobj(
        self, Fileobj: BinaryIO, Bucket: str, Key: str, *, ExtraArgs: dict[str, object]
    ) -> object: ...

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...


def publish_backup(
    archive: Path, manifest: Path, *, client: BackupClient | None = None
) -> dict[str, str]:
    """When enabled, fail closed rather than leave an unretained cloud backup.

    Local artifacts remain available for recovery if publication fails. Random
    prefixes prevent another backup run from replacing the latest key version.
    """
    settings = get_storage_settings()
    if not settings.object_lock_enabled:
        return {}
    bucket = os.getenv("BACKUP_WORM_BUCKET", "").strip()
    if not bucket:
        raise StorageValidationError(
            "BACKUP_WORM_BUCKET is required for retained database backups."
        )
    if not settings.kms_key_id:
        raise StorageValidationError(
            "STORAGE_KMS_KEY_ID is required for retained database backups."
        )
    if settings.endpoint:
        require_https(settings.endpoint, field="S3_ENDPOINT")
    if client is None:
        factory = cast(Callable[..., object], boto3.client)
        client = cast(
            BackupClient,
            factory(
                "s3",
                endpoint_url=settings.endpoint,
                region_name=settings.region,
                verify=get_settings().tls.ca_bundle or True,
                use_ssl=True,
                aws_access_key_id=settings.access_key,
                aws_secret_access_key=settings.secret_key,
                config=Config(connect_timeout=15, read_timeout=120),
            ),
        )
        require_boto_tls(client)
    require_object_lock(client, bucket)
    until = retention_until(settings)
    prefix = f"database-backups/{uuid4()}"
    versions: dict[str, str] = {}
    with retention_errors():
        for path in (archive, manifest):
            key = f"{prefix}/{path.name}"
            digest = hashlib.sha256()
            with path.open("rb") as data:
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    digest.update(chunk)
                _ = data.seek(0)
                _ = client.upload_fileobj(
                    data,
                    bucket,
                    key,
                    ExtraArgs={
                        "ObjectLockMode": "COMPLIANCE",
                        "ObjectLockRetainUntilDate": until,
                        "ChecksumAlgorithm": "SHA256",
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": settings.kms_key_id,
                        "Metadata": {"checksum-sha256": digest.hexdigest()},
                    },
                )
            version = client.head_object(Bucket=bucket, Key=key).get("VersionId")
            if not isinstance(version, str):
                raise StorageValidationError("Retained backup did not return a version ID.")
            retain_version(client, bucket, key, version, until)
            versions[key] = version
    return versions
