"""Inventory and optionally copy the AequorOS object store for recovery.

    cd backend
    uv run python -m scripts.backup_storage --out-dir /var/backups/aequoros/objects
    uv run python -m scripts.backup_storage --out-dir /var/backups/aequoros/objects --download

Regulatory artifacts — sealed return PDFs, signed packages, ingestion source
files — live in object storage, not in Postgres. A database backup alone
therefore recovers the *ledger* of what was filed and loses the *artifacts*
themselves, so this runs alongside :mod:`scripts.backup_database`.

Two modes, because they answer different questions:

* **inventory** (default) — every bucket, key, size, ETag and last-modified
  time, written to a manifest. Cheap enough to run often, and it is what proves
  after the fact that an object existed at a point in time.
* **``--download``** — additionally streams each object to ``--out-dir`` and
  records a SHA-256 per object. This is the copy an actual recovery needs.

**The HEAD problem.** This deployment's S3-compatible endpoint sits behind a
WAF that 403s and, worse, sometimes *times out* ``HEAD`` requests. The obvious
implementation — list keys, then ``head_object`` each for its metadata — stalls
for minutes and then fails. So this script never issues ``HEAD``: size and ETag
come from the ``ListObjectsV2`` response, which is a GET and passes. Downloads
use ``GetObject`` for the same reason.

Failures are recorded per bucket rather than aborting the run: a WAF that
blocks one bucket must not cost you the inventory of the other twenty. The exit
code is non-zero when anything failed, and the manifest names what.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.config import get_storage_settings
from scripts.dr_common import DisasterRecoveryError, load_env_file
from scripts.dr_manifest import utc_now_iso


class _Readable(Protocol):
    def read(self, size: int = ..., /) -> bytes: ...


class _Paginator(Protocol):
    def paginate(self, *, Bucket: str) -> Iterable[object]: ...  # noqa: N803 - boto3's own name


class _S3Client(Protocol):
    """Just the four calls this script makes, so boto3's `Any` stops here.

    Deliberately not the whole client: a protocol that names only what is used
    is the part a reader has to trust, and every addition to it is a new call
    against an endpoint whose WAF has opinions (see the module note on HEAD).
    """

    def list_buckets(self) -> object: ...
    def get_paginator(self, operation_name: str, /) -> _Paginator: ...
    def get_object(self, **kwargs: object) -> object: ...
    def get_bucket_versioning(self, *, Bucket: str) -> object: ...  # noqa: N803


# boto3 ships no types, so every response field arrives as `Any`. These narrow
# it once at the boundary — the same discipline #485 asks for in the engines —
# so the version-aware code below adds no `Any` to the type-check baseline.
def _as_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value)


def _as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _text(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    return "" if value is None else str(value)


def _number(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key)
    return value if isinstance(value, int) else 0


def _flag(mapping: dict[str, object], key: str) -> bool:
    return bool(mapping.get(key))


@dataclass
class ObjectRecord:
    key: str
    size: int
    etag: str
    last_modified: str
    sha256: str = ""
    #: The store's own identity for this revision. Without it a restore cannot
    #: put the bytes back under the id the database pinned (#501).
    version_id: str = ""
    #: False for a superseded revision that something still references.
    is_latest: bool = True
    #: A delete marker has no bytes. Recorded so a restore can reproduce the
    #: tombstone rather than resurrect the object underneath it.
    delete_marker: bool = False
    #: Captured from the GetObject response, never HEAD — see the module note.
    server_side_encryption: str = ""
    sse_kms_key_id: str = ""


@dataclass
class BucketRecord:
    bucket: str
    objects: int = 0
    bytes: int = 0
    error: str = ""
    #: Whether the bucket itself retains versions. An unversioned bucket cannot
    #: hold a pinned revision, so this is the honest answer to "is this
    #: recoverable", recorded per bucket rather than assumed per deployment.
    versioning: str = "unknown"
    keys: list[ObjectRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


#: 1 = current keys only (pre-#501). 2 = every retained version, each with its
#: own identity and encryption metadata. A restore must refuse a v1 manifest
#: rather than quietly reinstate current bytes under fresh version ids.
STORAGE_MANIFEST_SCHEMA_VERSION = 2


@dataclass
class StorageBackupReport:
    taken_at: str
    endpoint: str
    downloaded: bool
    schema_version: int = STORAGE_MANIFEST_SCHEMA_VERSION
    buckets: list[BucketRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(b.ok for b in self.buckets)

    @property
    def total_objects(self) -> int:
        return sum(b.objects for b in self.buckets)

    @property
    def total_bytes(self) -> int:
        return sum(b.bytes for b in self.buckets)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")


def _client() -> tuple[_S3Client, str]:
    """Build an S3 client from the application's own storage settings."""
    settings = get_storage_settings()
    if not settings.configured:
        msg = (
            "Object storage is not configured (S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY). "
            "Pass --env-file backend/.env or export them."
        )
        raise DisasterRecoveryError(msg)
    client = boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name=settings.region,
        config=BotoConfig(
            s3={"addressing_style": "path" if settings.force_path_style else "auto"},
            # Bounded: a WAF that blackholes a request must not hang the backup.
            connect_timeout=15,
            read_timeout=120,
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )
    return cast("_S3Client", client), str(settings.endpoint)


