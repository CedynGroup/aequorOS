"""The evidence record a backup carries with it, and how it is computed.

A `pg_dump` archive on its own proves nothing about completeness: it exits 0
whether it captured twelve million rows or none (see
:mod:`scripts.dr_common`). The manifest is the independent record that makes a
restore *checkable* — captured from the source at backup time, compared against
the restored target afterwards:

* **row counts** per table — catches partial restores and RLS-truncated dumps;
* **content digests** per table — catches silently altered values that a row
  count cannot see. The digest is an additive sum over per-row MD5s, so it is
  order-independent: a restore legitimately reorders heap tuples, and a digest
  that depended on physical order would produce false alarms on every run;
* the **Alembic revision** — catches restoring an archive whose schema does not
  match the application that will be pointed at it;
* **per-tenant row counts** on a FORCE-RLS probe table — the input to the
  post-restore isolation check, which proves the tenant policies themselves
  survived the round trip rather than only the rows.

Digests are computed from ``row::text``, which depends on session settings that
differ between servers — see :data:`TEXT_RENDERING_GUCS`, pinned on both sides
before any digest is taken. They remain sensitive to logical column order, which
a logical dump/restore preserves. :func:`compare` still reports count and digest
mismatches as distinct kinds, because a count mismatch is missing data and a
digest-only mismatch is altered data, and the two have different remediations.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

MANIFEST_SCHEMA_VERSION = 1
CHECKSUM_MODES = ("counts", "full")


@dataclass(frozen=True)
class TableFingerprint:
    table: str
    rows: int
    digest: str


@dataclass(frozen=True)
class TenantProbe:
    """Per-organization row counts on one FORCE-RLS table."""

    table: str
    counts: dict[str, int]


@dataclass
class BackupManifest:
    taken_at: str
    source: str
    server_version: str
    client_major: int
    alembic_revision: str
    checksum_mode: str
    archive_name: str
    archive_bytes: int
    archive_sha256: str
    extensions: list[str] = field(default_factory=list)
    tables: list[TableFingerprint] = field(default_factory=list)
    tenant_probe: TenantProbe | None = None
    notes: list[str] = field(default_factory=list)
    #: Schemas the archive contains. Empty means the whole database was dumped.
    schemas: list[str] = field(default_factory=list)
    #: Schemas deliberately excluded, and why — so a partial backup can never be
    #: mistaken for a complete one when it is read back months later.
    excluded_schemas: list[str] = field(default_factory=list)
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def write(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)

    @classmethod
    def read(cls, path: Path) -> BackupManifest:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        probe_raw = raw.get("tenant_probe")
        return cls(
            taken_at=raw["taken_at"],
            source=raw["source"],
            server_version=raw["server_version"],
            client_major=raw["client_major"],
            alembic_revision=raw["alembic_revision"],
            checksum_mode=raw["checksum_mode"],
            archive_name=raw["archive_name"],
            archive_bytes=raw["archive_bytes"],
            archive_sha256=raw["archive_sha256"],
            extensions=list(raw.get("extensions", [])),
            tables=[TableFingerprint(**t) for t in raw.get("tables", [])],
            tenant_probe=TenantProbe(**probe_raw) if probe_raw else None,
            notes=list(raw.get("notes", [])),
            schemas=list(raw.get("schemas", [])),
            excluded_schemas=list(raw.get("excluded_schemas", [])),
            schema_version=raw.get("schema_version", MANIFEST_SCHEMA_VERSION),
        )


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


#: Session settings that change how a row renders as text, pinned identically on
#: the source and the restore target before any digest is computed.
#:
#: Learned by running the drill against the primary: it reported 72 of 134 tables
#: as differing while every row count matched exactly. The cause was ``TimeZone``
#: — the primary runs UTC, the drill cluster inherited ``America/New_York`` from
#: the host — so every ``timestamptz`` rendered differently and the digest of any
#: table with a ``created_at`` diverged. The data was identical. Without this
#: pinning the digest check is not just noisy, it is actively misleading: it
#: cries corruption on a perfect restore.
TEXT_RENDERING_GUCS: tuple[tuple[str, str], ...] = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("IntervalStyle", "iso_8601"),
    ("extra_float_digits", "3"),
    ("bytea_output", "hex"),
)


def pin_text_rendering(conn: psycopg.Connection[Any]) -> None:
    """Make ``row::text`` deterministic across servers. See :data:`TEXT_RENDERING_GUCS`."""
    for name, value in TEXT_RENDERING_GUCS:
        conn.execute(sql.SQL("SET {} = {}").format(sql.Identifier(name), sql.Literal(value)))


def list_user_tables(conn: psycopg.Connection[Any]) -> list[str]:
    """Ordinary tables in ``public``, alphabetically — the fingerprint domain."""
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relpersistence = 'p' "
        "ORDER BY c.relname"
    ).fetchall()
    return [r[0] for r in rows]


@dataclass(frozen=True)
class SchemaAccess:
    """A non-system schema and whether the connecting role may read it."""

    name: str
    readable: bool
    tables: int


def list_schema_access(conn: psycopg.Connection[Any]) -> list[SchemaAccess]:
    """Every non-system schema, with the dump role's USAGE privilege on it.

    ``pg_dump`` takes an ACCESS SHARE lock on every table in the database, so a
    single unreadable schema aborts the entire backup with ``permission denied``
    — after the dump has appeared to start. Enumerating this up front turns that
    into a precise, actionable refusal instead of a late failure.
    """
    rows = conn.execute(
        "SELECT n.nspname, "
        "has_schema_privilege(current_user, n.nspname, 'USAGE'), "
        "(SELECT count(*) FROM pg_class k WHERE k.relnamespace = n.oid AND k.relkind = 'r') "
        "FROM pg_namespace n "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
        "AND n.nspname NOT LIKE 'pg\\_temp%' AND n.nspname NOT LIKE 'pg\\_toast\\_temp%' "
        "ORDER BY n.nspname"
    ).fetchall()
    return [SchemaAccess(name=str(r[0]), readable=bool(r[1]), tables=int(r[2])) for r in rows]


def list_extensions(conn: psycopg.Connection[Any]) -> list[str]:
    rows = conn.execute("SELECT extname FROM pg_extension ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def alembic_revision(conn: psycopg.Connection[Any]) -> str:
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.Error:
        conn.rollback()
        return ""
    return str(row[0]) if row else ""


def _fingerprint_query(table: str, *, checksum_mode: str) -> sql.Composed:
    ident = sql.Identifier("public", table)
    if checksum_mode == "full":
        # Additive (therefore order-independent) checksum. Cast to numeric so a
        # wide table cannot overflow bigint and abort the whole backup.
        return sql.SQL(
            "SELECT count(*)::bigint, coalesce(sum(((('x' || substr(md5(t.*::text), 1, 8))"
            ")::bit(32)::bigint)::numeric), 0)::text FROM {} AS t"
        ).format(ident)
    return sql.SQL("SELECT count(*)::bigint, '' FROM {} AS t").format(ident)


def collect_fingerprints(
    conn: psycopg.Connection[Any], *, checksum_mode: str = "counts"
) -> list[TableFingerprint]:
    """Row count (and optionally content digest) for every public table."""
    if checksum_mode not in CHECKSUM_MODES:
        msg = f"checksum_mode must be one of {CHECKSUM_MODES}, got {checksum_mode!r}"
        raise ValueError(msg)
    # Both the backup and the verification call this, so pinning here guarantees
    # the two sides are rendered identically without either caller remembering.
    pin_text_rendering(conn)
    out: list[TableFingerprint] = []
    for table in list_user_tables(conn):
        row = conn.execute(_fingerprint_query(table, checksum_mode=checksum_mode)).fetchone()
        if row is None:  # pragma: no cover - aggregate always returns a row
            continue
        out.append(TableFingerprint(table=table, rows=int(row[0]), digest=str(row[1])))
    return out


#: How many cheap candidate tables to inspect when looking for one that
#: actually holds more than one tenant.
_PROBE_CANDIDATES = 8


def choose_tenant_probe_table(conn: psycopg.Connection[Any]) -> str | None:
    """Pick a cheap, non-empty, FORCE-RLS table carrying ``organization_id``.

    Cheap matters because the probe runs a GROUP BY over the table on both the
    source and the target, and the largest tenant table here holds millions of
    rows. But a table holding a single organization only proves that RLS filters
    *something*; a table holding two or more proves tenants are separated from
    each other, which is the claim worth making. So the cheapest candidates are
    inspected in order and the first genuinely multi-tenant one wins, falling
    back to the cheapest non-empty table when no candidate has two.
    """
    rows = conn.execute(
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'organization_id' "
        "LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND c.relrowsecurity AND c.relforcerowsecurity AND NOT a.attisdropped "
        "AND coalesce(s.n_live_tup, 0) > 0 "
        "ORDER BY coalesce(s.n_live_tup, 0) ASC, c.relname ASC LIMIT %s",
        (_PROBE_CANDIDATES,),
    ).fetchall()
    if not rows:
        return None
    candidates = [str(r[0]) for r in rows]
    for table in candidates:
        query = sql.SQL("SELECT count(DISTINCT organization_id) FROM {}").format(
            sql.Identifier("public", table)
        )
        found = conn.execute(query).fetchone()
        if found and int(found[0]) > 1:
            return table
    return candidates[0]


def collect_tenant_probe(
    conn: psycopg.Connection[Any], *, table: str | None = None
) -> TenantProbe | None:
    """Per-organization row counts for the isolation check."""
    chosen = table or choose_tenant_probe_table(conn)
    if chosen is None:
        return None
    query = sql.SQL(
        "SELECT organization_id::text, count(*)::bigint FROM {} GROUP BY 1 ORDER BY 1"
    ).format(sql.Identifier("public", chosen))
    rows = conn.execute(query).fetchall()
    return TenantProbe(table=chosen, counts={str(r[0]): int(r[1]) for r in rows})


@dataclass(frozen=True)
class Difference:
    table: str
    kind: str
    expected: str
    actual: str


def compare(
    expected: list[TableFingerprint], actual: list[TableFingerprint]
) -> list[Difference]:
    """Differences between a manifest's fingerprints and a restored target's.

    Row-count and digest mismatches are reported as distinct ``kind`` values:
    a count mismatch is missing data, a digest-only mismatch is altered or
    re-rendered data, and the two have different remediations.
    """
    actual_by_table = {t.table: t for t in actual}
    expected_by_table = {t.table: t for t in expected}
    diffs: list[Difference] = []
    for table in sorted(set(expected_by_table) | set(actual_by_table)):
        want = expected_by_table.get(table)
        got = actual_by_table.get(table)
        if want is None:
            diffs.append(Difference(table, "unexpected_table", "absent", "present"))
            continue
        if got is None:
            diffs.append(Difference(table, "missing_table", "present", "absent"))
            continue
        if want.rows != got.rows:
            diffs.append(Difference(table, "row_count", str(want.rows), str(got.rows)))
        elif want.digest and got.digest and want.digest != got.digest:
            diffs.append(Difference(table, "content_digest", want.digest, got.digest))
    return diffs
