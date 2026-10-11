from __future__ import annotations

# pyright: reportMissingTypeStubs=false
import hashlib
import io
from pathlib import Path
from typing import BinaryIO

import pytest
from botocore.exceptions import ClientError

from app.storage.config import StorageEngineSettings
from app.storage.provisioning import TEMP_EXPIRY_DAYS, ensure_audit_bucket, provision_institution
from scripts import backup_storage
from scripts.backup_storage import (
    STORAGE_FORMAT_VERSION,
    BucketRecord,
    ObjectRecord,
    StorageBackupReport,
    inventory_bucket,
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
        #: Every revision in the order it was written, so a test can assert
        #: that a replay reproduced the TIMELINE and not just the bytes.
        self.timeline: list[tuple[str, str, bytes]] = []
        self.deleted: list[tuple[str, str]] = []
        self._minted = 0

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

    def get_object(self, *, Bucket: str, Key: str, VersionId: str = "") -> dict[str, object]:
        raise NotImplementedError

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: BinaryIO,
        Metadata: dict[str, str],
        ContentType: str,
    ) -> dict[str, object]:
        self.at_upload[Bucket] = dict(self.configuration[Bucket])
        payload = Body.read()
        self.objects[Bucket, Key] = payload, Metadata, ContentType
        self.timeline.append((Bucket, Key, payload))
        # A versioned store answers with the id it minted. That id is the whole
        # reason a restore can be reconciled, so the fake issues real ones.
        self._minted += 1
        return {"VersionId": f"restored-{self._minted}"}

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        self.deleted.append((Bucket, Key))
        _ = self.objects.pop((Bucket, Key), None)
        return {}


def _revision(  # noqa: PLR0913 - a revision IS its seven identifying parts
    root: Path,
    *,
    key: str,
    bucket: str,
    version_id: str,
    payload: bytes,
    latest: bool,
    when: str,
) -> ObjectRecord:
    """One backed-up revision, with its bytes written where the manifest says."""
    entry = ObjectRecord(
        key,
        len(payload),
        "etag",
        when,
        metadata={"key-envelope-id": "synthetic-envelope", "encryption-format": "synthetic"},
        content_type="application/octet-stream",
        version_id=version_id,
        is_latest=latest,
    )
    entry.backup_path = (
        f"{bucket}/{key}"
        if latest
        else f".versions/{bucket}/{hashlib.sha256(version_id.encode()).hexdigest()[:32]}"
    )
    source = root / entry.backup_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    entry.sha256 = hashlib.sha256(payload).hexdigest()
    return entry


def _manifest(
    root: Path,
    key: str = "object",
    bucket: str = "synthetic-bucket",
    *,
    entries: list[ObjectRecord] | None = None,
    format_version: int = STORAGE_FORMAT_VERSION,
) -> Path:
    keys = entries or [
        _revision(
            root,
            key=key,
            bucket=bucket,
            version_id="backed-up-1",
            payload=b"synthetic ciphertext",
            latest=True,
            when="2026-10-01",
        )
    ]
    path = root / "manifest.json"
    StorageBackupReport(
        "synthetic",
        "synthetic",
        True,
        [BucketRecord(bucket, objects=len(keys), bytes=20, keys=keys)],
        format_version=format_version,
    ).write(path)
    return path


