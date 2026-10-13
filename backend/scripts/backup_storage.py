"""Inventory, copy or restore the AequorOS object store.

Backup and restore commands, encrypted-object recovery requirements and known
limitations are owned by ``backend/docs/bank_key_setup.md``.

**The HEAD problem.** This deployment's S3-compatible endpoint sits behind a
WAF that 403s and, worse, sometimes *times out* ``HEAD`` requests. The obvious
implementation — list keys, then ``head_object`` each for its metadata — stalls
for minutes and then fails. Inventory and downloads never issue object ``HEAD``:
size and ETag come from GET responses. Restore uses the provisioning module for bucket
existence checks and configuration.

Inventory and download failures are recorded per bucket rather than aborting the run: a WAF that
blocks one bucket must not cost you the inventory of the other twenty. The exit
code is non-zero when anything failed, and the manifest names what.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import TypeAdapter, ValidationError

from app.storage.client import TIERS
from app.storage.config import get_storage_settings
from app.storage.provisioning import ProvisioningClient, provision_bucket
from scripts.dr_common import DisasterRecoveryError, load_env_file
from scripts.dr_manifest import utc_now_iso

#: Bumped to 3 when the backup began recording every retained revision (#501).
STORAGE_FORMAT_VERSION = 3


@dataclass
class ObjectRecord:
    key: str
    size: int
    etag: str
    last_modified: str
    sha256: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/octet-stream"
    #: The store's own identity for this revision. The database pins it on
    #: signed artifacts and ICAAP attachments, and a restore that does not
    #: carry it cannot put the bytes back under the id those rows name (#501).
    version_id: str = ""
    #: False for a superseded revision that something still references.
    is_latest: bool = True
    #: A delete marker has no bytes. Recorded so a restore reproduces the
    #: tombstone instead of resurrecting the revision underneath it.
    delete_marker: bool = False
    #: Where the bytes sit under the backup root, relative to it. Written by
    #: the backup rather than recomputed by the restore, so the two cannot
    #: disagree about a path and so the manifest alone says what was copied.
    backup_path: str = ""


@dataclass
class BucketRecord:
    bucket: str
    objects: int = 0
    bytes: int = 0
    error: str = ""
    #: ``Enabled`` / ``Suspended`` / ``Unversioned``, probed per bucket.
    versioning: str = "unknown"
    keys: list[ObjectRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def recoverable(self) -> bool:
        """Can a PINNED revision be restored from this bucket's backup?

        Only from a versioned bucket. Without versioning the store keeps one
        revision per key, so the backup cannot contain the revision a pinned
        download asks for however completely it copied the current bytes —
        which makes `ok` the wrong question to end a recovery drill on.
        """
        return self.ok and self.versioning == "Enabled"


@dataclass
class RestoredVersion:
    """One revision's identity before and after a restore.

    The database pins `storage_version_id`; `put_object` mints a fresh id. This
    is the row that lets those be reconciled, and the evidence that they were.
    """

    bucket: str
    key: str
    backed_up_version_id: str
    restored_version_id: str
    sha256: str


@dataclass
class RestoreReport:
    restored_at: str
    manifest: str
    objects: int = 0
    delete_markers: int = 0
    versions: list[RestoredVersion] = field(default_factory=list)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")


@dataclass
class StorageBackupReport:
    taken_at: str
    endpoint: str
    downloaded: bool
    buckets: list[BucketRecord] = field(default_factory=list)
    #: 1 = current keys, no metadata. 2 = current keys with metadata and
    #: checksums. 3 = every RETAINED REVISION, each with its own version
    #: identity (#501). A restore must refuse 2: its entries carry no version
    #: id, so replaying them mints fresh ids under which no pinned download
    #: resolves — which is the defect, not a degraded mode of it.
    format_version: int = 1

    @property
    def ok(self) -> bool:
        return all(b.ok for b in self.buckets)

    @property
    def recoverable(self) -> bool:
        """Every bucket backed up AND able to hold a pinned revision."""
        return bool(self.buckets) and all(b.recoverable for b in self.buckets)

    @property
    def total_objects(self) -> int:
        return sum(b.objects for b in self.buckets)

    @property
    def total_bytes(self) -> int:
        return sum(b.bytes for b in self.buckets)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")


def _client() -> tuple[Any, str]:
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
    return client, str(settings.endpoint)


def list_buckets(client: Any, *, prefix: str) -> list[str]:
    response = client.list_buckets()
    names = [str(b["Name"]) for b in response.get("Buckets", [])]
    return sorted(n for n in names if not prefix or n.startswith(prefix))


class _RecoveryClient(ProvisioningClient, Protocol):
    def get_object(self, *, Bucket: str, Key: str, VersionId: str = ...) -> dict[str, object]: ...

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: BinaryIO,
        Metadata: dict[str, str],
        ContentType: str,
    ) -> dict[str, object]: ...

    #: Reproduces a tombstone. Called with no VersionId on purpose: that is
    #: what writes a NEW delete marker, where naming one would erase a
    #: revision instead.
    def delete_object(self, *, Bucket: str, Key: str) -> object: ...


def _object_path(root: Path, bucket: str, key: str) -> Path:
    if Path(bucket).name != bucket or bucket in {".", ".."}:
        raise DisasterRecoveryError("Invalid backup bucket name.")
    bucket_root = (root / bucket).resolve()
    destination = (bucket_root / key).resolve()
    if (
        not bucket_root.is_relative_to(root.resolve())
        or not destination.is_relative_to(bucket_root)
        or ".." in Path(key).parts
    ):
        raise DisasterRecoveryError("Object key escapes the backup bucket directory.")
    return destination


def _download(
    client: _RecoveryClient, bucket: str, key: str, destination: Path, entry: ObjectRecord
) -> str:
    """Stream one named revision to disk (GET, never HEAD — see the module note).

    `VersionId` is passed when the entry has one, so a superseded revision is
    fetched as itself. Without it every GET returns the CURRENT bytes, and a
    backup of five revisions would be five copies of the newest one — complete
    by every count the manifest reports, and wrong.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    response = (
        client.get_object(Bucket=bucket, Key=key, VersionId=entry.version_id)
        if entry.version_id
        else client.get_object(Bucket=bucket, Key=key)
    )
    body = cast(BinaryIO, response["Body"])
    try:
        entry.metadata = {
            name.lower(): value
            for name, value in TypeAdapter(dict[str, str])
            .validate_python(response.get("Metadata", {}))
            .items()
        }
        entry.content_type = str(response.get("ContentType", "application/octet-stream"))
        entry.size = int(str(response.get("ContentLength", entry.size)))
        entry.etag = str(response.get("ETag", entry.etag)).strip('"')
        entry.last_modified = str(response.get("LastModified", entry.last_modified))
        with destination.open("wb") as handle:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(chunk)
                handle.write(chunk)
    finally:
        body.close()
    return digest.hexdigest()


