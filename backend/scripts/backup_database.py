"""Take a read-only, verifiable logical backup of an AequorOS PostgreSQL database.

    cd backend
    uv run python scripts/backup_database.py --out-dir /var/backups/aequoros

Reads ``BACKUP_SOURCE_DATABASE_URL``, else ``WORKER_DATABASE_URL``, else
``DATABASE_URL``. The worker URL is the right default: its role holds
``BYPASSRLS``, and a role without it produces a silently empty archive
(:mod:`scripts.dr_common`).

Two artifacts are written per run, sharing a timestamped stem:

* ``<stem>.dump`` — a ``pg_dump`` custom-format archive (compressed, selectively
  restorable via ``pg_restore -L``);
* ``<stem>.manifest.json`` — the completeness evidence: per-table row counts and
  content digests, per-tenant counts, the Alembic revision, the archive's
  SHA-256, and the source server version.

The manifest is what makes a restore checkable rather than merely finished, so
treat the pair as one artifact: an archive restored without its manifest cannot
be verified, only hoped about.

Safety: the source session is pinned ``default_transaction_read_only``, the
credential is passed through ``PG*`` environment variables rather than argv, and
nothing in the output stream contains a password.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from scripts.dr_common import (
    DisasterRecoveryError,
    Toolchain,
    assert_client_can_dump,
    assert_dump_role_sees_all_rows,
    describe,
    discover_toolchain,
    load_env_file,
    run_tool,
    to_libpq,
    tool_env,
)
from scripts.dr_manifest import (
    BackupManifest,
    SchemaAccess,
    alembic_revision,
    collect_fingerprints,
    collect_tenant_probe,
    list_extensions,
    list_schema_access,
    utc_now_iso,
)

DEFAULT_TIMEOUT_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class BackupOptions:
    out_dir: Path
    checksum_mode: str = "counts"
    schema_only: bool = False
    tenant_probe_table: str | None = None
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    label: str | None = None
    #: Schemas to dump. Empty means the whole database, which is only allowed
    #: when the dump role can read every schema in it.
    schemas: tuple[str, ...] = ()


def resolve_source_url(explicit: str | None = None) -> str:
    for candidate in (
        explicit,
        os.getenv("BACKUP_SOURCE_DATABASE_URL"),
        os.getenv("WORKER_DATABASE_URL"),
        os.getenv("DATABASE_URL"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    msg = (
        "No source database configured. Set BACKUP_SOURCE_DATABASE_URL (or "
        "WORKER_DATABASE_URL / DATABASE_URL) or pass --source-url."
    )
    raise DisasterRecoveryError(msg)


def _role_privileges(conn: psycopg.Connection[Any]) -> tuple[str, bool, bool]:
    row = conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None:  # pragma: no cover - current_user always resolves
        msg = "Could not read the connecting role's privileges."
        raise DisasterRecoveryError(msg)
    return str(row[0]), bool(row[1]), bool(row[2])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_schemas(
    access: list[SchemaAccess], requested: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """Decide which schemas to dump, refusing a silently partial backup.

    Discovered by running this against the primary: the production database
    carries a pile of orphaned ``risk_service_migration_*`` / ``risk_service_test_*``
    schemas owned by the *application* role. The backup role cannot read them,
    and ``pg_dump`` locks every table in the database, so a whole-database dump
    dies with ``permission denied for schema ...`` partway through.

    Rather than quietly skipping them — which is how a backup silently stops
    covering something — an unscoped backup refuses and names them. Passing
    ``--schema`` is then an explicit, recorded decision, and the excluded set
    lands in the manifest.
    """
    by_name = {entry.name: entry for entry in access}
    if requested:
        missing = [name for name in requested if name not in by_name]
        if missing:
            msg = f"Requested schema(s) do not exist on the source: {', '.join(missing)}"
            raise DisasterRecoveryError(msg)
        unreadable = [name for name in requested if not by_name[name].readable]
        if unreadable:
            msg = (
                f"The backup role cannot read requested schema(s): {', '.join(unreadable)}. "
                "Grant USAGE or drop them."
            )
            raise DisasterRecoveryError(msg)
        excluded = [name for name in sorted(by_name) if name not in set(requested)]
        return list(requested), excluded

    blocked = [entry for entry in access if not entry.readable]
    if blocked:
        listing = ", ".join(f"{e.name} ({e.tables} tables)" for e in blocked[:6])
        more = f" and {len(blocked) - 6} more" if len(blocked) > 6 else ""
        msg = (
            f"The backup role cannot read {len(blocked)} schema(s): {listing}{more}. "
            "pg_dump locks every table in the database, so a whole-database backup "
            "would abort partway through. Either grant USAGE / drop the leftovers, "
            "or scope the backup explicitly, e.g. --schema public."
        )
        raise DisasterRecoveryError(msg)
    return [], []


def _dump_argv(
    tools: Toolchain, archive: Path, *, schema_only: bool, schemas: list[str]
) -> list[str]:
    argv = [
        tools.pg_dump,
        "--format=custom",
        "--compress=6",
        # Ownership and grants are cluster-local; a restore target has different
        # roles. Roles/grants are a separate globals concern, recorded as such.
        "--no-owner",
        "--no-acl",
        "--verbose",
        f"--file={archive}",
    ]
    argv.extend(f"--schema={name}" for name in schemas)
    if schema_only:
        argv.append("--schema-only")
    return argv


def run_backup(source_url: str, options: BackupOptions) -> BackupManifest:
    """Fingerprint the source, dump it, and write the paired manifest."""
    tools = discover_toolchain()
    options.out_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(to_libpq(source_url), connect_timeout=30) as conn:
        conn.execute("SET default_transaction_read_only = on")
        server_version = str(conn.execute("SHOW server_version").fetchone()[0])  # type: ignore[index]
        assert_client_can_dump(tools.major, server_version)
        role, superuser, bypassrls = _role_privileges(conn)
        assert_dump_role_sees_all_rows(role=role, superuser=superuser, bypassrls=bypassrls)

        revision = alembic_revision(conn)
        extensions = list_extensions(conn)
        schemas, excluded = resolve_schemas(list_schema_access(conn), options.schemas)
        print(f"[backup] source={describe(source_url)} server={server_version} role={role}")
        print(f"[backup] alembic={revision or '<none>'} extensions={','.join(extensions)}")
        print(f"[backup] schemas={','.join(schemas) or '<all>'} excluded={len(excluded)}")
        print(f"[backup] fingerprinting ({options.checksum_mode})...", flush=True)
        started = time.monotonic()
        tables = (
            []
            if options.schema_only
            else collect_fingerprints(conn, checksum_mode=options.checksum_mode)
        )
        probe = None if options.schema_only else collect_tenant_probe(
            conn, table=options.tenant_probe_table
        )
        print(
            f"[backup] fingerprinted {len(tables)} tables "
            f"({sum(t.rows for t in tables)} rows) in {time.monotonic() - started:.1f}s"
        )

    stem = options.label or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive = options.out_dir / f"aequoros-{stem}.dump"
    manifest_path = options.out_dir / f"aequoros-{stem}.manifest.json"

    print(f"[backup] dumping to {archive} ...", flush=True)
    dump_started = time.monotonic()
    result = run_tool(
        _dump_argv(tools, archive, schema_only=options.schema_only, schemas=schemas),
        env=tool_env(source_url, read_only=True),
        timeout=options.timeout,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        msg = "pg_dump failed:\n" + "\n".join(tail)
        raise DisasterRecoveryError(msg)
    dump_seconds = time.monotonic() - dump_started

    notes = [f"pg_dump wall-clock seconds: {dump_seconds:.1f}"]
    if options.schema_only:
        notes.append("schema-only backup: contains no table data and cannot satisfy an RPO.")
    notes.append("Ownership and grants excluded (--no-owner --no-acl); restore roles separately.")
    if excluded:
        notes.append(
            f"Scoped backup: {len(excluded)} schema(s) excluded by --schema — "
            + ", ".join(excluded)
        )
    notes.append("Fingerprints cover the public schema, where every application table lives.")

    manifest = BackupManifest(
        taken_at=utc_now_iso(),
        source=describe(source_url),
        server_version=server_version,
        client_major=tools.major,
        alembic_revision=revision,
        checksum_mode="none" if options.schema_only else options.checksum_mode,
        archive_name=archive.name,
        archive_bytes=archive.stat().st_size,
        archive_sha256=sha256_file(archive),
        extensions=extensions,
        tables=tables,
        tenant_probe=probe,
        notes=notes,
        schemas=schemas,
        excluded_schemas=excluded,
    )
    manifest.write(manifest_path)
    print(
        f"[backup] OK archive={archive.name} bytes={manifest.archive_bytes} "
        f"sha256={manifest.archive_sha256[:16]}... dump_seconds={dump_seconds:.1f}"
    )
    print(f"[backup] manifest={manifest_path}")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--source-url", default=None, help="Overrides the env-resolved source.")
    parser.add_argument("--checksum-mode", choices=("counts", "full"), default="counts")
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--tenant-probe-table", default=None)
    parser.add_argument(
        "--schema",
        dest="schemas",
        action="append",
        default=[],
        help="Restrict the dump to this schema (repeatable). Default: the whole database.",
    )
    parser.add_argument("--label", default=None, help="Artifact stem; defaults to a UTC stamp.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
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
    options = BackupOptions(
        out_dir=args.out_dir,
        checksum_mode=args.checksum_mode,
        schema_only=args.schema_only,
        tenant_probe_table=args.tenant_probe_table,
        timeout=args.timeout,
        label=args.label,
        schemas=tuple(args.schemas),
    )
    run_backup(resolve_source_url(args.source_url), options)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[backup] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