def test_restore_recreates_missing_bucket_and_reapplies_headers(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    client = RecoveryClient()
    assert restore_storage_backup(client, manifest, out_dir=tmp_path).objects == 1
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
    assert restore_storage_backup(client, manifest, out_dir=tmp_path).objects == 1
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
    assert restore_storage_backup(client, manifest, out_dir=tmp_path).objects == 1
    assert client.configuration == before
    again = provision_institution(client, settings, "synthetic")
    assert again.created_buckets == [] and sorted(again.existing_buckets) == sorted(
        result.created_buckets
    )


# --- #501: version identity must survive the round trip ----------------------
#
# These use the monkeypatched-settings fixture shape of the tests above, because
# the three that read the ambient storage settings already fail on any machine
# with an S3_REGION in `.env`.


@pytest.fixture
def local_settings(monkeypatch: pytest.MonkeyPatch) -> StorageEngineSettings:
    settings = StorageEngineSettings.model_validate(
        {
            "STORAGE_BACKEND": "s3",
            "STORAGE_ENV": "dev",
            "S3_ENDPOINT": None,
            "S3_REGION": "us-east-1",
            "STORAGE_KMS_KEY_ID": "synthetic-sse-key",
        }
    )
    monkeypatch.setattr(backup_storage, "get_storage_settings", lambda: settings)
    return settings


def test_restore_replays_every_revision_oldest_first(
    tmp_path: Path, local_settings: StorageEngineSettings
) -> None:
    """The timeline, not just the bytes.

    `put_object` APPENDS a revision, so replaying in manifest order — which
    `ListObjectVersions` returns newest-first — would bury the current document
    under a superseded one, and the plain download would serve the wrong
    figures. Order is the correctness property here, not an optimisation.
    """
    _ = local_settings
    bucket = "aequoros-dev-synthetic-outputs"
    entries = [
        _revision(tmp_path, key="LCR.pdf", bucket=bucket, version_id="v3",
                  payload=b"third", latest=True, when="2026-10-03"),
        _revision(tmp_path, key="LCR.pdf", bucket=bucket, version_id="v2",
                  payload=b"second", latest=False, when="2026-10-02"),
        _revision(tmp_path, key="LCR.pdf", bucket=bucket, version_id="v1",
                  payload=b"first", latest=False, when="2026-10-01"),
    ]
    manifest = _manifest(tmp_path, bucket=bucket, entries=entries)
    client = RecoveryClient()

    report = restore_storage_backup(client, manifest, out_dir=tmp_path)

    assert report.objects == 3, "a superseded revision is not optional"
    assert [payload for _b, _k, payload in client.timeline] == [b"first", b"second", b"third"]
    # And the current bytes are the newest, which is what a plain download gets.
    data, _headers, _ct = client.objects[bucket, "LCR.pdf"]
    assert data == b"third"


def test_restore_records_which_new_version_replaced_which_old_one(
    tmp_path: Path, local_settings: StorageEngineSettings
) -> None:
    """The mapping is the deliverable.

    The database pins the OLD `storage_version_id`; the store mints a new one.
    Without this pairing the bytes are back and every pinned download still
    404s — bytes restored is not recovery.
    """
    _ = local_settings
    bucket = "aequoros-dev-synthetic-outputs"
    entries = [
        _revision(tmp_path, key="LCR.pdf", bucket=bucket, version_id="signed-rev",
                  payload=b"signed", latest=False, when="2026-10-01"),
        _revision(tmp_path, key="LCR.pdf", bucket=bucket, version_id="current-rev",
                  payload=b"current", latest=True, when="2026-10-02"),
    ]
    manifest = _manifest(tmp_path, bucket=bucket, entries=entries)

    report = restore_storage_backup(RecoveryClient(), manifest, out_dir=tmp_path)

    mapping = {v.backed_up_version_id: v.restored_version_id for v in report.versions}
    assert set(mapping) == {"signed-rev", "current-rev"}
    assert all(new for new in mapping.values()), "a revision with no new id cannot be reconciled"
    assert len(set(mapping.values())) == 2, "two revisions must not collapse onto one id"
    # The pairing is per revision, so the signed one can be found by its old id.
    signed = next(v for v in report.versions if v.backed_up_version_id == "signed-rev")
    assert signed.key == "LCR.pdf"
    assert signed.sha256 == hashlib.sha256(b"signed").hexdigest()


def test_restore_reproduces_a_delete_marker_instead_of_resurrecting(
    tmp_path: Path, local_settings: StorageEngineSettings
) -> None:
    """A tombstone is part of the history.

    Replaying the revision under a delete marker and stopping there would
    resurrect a document somebody deleted — a worse outcome than losing it,
    because nothing announces it.
    """
    _ = local_settings
    bucket = "aequoros-dev-synthetic-outputs"
    entries = [
        _revision(tmp_path, key="withdrawn.pdf", bucket=bucket, version_id="v1",
                  payload=b"withdrawn", latest=False, when="2026-10-01"),
    ]
    marker = ObjectRecord(
        "withdrawn.pdf", 0, "", "2026-10-02", version_id="dm1", is_latest=True, delete_marker=True
    )
    manifest = _manifest(tmp_path, bucket=bucket, entries=[*entries, marker])

    client = RecoveryClient()
    report = restore_storage_backup(client, manifest, out_dir=tmp_path)

    assert report.delete_markers == 1
    assert client.deleted == [(bucket, "withdrawn.pdf")]
    assert (bucket, "withdrawn.pdf") not in client.objects, "the document stays deleted"
    # The marker is not an object, so it earns no mapping row.
    assert [v.backed_up_version_id for v in report.versions] == ["v1"]


def test_restore_refuses_a_manifest_that_carries_no_version_identity(
    tmp_path: Path, local_settings: StorageEngineSettings
) -> None:
    """Format 2 is refused, loudly.

    Its entries have no version id, so replaying one mints ids nothing
    references. Accepting it would put the bytes back and leave every pinned
    download broken while reporting success — the defect, not a degraded mode
    of it.
    """
    _ = local_settings
    manifest = _manifest(tmp_path, format_version=2)
    client = RecoveryClient()
    with pytest.raises(DisasterRecoveryError, match="version identity|format version"):
        _ = restore_storage_backup(client, manifest, out_dir=tmp_path)
    assert client.objects == {}, "nothing is uploaded on a refusal"


class _VersionedListing:
    """Just enough store to answer ListObjectVersions and GetObject by id."""

    def __init__(self, revisions: list[dict[str, object]], payloads: dict[tuple[str, str], bytes]):
        self._revisions = revisions
        self._payloads = payloads

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, object]:
        return {"Status": "Enabled"}

    def get_paginator(self, name: str, /) -> _VersionedListing:
        assert name == "list_object_versions"
        return self

    def paginate(self, *, Bucket: str) -> list[dict[str, object]]:
        return [{"Versions": self._revisions}]

    def get_object(self, *, Bucket: str, Key: str, VersionId: str = "") -> dict[str, object]:
        return {"Body": io.BytesIO(self._payloads[Key, VersionId])}