def restore_storage_backup(
    client: _RecoveryClient, manifest: Path, *, out_dir: Path
) -> RestoreReport:
    """Replay a versioned backup, and record which new id replaced which old one.

    Three things make this a RECOVERY rather than a re-upload (#501):

    * **Oldest first, per key.** `put_object` appends a revision, so replaying
      in manifest order would leave the newest bytes buried under older ones
      and the current download would serve a superseded document. Entries are
      grouped by key and replayed by `last_modified`.
    * **The new id is captured.** `put_object` answers with the `VersionId` it
      minted. The database pins the OLD one, so without this pairing the bytes
      are back and every pinned download still 404s. The report is the
      auditable mapping, and `reconcile_storage_versions.py` applies it.
    * **Tombstones are reproduced, not skipped.** A delete marker is replayed
      as a new marker once the revisions beneath it are in place, so a document
      somebody deleted stays deleted.

    A format-2 manifest is refused: its entries carry no version identity, so
    replaying one mints ids nothing references. That is the defect, not a
    degraded mode of it.
    """
    report = TypeAdapter(StorageBackupReport).validate_json(manifest.read_bytes())
    if report.format_version != STORAGE_FORMAT_VERSION or not report.downloaded or not report.ok:
        raise DisasterRecoveryError(
            "Restore requires a complete, downloaded backup at format version "
            f"{STORAGE_FORMAT_VERSION}. A format-2 manifest records no version "
            "identity, so restoring from it cannot reinstate a pinned "
            "storage_version_id (#501)."
        )
    restored = RestoreReport(restored_at=utc_now_iso(), manifest=str(manifest))
    settings = get_storage_settings()
    for bucket in report.buckets:
        suffix = bucket.bucket.rsplit("-", 1)[-1]
        tier = suffix if suffix in TIERS else None
        _ = provision_bucket(
            client,
            settings,
            bucket.bucket,
            "audit-logs" if bucket.bucket.endswith("-audit-logs") else tier,
        )
        for key in sorted({entry.key for entry in bucket.keys}):
            timeline = sorted(
                (e for e in bucket.keys if e.key == key),
                key=lambda e: (e.last_modified, e.is_latest),
            )
            for entry in timeline:
                if entry.delete_marker:
                    _ = client.delete_object(Bucket=bucket.bucket, Key=entry.key)
                    restored.delete_markers += 1
                    continue
                source = _object_path(out_dir, *entry.backup_path.split("/", 1))
                with source.open("rb") as body:
                    if (
                        not entry.sha256
                        or hashlib.file_digest(body, "sha256").hexdigest() != entry.sha256
                    ):
                        raise DisasterRecoveryError(
                            "Backup object checksum does not match the manifest."
                        )
                    _ = body.seek(0)
                    response = client.put_object(
                        Bucket=bucket.bucket,
                        Key=entry.key,
                        Body=body,
                        Metadata=entry.metadata,
                        ContentType=entry.content_type,
                    )
                minted = str(
                    TypeAdapter(dict[str, object]).validate_python(response).get("VersionId", "")
                    or ""
                )
                restored.versions.append(
                    RestoredVersion(
                        bucket=bucket.bucket,
                        key=entry.key,
                        backed_up_version_id=entry.version_id,
                        restored_version_id=minted,
                        sha256=entry.sha256,
                    )
                )
                restored.objects += 1
    return restored


