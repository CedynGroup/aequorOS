"""Restore an AequorOS backup archive into a dedicated, non-production target.

    cd backend
    RESTORE_DATABASE_URL=postgresql://user:pw@host:5432/aequoros_restore \
      uv run python scripts/restore_database.py --archive /var/backups/aequoros/x.dump

The target is refused if it addresses the same host/port/database as
``DATABASE_URL``, ``WORKER_DATABASE_URL`` or ``OPERATOR_DATABASE_URL`` — compared
structurally, so a different username or driver prefix pointing at the primary
is still refused. ``--recreate`` drops and recreates the target database, which
is why that refusal runs first and unconditionally.

Extensions are reconciled rather than assumed. The primary carries
``timescaledb``, which most restore targets will not have; a plain ``pg_restore``
then fails on ``CREATE EXTENSION`` and on everything the archive orders after
it. Unavailable extensions are filtered out of the archive's table of contents
via ``pg_restore -L`` and reported explicitly, so the omission is a recorded
fact rather than a silent difference between the primary and its recovery.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from scripts.dr_common import (
    DisasterRecoveryError,
    Toolchain,
    describe,
    discover_toolchain,
    load_env_file,
    protected_urls_from_env,
    refuse_protected_target,
    run_tool,
    to_libpq,
    tool_env,
)

DEFAULT_TIMEOUT_SECONDS = 4 * 60 * 60
_EXTENSION_LINE = re.compile(r"\bEXTENSION\s+-\s+(\S+)")
_EXTENSION_COMMENT = re.compile(r"\bCOMMENT\s+-\s+EXTENSION\s+(\S+)")
_SCHEMA_LINE = re.compile(r"\bSCHEMA\s+-\s+(\S+)")
_SCHEMA_COMMENT = re.compile(r"\bCOMMENT\s+-\s+SCHEMA\s+(\S+)")
_IGNORED_ERRORS = re.compile(r"errors ignored on restore:\s*(\d+)", re.IGNORECASE)


@dataclass
class RestoreReport:
    target: str
    archive: str
    seconds: float
    skipped_extensions: list[str] = field(default_factory=list)
    skipped_schemas: list[str] = field(default_factory=list)
    ignored_errors: int = 0
    stderr_tail: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.ignored_errors == 0


def resolve_target_url(explicit: str | None = None) -> str:
    for candidate in (explicit, os.getenv("RESTORE_DATABASE_URL")):
        if candidate and candidate.strip():
            return candidate.strip()
    msg = "No restore target configured. Set RESTORE_DATABASE_URL or pass --target-url."
    raise DisasterRecoveryError(msg)


def maintenance_url(target_url: str, *, database: str = "postgres") -> str:
    """The same cluster and credentials, pointed at a maintenance database."""
    parts = urlsplit(to_libpq(target_url))
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))


def target_database_name(target_url: str) -> str:
    return (urlsplit(to_libpq(target_url)).path or "/").lstrip("/")


def recreate_target_database(target_url: str, *, maintenance_db: str = "postgres") -> None:
    """Drop and recreate the target database. Only ever a non-production target."""
    name = target_database_name(target_url)
    if not name:
        msg = "Restore target URL has no database name."
        raise DisasterRecoveryError(msg)
    with psycopg.connect(
        maintenance_url(target_url, database=maintenance_db), connect_timeout=30, autocommit=True
    ) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def available_extensions(target_url: str) -> set[str]:
    with psycopg.connect(to_libpq(target_url), connect_timeout=30) as conn:
        rows = conn.execute("SELECT name FROM pg_available_extensions").fetchall()
    return {str(r[0]) for r in rows}


def existing_schemas(target_url: str) -> set[str]:
    with psycopg.connect(to_libpq(target_url), connect_timeout=30) as conn:
        rows = conn.execute("SELECT nspname FROM pg_namespace").fetchall()
    return {str(r[0]) for r in rows}


def read_toc(tools: Toolchain, archive: Path, *, timeout: int) -> str:
    """The archive's table of contents, as ``pg_restore --list`` renders it."""
    result = run_tool(
        [tools.pg_restore, "--list", str(archive)], env=dict(os.environ), timeout=timeout
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        msg = "pg_restore --list failed:\n" + "\n".join(tail)
        raise DisasterRecoveryError(msg)
    return result.stdout


def _names_in_toc(toc: str, pattern: re.Pattern[str]) -> list[str]:
    return sorted({m.group(1) for line in toc.splitlines() if (m := pattern.search(line))})


def archive_extensions(toc: str) -> list[str]:
    """Extension names the archive would create."""
    return _names_in_toc(toc, _EXTENSION_LINE)


def archive_schemas(toc: str) -> list[str]:
    """Schema names the archive would create."""
    return _names_in_toc(toc, _SCHEMA_LINE)


def filter_toc(
    toc: str, *, drop_extensions: set[str] | None = None, drop_schemas: set[str] | None = None
) -> str:
    """Remove entries the target cannot or need not replay.

    Two cases, both of which otherwise produce errors on a perfectly good
    restore. Extensions the target does not have (``timescaledb`` on the
    primary) fail at ``CREATE EXTENSION`` and take everything ordered after them
    with it. Schemas the target already has — ``public`` exists in every freshly
    created database — fail at ``CREATE SCHEMA``. The second is harmless, but
    leaving it in means every clean restore reports one ignored error, which
    teaches an operator to ignore the error count, which is the number that
    matters.
    """
    extensions = drop_extensions or set()
    schemas = drop_schemas or set()
    if not extensions and not schemas:
        return toc
    kept: list[str] = []
    for line in toc.splitlines():
        extension = _EXTENSION_LINE.search(line) or _EXTENSION_COMMENT.search(line)
        if extension and extension.group(1) in extensions:
            continue
        schema = _SCHEMA_LINE.search(line) or _SCHEMA_COMMENT.search(line)
        if schema and schema.group(1) in schemas:
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


@dataclass(frozen=True)
class RestoreOptions:
    archive: Path
    recreate: bool = False
    jobs: int = 1
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    maintenance_db: str = "postgres"
    work_dir: Path | None = None


def _restore_argv(
    tools: Toolchain, options: RestoreOptions, toc_path: Path | None, database: str
) -> list[str]:
    argv = [
        tools.pg_restore,
        "--no-owner",
        "--no-acl",
        "--verbose",
        # A single transaction makes a failed restore leave nothing behind, which
        # is what a recovery target wants. Parallel restore trades that for speed
        # and is the right choice when RTO matters more than atomicity.
        f"--jobs={options.jobs}" if options.jobs > 1 else "--single-transaction",
    ]
    if toc_path is not None:
        argv.append(f"--use-list={toc_path}")
    # Host/port/user/password arrive through PG* env; only the database is named
    # here, because pg_restore requires an explicit -d to restore into a database.
    argv.extend(["--dbname", database])
    argv.append(str(options.archive))
    return argv


def run_restore(target_url: str, options: RestoreOptions) -> RestoreReport:
    """Restore ``options.archive`` into ``target_url`` after refusing production."""
    refuse_protected_target(target_url, protected_urls_from_env())
    if not options.archive.is_file():
        msg = f"Archive not found: {options.archive}"
        raise DisasterRecoveryError(msg)
    tools = discover_toolchain()

    if options.recreate:
        print(f"[restore] recreating database {describe(target_url)}")
        recreate_target_database(target_url, maintenance_db=options.maintenance_db)

    toc = read_toc(tools, options.archive, timeout=options.timeout)
    missing = sorted(set(archive_extensions(toc)) - available_extensions(target_url))
    present = sorted(set(archive_schemas(toc)) & existing_schemas(target_url))
    toc_path: Path | None = None
    if missing or present:
        work_dir = options.work_dir or options.archive.parent
        toc_path = work_dir / f"{options.archive.stem}.toc"
        toc_path.write_text(
            filter_toc(toc, drop_extensions=set(missing), drop_schemas=set(present)),
            encoding="utf-8",
        )
        if missing:
            print(f"[restore] target lacks extensions {','.join(missing)}; filtered from the TOC")
        if present:
            joined = ",".join(present)
            print(f"[restore] target already has schemas {joined}; filtered from the TOC")

    env = tool_env(target_url)
    print(f"[restore] restoring {options.archive.name} -> {describe(target_url)}", flush=True)
    started = time.monotonic()
    result = run_tool(
        _restore_argv(tools, options, toc_path, target_database_name(target_url)),
        env=env,
        timeout=options.timeout,
    )
    seconds = time.monotonic() - started
    stderr = result.stderr or ""
    ignored = int(m.group(1)) if (m := _IGNORED_ERRORS.search(stderr)) else 0
    if result.returncode != 0 and ignored == 0:
        tail = stderr.strip().splitlines()[-10:]
        msg = "pg_restore failed:\n" + "\n".join(tail)
        raise DisasterRecoveryError(msg)

    report = RestoreReport(
        target=describe(target_url),
        archive=options.archive.name,
        seconds=seconds,
        skipped_extensions=missing,
        skipped_schemas=present,
        ignored_errors=ignored,
        stderr_tail=[ln for ln in stderr.strip().splitlines() if "error" in ln.lower()][-10:],
    )
    status = "OK" if report.clean else f"COMPLETED WITH {ignored} IGNORED ERROR(S)"
    print(f"[restore] {status} in {seconds:.1f}s")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--target-url", default=None)
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the target DB.")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--maintenance-db", default="postgres")
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
    options = RestoreOptions(
        archive=args.archive,
        recreate=args.recreate,
        jobs=args.jobs,
        timeout=args.timeout,
        maintenance_db=args.maintenance_db,
    )
    report = run_restore(resolve_target_url(args.target_url), options)
    return 0 if report.clean else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[restore] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
