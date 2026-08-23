"""Execute a full restore drill: back up, provision, restore, verify, tear down.

    cd backend
    DRILL_CLUSTER_URL=postgresql://user:pw@drill-host:5432/postgres \
      uv run python scripts/restore_drill.py --out-dir /var/backups/aequoros

This is the executable form of the claim "we can recover". It refuses to run on
the production cluster at all — not merely on the production *database* — because
a drill creates and drops databases, and doing that on the primary's cluster is
the failure mode the drill exists to avoid.

The drill provisions a uniquely-named throwaway database, restores the archive
into it, runs the full verification (revision, row counts, digests, tenant
isolation), and drops it again. ``--keep`` leaves the target in place for
inspection after a failure.

What it measures, and what it does not:

* **RTO** is measured directly — restore wall-clock plus verification wall-clock,
  reported per phase. It is a floor, not a guarantee: it excludes provisioning
  the replacement host, DNS/cutover, and application restart.
* **RPO** is *not* measured by this drill and is never inferred from it. A
  logical dump bounds data loss at the interval between dumps plus the dump's
  own duration; the drill reports the dump duration so that arithmetic can be
  done honestly, but the interval is a scheduling decision made outside this
  repository.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from scripts.backup_database import BackupOptions, resolve_source_url, run_backup
from scripts.dr_common import (
    DisasterRecoveryError,
    describe,
    load_env_file,
    protected_urls_from_env,
    refuse_protected_cluster,
    to_libpq,
)
from scripts.dr_manifest import BackupManifest
from scripts.restore_database import RestoreOptions, run_restore
from scripts.verify_restore import verify

_DRILL_DB_PREFIX = "aequoros_drill_"


@dataclass
class DrillTimings:
    backup_seconds: float = 0.0
    restore_seconds: float = 0.0
    verify_seconds: float = 0.0

    @property
    def recovery_seconds(self) -> float:
        """Measured RTO floor: restore plus verification. Excludes cutover."""
        return self.restore_seconds + self.verify_seconds


def resolve_drill_cluster_url(explicit: str | None = None) -> str:
    for candidate in (explicit, os.getenv("DRILL_CLUSTER_URL")):
        if candidate and candidate.strip():
            return candidate.strip()
    msg = (
        "No drill cluster configured. Set DRILL_CLUSTER_URL (or --drill-cluster-url) to a "
        "maintenance database on a NON-production PostgreSQL cluster where the drill may "
        "create and drop databases."
    )
    raise DisasterRecoveryError(msg)


def drill_target_url(cluster_url: str, database: str) -> str:
    parts = urlsplit(to_libpq(cluster_url))
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))


def create_drill_database(cluster_url: str, database: str) -> None:
    with psycopg.connect(to_libpq(cluster_url), connect_timeout=30, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


def drop_drill_database(cluster_url: str, database: str) -> None:
    with psycopg.connect(to_libpq(cluster_url), connect_timeout=30, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


@dataclass(frozen=True)
class DrillOptions:
    out_dir: Path
    archive: Path | None = None
    manifest: Path | None = None
    checksum_mode: str = "counts"
    jobs: int = 1
    keep: bool = False
    timeout: int = 4 * 60 * 60
    schemas: tuple[str, ...] = ()


def _obtain_backup(options: DrillOptions, timings: DrillTimings) -> tuple[Path, BackupManifest]:
    """Either reuse a supplied archive/manifest pair, or take a fresh backup."""
    if options.archive is not None:
        if options.manifest is None:
            msg = "--archive requires --manifest: an archive without its manifest is unverifiable."
            raise DisasterRecoveryError(msg)
        return options.archive, BackupManifest.read(options.manifest)
    started = time.monotonic()
    manifest = run_backup(
        resolve_source_url(None),
        BackupOptions(
            out_dir=options.out_dir,
            checksum_mode=options.checksum_mode,
            timeout=options.timeout,
            schemas=options.schemas,
        ),
    )
    timings.backup_seconds = time.monotonic() - started
    return options.out_dir / manifest.archive_name, manifest


def run_drill(cluster_url: str, options: DrillOptions) -> tuple[bool, DrillTimings]:
    refuse_protected_cluster(cluster_url, protected_urls_from_env())
    timings = DrillTimings()
    archive, manifest = _obtain_backup(options, timings)

    database = _DRILL_DB_PREFIX + secrets.token_hex(6)
    target_url = drill_target_url(cluster_url, database)
    print(f"[drill] provisioning throwaway target {describe(target_url)}")
    create_drill_database(cluster_url, database)
    try:
        restore_report = run_restore(
            target_url,
            RestoreOptions(
                archive=archive,
                jobs=options.jobs,
                timeout=options.timeout,
                work_dir=options.out_dir,
            ),
        )
        timings.restore_seconds = restore_report.seconds

        started = time.monotonic()
        report = verify(target_url, manifest)
        timings.verify_seconds = time.monotonic() - started
        print(report.render())
        if restore_report.skipped_extensions:
            print(
                "[drill] NOTE extensions absent on the drill cluster and skipped: "
                + ", ".join(restore_report.skipped_extensions)
            )
        if restore_report.ignored_errors:
            print(f"[drill] NOTE pg_restore ignored {restore_report.ignored_errors} error(s):")
            for line in restore_report.stderr_tail:
                print(f"[drill]      {line}")
        ok = report.ok and restore_report.clean
    finally:
        if options.keep:
            print(f"[drill] --keep: leaving {database} in place for inspection")
        else:
            drop_drill_database(cluster_url, database)
            print(f"[drill] tore down {database}")

    print(
        f"[drill] TIMINGS backup={timings.backup_seconds:.1f}s "
        f"restore={timings.restore_seconds:.1f}s verify={timings.verify_seconds:.1f}s "
        f"measured_rto_floor={timings.recovery_seconds:.1f}s"
    )
    print(f"[drill] RESULT: {'PASS' if ok else 'FAIL'}")
    return ok, timings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--drill-cluster-url", default=None)
    parser.add_argument("--archive", type=Path, default=None, help="Reuse an existing archive.")
    parser.add_argument("--manifest", type=Path, default=None, help="Required with --archive.")
    parser.add_argument("--checksum-mode", choices=("counts", "full"), default="counts")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--schema",
        dest="schemas",
        action="append",
        default=[],
        help="Restrict the backup to this schema (repeatable).",
    )
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--timeout", type=int, default=4 * 60 * 60)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Load backend/.env (KEY = value form) before resolving URLs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.env_file is not None:
        load_env_file(args.env_file)
    options = DrillOptions(
        out_dir=args.out_dir,
        archive=args.archive,
        manifest=args.manifest,
        checksum_mode=args.checksum_mode,
        jobs=args.jobs,
        keep=args.keep,
        timeout=args.timeout,
        schemas=tuple(args.schemas),
    )
    ok, _ = run_drill(resolve_drill_cluster_url(args.drill_cluster_url), options)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[drill] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