def bucket_versioning(client: Any, bucket: str) -> str:
    """``Enabled`` / ``Suspended`` / ``Unversioned``, or why we could not tell."""
    try:
        response = client.get_bucket_versioning(Bucket=bucket)
    except (ClientError, BotoCoreError) as exc:
        return f"unknown ({type(exc).__name__})"
    status = TypeAdapter(dict[str, object]).validate_python(response).get("Status")
    return str(status) if status else "Unversioned"


def _version_path(bucket: str, entry: ObjectRecord) -> str:
    """Where one revision's bytes live under the backup root.

    The current revision keeps the plain ``<bucket>/<key>`` path a runbook
    already names and an operator can eyeball. A superseded one goes to a flat
    sidecar tree keyed by a digest of its version id, which cannot collide with
    an object path however the keys are shaped — version ids are opaque (MinIO
    issues UUIDs, S3 URL-safe base64 that can carry a dot or a slash), so the
    real id stays in the manifest and only the name on disk is derived.
    """
    if entry.is_latest:
        return f"{bucket}/{entry.key}"
    # KEY AND version, because a version id is scoped to its key: S3's
    # historical `null` id can exist under many keys, and hashing the id alone
    # would send two unrelated revisions to the same file — the second
    # overwriting the first, surfacing later as a checksum failure or, worse,
    # a recovery that returns another object's bytes.
    identity = f"{entry.key}\x00{entry.version_id}".encode()
    return f".versions/{bucket}/{hashlib.sha256(identity).hexdigest()[:32]}"


