"""Tests for test_object_lock."""

from __future__ import annotations

# pyright: reportMissingTypeStubs=false
from collections.abc import Callable
from pathlib import Path
from typing import cast

import boto3
import pytest
from botocore.client import BaseClient
from moto import mock_aws

from app.storage.backups import BackupClient, publish_backup
from app.storage.config import get_storage_settings
from app.storage.provisioning import ProvisioningClient
from tests.storage.test_object_lock_integration import (
    LockClient,
    _moto_retention,
    _set_default_retention,
)


@pytest.mark.parametrize("default_mode", [None, "COMPLIANCE", "GOVERNANCE"])
def test_backup_pair_is_published_with_compliance_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default_mode: str | None,
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
        if default_mode is not None:
            _set_default_retention(
                cast(LockClient, cast(object, s3)), "synthetic-backups", default_mode
            )
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