def list_buckets(client: _S3Client, *, prefix: str) -> list[str]:
    response = _as_dict(client.list_buckets())
    names = [_text(_as_dict(b), "Name") for b in _as_list(response.get("Buckets"))]
    return sorted(n for n in names if not prefix or n.startswith(prefix))


def _safe_version(version_id: str) -> str:
    """A filesystem-safe stand-in for a version id.

    Version ids are opaque: MinIO issues UUIDs, S3 issues URL-safe base64 that
    can carry a dot or a slash. The real id lives in the manifest; this is only
    the name on disk, so a digest is both safe and stable.
    """
    return hashlib.sha256(version_id.encode()).hexdigest()[:32]


def _download(
    client: _S3Client, bucket: str, key: str, destination: Path, *, version_id: str = ""
) -> tuple[str, str, str]:
    """Stream one revision to disk (GET, never HEAD).

    Returns its SHA-256 plus the encryption metadata the response carries.
    GetObject answers with ``ServerSideEncryption`` and ``SSEKMSKeyId``, so a
    versioned backup records how each revision was encrypted without issuing
    the HEAD this endpoint's WAF stalls.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    request: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id:
        request["VersionId"] = version_id
    response = _as_dict(client.get_object(**request))
    body = cast("_Readable", response["Body"])
    with destination.open("wb") as handle:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
            handle.write(chunk)
    return (
        digest.hexdigest(),
        _text(response, "ServerSideEncryption"),
        _text(response, "SSEKMSKeyId"),
    )


def bucket_versioning(client: _S3Client, bucket: str) -> str:
    """``Enabled`` / ``Suspended`` / ``Unversioned``, or why we could not tell."""
    try:
        response = _as_dict(client.get_bucket_versioning(Bucket=bucket))
    except (ClientError, BotoCoreError) as exc:
        return f"unknown ({type(exc).__name__})"
    status = _text(response, "Status")
    return status or "Unversioned"


def inventory_bucket(client: _S3Client, bucket: str, *, out_dir: Path | None) -> BucketRecord:
    """List (and optionally copy) every RETAINED REVISION in one bucket.

    ``ListObjectVersions``, not ``ListObjectsV2`` (#501). The latter answers
    only with current keys and carries no version identity, so a backup taken
    with it loses two things at once: every superseded revision something still
    references, and the ids a restore needs to put the bytes back under. The
    database pins `storage_version_id` on signed artifacts and ICAAP
    attachments, and a re-upload mints fresh ids — so after a bucket loss those
    downloads 404 even though the bytes are sitting in the backup.

    It is a GET, like ``ListObjectsV2``, so the no-HEAD rule above still holds.

    Delete markers are recorded with no bytes. A tombstone is part of the
    object's history: restoring the revision underneath one would resurrect a
    document somebody deleted.
    """
    record = BucketRecord(bucket=bucket, versioning=bucket_versioning(client, bucket))
    try:
        paginator = client.get_paginator("list_object_versions")
        for raw_page in paginator.paginate(Bucket=bucket):
            page = _as_dict(raw_page)
            for raw_item in _as_list(page.get("Versions")):
                item = _as_dict(raw_item)
                key = _text(item, "Key")
                version_id = _text(item, "VersionId")
                entry = ObjectRecord(
                    key=key,
                    size=_number(item, "Size"),
                    etag=_text(item, "ETag").strip('"'),
                    last_modified=_text(item, "LastModified"),
                    version_id=version_id,
                    is_latest=_flag(item, "IsLatest"),
                )
                if out_dir is not None:
                    # The current revision keeps the plain `<bucket>/<key>`
                    # path an operator can eyeball, and a runbook already
                    # names. Superseded revisions go to a flat sidecar tree
                    # keyed by version, which cannot collide with any object
                    # path however the keys are shaped.
                    destination = (
                        out_dir / bucket / key
                        if entry.is_latest
                        else out_dir / ".versions" / bucket / _safe_version(version_id)
                    )
                    (
                        entry.sha256,
                        entry.server_side_encryption,
                        entry.sse_kms_key_id,
                    ) = _download(client, bucket, key, destination, version_id=version_id)
                record.keys.append(entry)
                record.objects += 1
                record.bytes += entry.size
            for raw_marker in _as_list(page.get("DeleteMarkers")):
                marker = _as_dict(raw_marker)
                record.keys.append(
                    ObjectRecord(
                        key=_text(marker, "Key"),
                        size=0,
                        etag="",
                        last_modified=_text(marker, "LastModified"),
                        version_id=_text(marker, "VersionId"),
                        is_latest=_flag(marker, "IsLatest"),
                        delete_marker=True,
                    )
                )
                record.objects += 1
    except (ClientError, BotoCoreError) as exc:
        # Recorded, not raised: one blocked bucket must not cost the whole run.
        record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    return record


def run_storage_backup(
    *, out_dir: Path, download: bool, bucket_prefix: str = "aequoros-"
) -> StorageBackupReport:
    client, endpoint = _client()
    out_dir.mkdir(parents=True, exist_ok=True)
    report = StorageBackupReport(
        taken_at=utc_now_iso(), endpoint=endpoint, downloaded=download
    )
    try:
        buckets = list_buckets(client, prefix=bucket_prefix)
    except (ClientError, BotoCoreError) as exc:
        msg = f"Could not list buckets at {endpoint}: {type(exc).__name__}: {str(exc)[:200]}"
        raise DisasterRecoveryError(msg) from exc

    print(f"[storage] endpoint={endpoint} buckets={len(buckets)} download={download}")
    for bucket in buckets:
        record = inventory_bucket(client, bucket, out_dir=out_dir if download else None)
        report.buckets.append(record)
        status = "OK" if record.ok else f"FAILED ({record.error})"
        superseded = sum(1 for k in record.keys if not k.is_latest and not k.delete_marker)
        print(
            f"[storage]   {bucket}: {record.objects} revision(s) "
            f"({superseded} superseded), {record.bytes} bytes, "
            f"versioning={record.versioning} — {status}"
        )
        if record.ok and record.versioning not in {"Enabled"}:
            # Said out loud: an unversioned bucket cannot hold the revision a
            # pinned download asks for, so no backup of it is recoverable.
            print(
                f"[storage]   WARNING {bucket}: versioning is {record.versioning}; "
                "pinned revisions cannot be restored for this bucket."
            )

    manifest = out_dir / f"storage-inventory-{report.taken_at.replace(':', '')}.json"
    report.write(manifest)
    print(
        f"[storage] {report.total_objects} object(s), {report.total_bytes} bytes across "
        f"{len(report.buckets)} bucket(s); manifest={manifest}"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--download", action="store_true", help="Copy object bytes, not just the inventory."
    )
    parser.add_argument("--bucket-prefix", default="aequoros-")
    parser.add_argument("--env-file", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.env_file is not None:
        load_env_file(args.env_file)
    report = run_storage_backup(
        out_dir=args.out_dir, download=args.download, bucket_prefix=args.bucket_prefix
    )
    return 0 if report.ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[storage] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