def test_two_null_versioned_keys_do_not_share_a_backup_file(tmp_path: Path) -> None:
    """A version id is scoped to its KEY, so two keys may share one id.

    S3's historical `null` version id can exist under many keys. While the
    backup named files by the id alone, two unrelated revisions landed on the
    same path — the second overwriting the first — which surfaces later as a
    checksum failure or, far worse, a recovery that hands back another object's
    bytes. Asserted through `inventory_bucket` rather than the naming helper:
    what matters is that both revisions are on disk, intact.
    """
    revisions: list[dict[str, object]] = [
        {"Key": "a/report.csv", "VersionId": "null", "IsLatest": False, "Size": 5},
        {"Key": "b/report.csv", "VersionId": "null", "IsLatest": False, "Size": 5},
    ]
    client = _VersionedListing(
        revisions, {("a/report.csv", "null"): b"aaaaa", ("b/report.csv", "null"): b"bbbbb"}
    )

    record = inventory_bucket(client, "bucket", out_dir=tmp_path)

    assert record.error == ""
    stored = {entry.key: (tmp_path / entry.backup_path).read_bytes() for entry in record.keys}
    assert stored == {"a/report.csv": b"aaaaa", "b/report.csv": b"bbbbb"}
    assert len({entry.backup_path for entry in record.keys}) == 2
    # And each checksum describes its own bytes, not the survivor's.
    digests = {entry.key: entry.sha256 for entry in record.keys}
    assert digests["a/report.csv"] == hashlib.sha256(b"aaaaa").hexdigest()
    assert digests["b/report.csv"] == hashlib.sha256(b"bbbbb").hexdigest()


def test_an_unversioned_bucket_is_not_reported_as_recoverable() -> None:
    """`ok` is the wrong question to end a recovery drill on.

    Without versioning the store keeps one revision per key, so the backup
    cannot contain the revision a pinned download asks for however completely
    it copied the current bytes. The run says so and does not exit 0.
    """
    copied = BucketRecord("bucket", objects=1, versioning="Unversioned")
    assert copied.ok, "the bytes were copied"
    assert not copied.recoverable, "but a pinned revision cannot come back from them"

    report = StorageBackupReport("now", "endpoint", True, [copied])
    assert report.ok and not report.recoverable

    versioned = BucketRecord("bucket", objects=1, versioning="Enabled")
    assert StorageBackupReport("now", "endpoint", True, [versioned]).recoverable


def test_a_report_with_no_buckets_is_not_recoverable() -> None:
    """An empty sweep is not a clean bill of health."""
    assert not StorageBackupReport("now", "endpoint", True, []).recoverable
