"""Prove a restored AequorOS database matches the backup it came from.

    cd backend
    RESTORE_DATABASE_URL=postgresql://user:pw@host:5432/aequoros_restore \
      uv run python scripts/verify_restore.py --manifest /var/backups/aequoros/x.manifest.json

Four independent checks, because each catches a failure the others cannot:

* **Alembic revision** — the restored schema is the one the application expects.
* **Row counts** — nothing was lost. This is the check that catches a backup
  taken through RLS, a truncated transfer, or a partially-applied restore.
* **Content digests** — nothing was altered. A row count cannot see a changed
  value; the digest can (only when the backup was taken with
  ``--checksum-mode full``, otherwise this is reported as not performed).
* **Tenant isolation** — the RLS policies came back with the data. This is the
  one check specific to this product: every tenant table is
  ``FORCE ROW LEVEL SECURITY``, and a restore that returned the rows but
  dropped the policies would look perfect to the three checks above while
  exposing every bank's data to every other bank. It is verified by connecting
  as a purpose-made role with neither ``SUPERUSER`` nor ``BYPASSRLS``, setting
  the tenant GUC to each organization in turn, and confirming the visible row
  count matches that organization's count at backup time — and that an
  unknown organization sees nothing.

A check that could not be run is reported as ``UNVERIFIED``, never as a pass.
The exit code is 0 only when every check actually ran and actually passed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql

from scripts.dr_common import (
    TENANT_GUC,
    DisasterRecoveryError,
    describe,
    load_env_file,
    to_libpq,
)
from scripts.dr_manifest import (
    BackupManifest,
    Difference,
    TenantProbe,
    alembic_revision,
    collect_fingerprints,
    compare,
)

_PROBE_ROLE_PREFIX = "aeq_restore_probe_"
_ABSENT_ORG = "00000000-0000-4000-8000-000000000000"


@dataclass
class Check:
    name: str
    status: str  # "pass" | "fail" | "unverified"
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass
class VerificationReport:
    target: str
    checks: list[Check] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def render(self) -> str:
        lines = [f"Restore verification for {self.target}"]
        for check in self.checks:
            lines.append(f"  [{check.status.upper():<10}] {check.name}: {check.detail}")
        for diff in self.differences[:20]:
            lines.append(
                f"    - {diff.table}: {diff.kind} expected={diff.expected} actual={diff.actual}"
            )
        if len(self.differences) > 20:
            lines.append(f"    ... and {len(self.differences) - 20} more difference(s)")
        lines.append(f"RESULT: {'PASS' if self.ok else 'FAIL'}")
        return "\n".join(lines)


def _check_revision(conn: psycopg.Connection[Any], manifest: BackupManifest) -> Check:
    actual = alembic_revision(conn)
    if not manifest.alembic_revision:
        return Check("alembic_revision", "unverified", "Source recorded no Alembic revision.")
    if actual == manifest.alembic_revision:
        return Check("alembic_revision", "pass", f"{actual} matches the backup.")
    return Check(
        "alembic_revision",
        "fail",
        f"restored {actual or '<none>'} but the backup was taken at {manifest.alembic_revision}.",
    )


def _check_tables(
    conn: psycopg.Connection[Any], manifest: BackupManifest
) -> tuple[Check, Check, list[Difference]]:
    if manifest.checksum_mode == "none":
        unverified = Check("row_counts", "unverified", "Schema-only backup carries no row counts.")
        return unverified, Check("content_digests", "unverified", "Schema-only backup."), []
    actual = collect_fingerprints(conn, checksum_mode=manifest.checksum_mode)
    diffs = compare(manifest.tables, actual)
    count_diffs = [d for d in diffs if d.kind in {"row_count", "missing_table", "unexpected_table"}]
    digest_diffs = [d for d in diffs if d.kind == "content_digest"]

    rows = manifest.total_rows
    counts = (
        Check("row_counts", "pass", f"{len(manifest.tables)} tables, {rows} rows, all matching.")
        if not count_diffs
        else Check("row_counts", "fail", f"{len(count_diffs)} table(s) differ.")
    )
    if manifest.checksum_mode != "full":
        digests = Check(
            "content_digests",
            "unverified",
            "Backup was taken with --checksum-mode counts; no digests to compare.",
        )
    elif digest_diffs:
        digests = Check("content_digests", "fail", f"{len(digest_diffs)} table(s) differ.")
    else:
        digests = Check("content_digests", "pass", f"{len(manifest.tables)} table digests match.")
    return counts, digests, diffs


def _grant_probe_role(conn: psycopg.Connection[Any], role: str, password: str, table: str) -> None:
    """Create a login role that RLS actually applies to, and let it read the probe."""
    role_ident = sql.Identifier(role)
    conn.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
            role_ident, sql.Literal(password)
        )
    )
    row = conn.execute("SELECT current_database()").fetchone()
    database = str(row[0]) if row else ""
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), role_ident)
    )
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_ident))
    conn.execute(
        sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier("public", table), role_ident)
    )


def _drop_probe_role(conn: psycopg.Connection[Any], role: str) -> None:
    """Remove the probe role and everything that depends on it.

    A bare ``DROP ROLE`` fails with ``DependentObjectsStillExist`` while the
    grants issued above still reference it, which would leave a login role
    behind on the restore target after every drill.
    """
    role_ident = sql.Identifier(role)
    row = conn.execute("SELECT current_database()").fetchone()
    database = str(row[0]) if row else ""
    conn.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(database), role_ident)
    )
    conn.execute(sql.SQL("DROP OWNED BY {}").format(role_ident))
    conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(role_ident))


def _probe_url(target_url: str, role: str, password: str) -> str:
    parts = urlsplit(to_libpq(target_url))
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _visible_counts(url: str, probe: TenantProbe, orgs: list[str]) -> dict[str, int]:
    """Rows each organization can see, read as a role RLS is not bypassed for."""
    counter = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", probe.table))
    seen: dict[str, int] = {}
    with psycopg.connect(url, connect_timeout=30) as conn:
        for org in orgs:
            # set_config, not SET: the GUC value is data and must be parameterised.
            conn.execute("SELECT set_config(%s, %s, false)", (TENANT_GUC, org))
            row = conn.execute(counter).fetchone()
            seen[org] = int(row[0]) if row else 0
    return seen


def _check_tenant_isolation(target_url: str, manifest: BackupManifest) -> Check:
    probe = manifest.tenant_probe
    if probe is None or not probe.counts:
        return Check("tenant_isolation", "unverified", "Backup recorded no tenant probe.")
    role = _PROBE_ROLE_PREFIX + secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    try:
        with psycopg.connect(to_libpq(target_url), connect_timeout=30, autocommit=True) as conn:
            try:
                _grant_probe_role(conn, role, password, probe.table)
            except psycopg.Error as exc:
                return Check(
                    "tenant_isolation",
                    "unverified",
                    f"Could not create an unprivileged probe role ({exc.__class__.__name__}); "
                    "rerun as a role holding CREATEROLE on the restore target.",
                )
            try:
                orgs = [*sorted(probe.counts), _ABSENT_ORG]
                seen = _visible_counts(_probe_url(target_url, role, password), probe, orgs)
            finally:
                _drop_probe_role(conn, role)
    except psycopg.Error as exc:
        return Check("tenant_isolation", "unverified", f"probe failed: {exc.__class__.__name__}")

    mismatches = [
        f"{org}: expected {want}, saw {seen.get(org)}"
        for org, want in probe.counts.items()
        if seen.get(org) != want
    ]
    if seen.get(_ABSENT_ORG, 0) != 0:
        mismatches.append(
            f"unknown organization saw {seen.get(_ABSENT_ORG)} row(s) — RLS not active"
        )
    if mismatches:
        return Check("tenant_isolation", "fail", "; ".join(mismatches[:5]))
    return Check(
        "tenant_isolation",
        "pass",
        f"{len(probe.counts)} organization(s) on {probe.table} isolated correctly; "
        "an unknown organization sees 0 rows.",
    )


def verify(target_url: str, manifest: BackupManifest) -> VerificationReport:
    report = VerificationReport(target=describe(target_url))
    with psycopg.connect(to_libpq(target_url), connect_timeout=30) as conn:
        conn.execute("SET default_transaction_read_only = on")
        report.checks.append(_check_revision(conn, manifest))
        counts, digests, diffs = _check_tables(conn, manifest)
        report.checks.extend([counts, digests])
        report.differences = diffs
    report.checks.append(_check_tenant_isolation(target_url, manifest))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--target-url", default=None)
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
    target = args.target_url or os.getenv("RESTORE_DATABASE_URL", "").strip()
    if not target:
        msg = "No restore target configured. Set RESTORE_DATABASE_URL or pass --target-url."
        raise DisasterRecoveryError(msg)
    report = verify(target, BackupManifest.read(args.manifest))
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DisasterRecoveryError as exc:
        print(f"[verify] FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
