from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

import pytest

from scripts.backup_storage import (
    BucketRecord,
    ObjectRecord,
    StorageBackupReport,
    restore_storage_backup,
)
from scripts.dr_common import DisasterRecoveryError


class RecoveryClient:
    def __init__(self) -> None:
        self.buckets: list[str] = []
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str], str]] = {}

    def list_buckets(self) -> dict[str, object]:
        return {"Buckets": [{"Name": bucket} for bucket in self.buckets]}

    def create_bucket(self, **kwargs: object) -> object:
        self.buckets.append(str(kwargs["Bucket"]))
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
        self.objects[Bucket, Key] = Body.read(), Metadata, ContentType
        return {}


def _manifest(root: Path, key: str = "object") -> Path:
    source = root / "synthetic-bucket" / "object"
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
            BucketRecord("synthetic-bucket", objects=1, bytes=20, keys=[entry]),
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
