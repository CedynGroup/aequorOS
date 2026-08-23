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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.config import get_storage_settings
from scripts.dr_common import DisasterRecoveryError, load_env_file
from scripts.dr_manifest import utc_now_iso


@dataclass
class ObjectRecord:
    key: str
    size: int
    etag: str
    last_modified: str
    sha256: str = ""


@dataclass
class BucketRecord:
    bucket: str
    objects: int = 0
    bytes: int = 0
    error: str = ""
    keys: list[ObjectRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class StorageBackupReport:
    taken_at: str
    endpoint: str
    downloaded: bool
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


def _download(client: Any, bucket: str, key: str, destination: Path) -> str:
    """Stream one object to disk and return its SHA-256 (GET, never HEAD)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    with destination.open("wb") as handle:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()


def inventory_bucket(client: Any, bucket: str, *, out_dir: Path | None) -> BucketRecord:
    """List (and optionally copy) every object in one bucket."""
    record = BucketRecord(bucket=bucket)
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                key = str(item["Key"])
                entry = ObjectRecord(
                    key=key,
                    size=int(item.get("Size", 0)),
                    etag=str(item.get("ETag", "")).strip('"'),
                    last_modified=str(item.get("LastModified", "")),
                )
                if out_dir is not None:
                    entry.sha256 = _download(client, bucket, key, out_dir / bucket / key)
                record.keys.append(entry)
                record.objects += 1
                record.bytes += entry.size
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
        print(f"[storage]   {bucket}: {record.objects} object(s), {record.bytes} bytes — {status}")

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
