"""The storage backup must record every retained revision, with its identity.

#501: `backup_storage` listed CURRENT keys only, through `ListObjectsV2`, which
carries no version identity. Two things were lost at once — every superseded
revision something still references, and the ids a restore needs to put bytes
back under. The database pins `storage_version_id` on signed regulatory
artifacts, ICAAP attachments and supervisory letters, and a re-upload mints
fresh ids, so after a bucket loss those downloads 404 while the bytes sit in
the backup. A backup that cannot be restored from is not a backup.

Nothing tested this script before, which is how a recovery gap stayed open: the
only proof a backup works is a restore, and the only proof it CAN be restored
from is that it carries the identity the restore needs.

The fake client is deliberately small and literal — it answers
`ListObjectVersions` the way S3 and MinIO do, newest first, with delete markers
in their own list — because a mock that is generous about shape proves nothing
about a store that is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.backup_storage import (
    STORAGE_MANIFEST_SCHEMA_VERSION,
    BucketRecord,
    ObjectRecord,
    inventory_bucket,
)


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._sent = False

    def read(self, size: int = -1, /) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._payload


class _FakeS3:
    """Versioned object store, as much of one as this script talks to."""

    def __init__(
        self,
        *,
        versions: list[dict[str, Any]],
        delete_markers: list[dict[str, Any]] | None = None,
        payloads: dict[str, bytes] | None = None,
        versioning: str | None = "Enabled",
        pages: int = 1,
    ) -> None:
        self._versions = versions
        self._delete_markers = delete_markers or []
        self._payloads = payloads or {}
        self._versioning = versioning
        self._pages = pages
        #: Every GetObject this run issued, so a test can assert WHICH
        #: revisions were fetched rather than only how many.
        self.fetched: list[tuple[str, str]] = []

    def get_bucket_versioning(self, *, Bucket: str) -> object:  # noqa: N803
        return {"Status": self._versioning} if self._versioning else {}

    def get_paginator(self, operation_name: str, /) -> _FakeS3:
        assert operation_name == "list_object_versions", (
            "the backup must list VERSIONS: ListObjectsV2 answers with current "
            "keys only and no version identity (#501)"
        )
        return self

    def paginate(self, *, Bucket: str) -> Iterable[object]:  # noqa: N803
        def pages() -> Iterator[object]:
            # Split across pages so a paginated bucket is exercised: the defect
            # being guarded against is a lost revision, and a single-page fake
            # cannot lose one.
            size = max(1, len(self._versions) // self._pages or 1)
            chunks = [self._versions[i : i + size] for i in range(0, len(self._versions), size)]
            for index, chunk in enumerate(chunks or [[]]):
                page: dict[str, Any] = {"Versions": chunk}
                if index == len(chunks) - 1:
                    page["DeleteMarkers"] = self._delete_markers
                yield page

        return pages()

    def get_object(self, **kwargs: object) -> object:
        key = str(kwargs["Key"])
        version_id = str(kwargs.get("VersionId", ""))
        self.fetched.append((key, version_id))
        return {
            "Body": _Body(self._payloads.get(version_id, b"")),
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "aequoros-key",
        }

    def list_buckets(self) -> object:
        return {"Buckets": []}


def _version(key: str, version_id: str, *, latest: bool, size: int = 3) -> dict[str, Any]:
    return {
        "Key": key,
        "VersionId": version_id,
        "IsLatest": latest,
        "Size": size,
        "ETag": f'"{version_id}-etag"',
        "LastModified": "2026-10-10 00:00:00+00:00",
    }


def _entry(record: BucketRecord, version_id: str) -> ObjectRecord:
    found = [k for k in record.keys if k.version_id == version_id]
    assert found, f"version {version_id} is absent from the manifest"
    return found[0]


def test_every_retained_revision_is_recorded_with_its_identity() -> None:
    """The whole point: a superseded revision is in the manifest, with its id.

    A pinned download asks for one exact `storage_version_id`. If the manifest
    names only the current revision, that download cannot be restored however
    complete the bytes are.
    """
    client = _FakeS3(
        versions=[
            _version("outputs/LCR.pdf", "v3", latest=True),
            _version("outputs/LCR.pdf", "v2", latest=False),
            _version("outputs/LCR.pdf", "v1", latest=False),
        ]
    )
    record = inventory_bucket(client, "aequoros-sample", out_dir=None)

    assert record.error == ""
    assert record.objects == 3, "a superseded revision is not an optional extra"
    assert {k.version_id for k in record.keys} == {"v1", "v2", "v3"}
    assert _entry(record, "v3").is_latest is True
    assert _entry(record, "v1").is_latest is False
    assert record.versioning == "Enabled"


def test_superseded_revisions_are_downloaded_to_their_own_paths() -> None:
    """Bytes for every revision, and no revision overwriting another on disk.

    The current revision keeps the plain `<bucket>/<key>` path a runbook names;
    superseded ones go to a flat sidecar tree keyed by version, which cannot
    collide with an object path whatever the keys look like.
    """
    client = _FakeS3(
        versions=[
            _version("outputs/LCR.pdf", "v2", latest=True),
            _version("outputs/LCR.pdf", "v1", latest=False),
        ],
        payloads={"v2": b"new", "v1": b"old"},
    )
    out = Path(__file__).parent / "_backup_probe"
    record = inventory_bucket(client, "aequoros-sample", out_dir=out)
    try:
        current = out / "aequoros-sample" / "outputs" / "LCR.pdf"
        assert current.read_bytes() == b"new"
        sidecar = list((out / ".versions" / "aequoros-sample").iterdir())
        assert len(sidecar) == 1, "the superseded revision needs its own file"
        assert sidecar[0].read_bytes() == b"old"
        # Each revision fetched BY ID — not the key twice, which would copy the
        # current bytes into both slots and look like a complete backup.
        assert client.fetched == [("outputs/LCR.pdf", "v2"), ("outputs/LCR.pdf", "v1")]
        assert _entry(record, "v1").sha256 != _entry(record, "v2").sha256
    finally:
        for path in sorted(out.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        out.rmdir()


def test_encryption_metadata_rides_with_each_revision() -> None:
    """Captured from GetObject, because this endpoint's WAF stalls HEAD.

    A restore has to re-encrypt under the same key; without this the manifest
    says what the bytes were, not how they were protected.
    """
    client = _FakeS3(
        versions=[_version("outputs/LCR.pdf", "v1", latest=True)],
        payloads={"v1": b"x"},
    )
    out = Path(__file__).parent / "_backup_sse_probe"
    record = inventory_bucket(client, "aequoros-sample", out_dir=out)
    try:
        assert _entry(record, "v1").server_side_encryption == "aws:kms"
        assert _entry(record, "v1").sse_kms_key_id == "aequoros-key"
    finally:
        for path in sorted(out.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        out.rmdir()


def test_a_delete_marker_is_recorded_and_not_fetched() -> None:
    """A tombstone is part of the history, and has no bytes.

    Restoring the revision under a delete marker would resurrect a document
    somebody deleted, so the marker is recorded as one rather than skipped.
    """
    client = _FakeS3(
        versions=[_version("outputs/LCR.pdf", "v1", latest=False)],
        delete_markers=[
            {
                "Key": "outputs/LCR.pdf",
                "VersionId": "dm1",
                "IsLatest": True,
                "LastModified": "2026-10-10 01:00:00+00:00",
            }
        ],
        payloads={"v1": b"x"},
    )
    out = Path(__file__).parent / "_backup_dm_probe"
    record = inventory_bucket(client, "aequoros-sample", out_dir=out)
    try:
        marker = _entry(record, "dm1")
        assert marker.delete_marker is True
        assert marker.size == 0
        assert ("outputs/LCR.pdf", "dm1") not in client.fetched, "a marker has no bytes to GET"
    finally:
        for path in sorted(out.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        out.rmdir()


def test_revisions_survive_pagination() -> None:
    client = _FakeS3(
        versions=[_version("outputs/LCR.pdf", f"v{i}", latest=i == 4) for i in range(1, 5)],
        pages=2,
    )
    record = inventory_bucket(client, "aequoros-sample", out_dir=None)
    assert {k.version_id for k in record.keys} == {"v1", "v2", "v3", "v4"}


@pytest.mark.parametrize("status", [None, "Suspended"])
def test_an_unversioned_bucket_is_reported_as_such(status: str | None) -> None:
    """Stated, not inferred. A bucket without versioning cannot hold the
    revision a pinned download asks for, so no backup of it is recoverable —
    and that is a fact about the deployment the manifest should carry."""
    client = _FakeS3(versions=[_version("outputs/LCR.pdf", "", latest=True)], versioning=status)
    record = inventory_bucket(client, "aequoros-sample", out_dir=None)
    assert record.versioning == (status or "Unversioned")


def test_the_manifest_schema_version_marks_versioned_backups() -> None:
    """A v1 manifest holds current keys under no identity. A restore must be
    able to tell the two apart and refuse the older shape rather than quietly
    reinstating current bytes under fresh version ids."""
    assert STORAGE_MANIFEST_SCHEMA_VERSION == 2