def inventory_bucket(client: Any, bucket: str, *, out_dir: Path | None) -> BucketRecord:
    """List (and optionally copy) every RETAINED REVISION in one bucket.

    ``ListObjectVersions``, not ``ListObjectsV2`` (#501). The latter answers
    with current keys only and carries no version identity, so a backup taken
    with it loses two things at once: every superseded revision something still
    references, and the ids a restore needs to put the bytes back under. The
    database pins `storage_version_id` on signed regulatory artifacts, ICAAP
    attachments and supervisory letters; `restore_storage_backup` replays
    through `put_object`, which mints fresh ids — so those downloads 404 after
    a bucket loss even though the bytes came back.

    It is a GET, like ``ListObjectsV2``, so the no-HEAD rule above still holds.
    """
    record = BucketRecord(bucket=bucket, versioning=bucket_versioning(client, bucket))
    try:
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            payload = TypeAdapter(dict[str, object]).validate_python(page)
            for item in TypeAdapter(list[dict[str, object]]).validate_python(
                payload.get("Versions", [])
            ):
                entry = ObjectRecord(
                    key=str(item["Key"]),
                    size=int(str(item.get("Size", 0))),
                    etag=str(item.get("ETag", "")).strip('"'),
                    last_modified=str(item.get("LastModified", "")),
                    version_id=str(item.get("VersionId", "") or ""),
                    is_latest=bool(item.get("IsLatest", False)),
                )
                entry.backup_path = _version_path(bucket, entry)
                if out_dir is not None:
                    entry.sha256 = _download(
                        client, bucket, entry.key, out_dir / entry.backup_path, entry
                    )
                record.keys.append(entry)
                record.objects += 1
                record.bytes += entry.size
            for marker in TypeAdapter(list[dict[str, object]]).validate_python(
                payload.get("DeleteMarkers", [])
            ):
                record.keys.append(
                    ObjectRecord(
                        key=str(marker["Key"]),
                        size=0,
                        etag="",
                        last_modified=str(marker.get("LastModified", "")),
                        version_id=str(marker.get("VersionId", "") or ""),
                        is_latest=bool(marker.get("IsLatest", False)),
                        delete_marker=True,
                    )
                )
                record.objects += 1
    except (ClientError, BotoCoreError, ValidationError) as exc:
        # Recorded, not raised: one blocked bucket must not cost the whole run.
        record.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    return record


def run_storage_backup(
    *, out_dir: Path, download: bool, bucket_prefix: str = "aequoros-"
) -> StorageBackupReport:
    client, endpoint = _client()
    out_dir.mkdir(parents=True, exist_ok=True)
    report = StorageBackupReport(
        taken_at=utc_now_iso(),
        endpoint=endpoint,
        downloaded=download,
        format_version=STORAGE_FORMAT_VERSION,
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
        if record.ok and not record.recoverable:
            # Said out loud AND reflected in the exit code. A bucket without
            # versioning keeps one revision per key, so this backup cannot
            # contain the revision a pinned download asks for — printing OK
            # and exiting 0 would report a recovery capability that is absent.
            print(
                f"[storage]   WARNING {bucket}: versioning is {record.versioning}; "
                "a pinned revision cannot be recovered from this backup.",
                file=sys.stderr,
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--download", action="store_true", help="Copy object bytes, not just the inventory."
    )
    mode.add_argument("--restore-manifest", type=Path, default=None)
    parser.add_argument("--bucket-prefix", default="aequoros-")
    parser.add_argument("--env-file", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.env_file is not None:
        load_env_file(args.env_file)
    if args.restore_manifest is not None:
        client, _endpoint = _client()
        restored = restore_storage_backup(
            cast(_RecoveryClient, cast(object, client)), args.restore_manifest, out_dir=args.out_dir
        )
        # The mapping is the deliverable, not a log line: the database pins the
        # OLD version ids, and `reconcile_storage_versions.py` reads this file
        # to repoint them. A restore whose mapping was only printed is a
        # recovery nobody can finish.
        mapping = args.out_dir / f"storage-restore-{restored.restored_at.replace(':', '')}.json"
        restored.write(mapping)
        print(
            f"[storage] Restored {restored.objects} object(s) and "
            f"{restored.delete_markers} delete marker(s); version mapping={mapping}"
        )
        unmapped = [v for v in restored.versions if not v.restored_version_id]
        if unmapped:
            # Said loudly: without a minted id there is nothing to repoint the
            # database at, and the pinned download stays broken.
            print(
                f"[storage] WARNING {len(unmapped)} restored object(s) returned no VersionId; "
                "the target bucket is probably unversioned, and pinned downloads "
                "cannot be reconciled.",
                file=sys.stderr,
            )
            return 2
        return 0
    report = run_storage_backup(
        out_dir=args.out_dir, download=args.download, bucket_prefix=args.bucket_prefix
    )
    if not report.ok:
        return 2
    # Degraded, not failed: the bytes are copied, but a pinned revision is not
    # recoverable from an unversioned bucket, and a drill that exits 0 here
    # would certify a capability the deployment does not have (#501).
    return 0 if report.recoverable else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[storage] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
