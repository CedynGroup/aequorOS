"""Repoint restored, pinned object versions through the audited recovery operation.

Run against a dedicated restored database using an incident-only credential in
RESTORE_DATABASE_URL. See backend/docs/bank_key_setup.md for authority and scope.
This script never unwraps bank keys or modifies document/signature payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from typing import cast

from psycopg import Error as PostgresError
from psycopg import pq
from pydantic import TypeAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.engine import URL, Connection, Dialect, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.db.session import role_bypasses_rls
from scripts.backup_storage import RestoreReport
from scripts.dr_common import (
    PROTECTED_TARGET_REFUSAL,
    DisasterRecoveryError,
    load_env_file,
    protected_urls_from_env,
)

_ROUTING_REFUSAL = "Explicit, comparable PostgreSQL routing is required."
_LOCAL_ROUTE = "<local>"


def _address_identity(value: str) -> str:
    address = ip_address(value)
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return _LOCAL_ROUTE if address.is_loopback else str(address)


def _host_addresses(host: str) -> frozenset[str]:
    if host.startswith(("/", "@")):
        return frozenset((_LOCAL_ROUTE,))
    try:
        return frozenset((_address_identity(host),))
    except ValueError:
        hostname = host.lower().rstrip(".")
    try:
        records = cast(
            list[
                tuple[
                    object,
                    object,
                    object,
                    object,
                    tuple[str, int] | tuple[str, int, int, int],
                ]
            ],
            socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM),
        )
    except OSError:
        raise DisasterRecoveryError(_ROUTING_REFUSAL) from None
    if not records:
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    # Keep the name as well: round-robin DNS answers must not make the same
    # configured hostname appear distinct across two successive lookups.
    return frozenset((hostname, *(_address_identity(record[4][0]) for record in records)))


def _database_targets(url: URL) -> frozenset[tuple[str, int, str]]:
    """Compare the actual psycopg routing, including every fallback candidate.

    Service files, hostaddr and implicit socket/database defaults can hide the
    destination. Require explicit comparable routing instead of guessing it.
    """
    if url.get_backend_name() != "postgresql":
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    dialect = cast(Dialect, PGDialect_psycopg())
    positional, parameters = cast(
        tuple[list[object], dict[str, object]],
        dialect.create_connect_args(url.set(drivername="postgresql+psycopg")),
    )
    if any(positional) or any(
        key in parameters or os.getenv(f"PG{key.upper()}") for key in ("service", "hostaddr")
    ):
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    host = parameters.get("host", os.getenv("PGHOST"))
    database = parameters.get("dbname", os.getenv("PGDATABASE"))
    if not isinstance(host, str) or not host or not isinstance(database, str) or not database:
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    default_port = next(
        option.compiled.decode("ascii")
        for option in pq.Conninfo.get_defaults()
        if option.keyword == b"port" and option.compiled is not None
    )
    raw_port = parameters.get("port", os.getenv("PGPORT") or default_port)
    if not isinstance(raw_port, (int, str)):
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    hosts = host.split(",")
    ports = str(raw_port).split(",")
    if len(ports) == 1:
        ports *= len(hosts)
    if len(hosts) != len(ports) or not all(hosts):
        raise DisasterRecoveryError(_ROUTING_REFUSAL)
    targets: set[tuple[str, int, str]] = set()
    for candidate, port in zip(hosts, ports, strict=True):
        # An explicit empty port/list entry uses libpq's compiled default, not PGPORT.
        number = int(port or default_port)
        if not 0 < number < 65536:
            raise DisasterRecoveryError(_ROUTING_REFUSAL)
        targets.update((address, number, database) for address in _host_addresses(candidate))
    return frozenset(targets)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise DisasterRecoveryError("Ambiguous duplicate field in restore report.")
    return fields


def load_mapping(path: Path) -> RestoreReport:
    """Read precisely the report emitted by backup_storage's restore replay."""
    raw = path.read_bytes()
    _ = cast(object, json.loads(raw, object_pairs_hook=_unique_fields))
    report = TypeAdapter(RestoreReport).validate_json(raw, strict=True)
    old: set[tuple[str, str, str]] = set()
    new: set[tuple[str, str, str]] = set()
    if not report.versions or report.objects != len(report.versions):
        raise DisasterRecoveryError("Restore report must contain every restored object mapping.")
    for version in report.versions:
        before = (version.bucket, version.key, version.backed_up_version_id)
        after = (version.bucket, version.key, version.restored_version_id)
        if (
            not all(value.strip() for value in (version.bucket, version.key))
            or any(value and not value.strip() for value in (before[2], after[2]))
            or len(version.restored_version_id) > 255
            or re.fullmatch(r"[a-f0-9]{64}", version.sha256) is None
        ):
            raise DisasterRecoveryError("Incomplete restore version mapping.")
        if before in old or after in new:
            raise DisasterRecoveryError("Ambiguous restore version mapping.")
        old.add(before)
        new.add(after)
    return report


def reconcile_storage_versions(  # noqa: PLR0913 - recovery scope is explicit
    connection: Connection,
    report: RestoreReport,
    *,
    storage_env: str,
    reason: str,
    organization_id: str | None = None,
    bank_id: str | None = None,
) -> int:
    """Reconcile atomically in the caller's transaction; refuse RLS-filtered success.

    PostgreSQL independently proves the incident identity inside its definer
    operation. This early check prevents even attempting recovery through a
    tenant-filtered connection. There is deliberately no SQLite UPDATE fallback.
    """
    if connection.dialect.name != "postgresql" or not role_bypasses_rls(connection):
        raise DisasterRecoveryError("Proved cross-tenant PostgreSQL recovery session required.")
    return cast(
        int,
        connection.scalar(
            text("""
                    SELECT aqs_reconcile_storage_versions(
                        CAST(:report AS jsonb), :env, :reason, :organization, :bank)
                """),
            {
                "report": TypeAdapter(RestoreReport).dump_json(report).decode(),
                "env": storage_env,
                "reason": reason,
                "organization": organization_id,
                "bank": bank_id,
            },
        ),
    )


class Arguments(argparse.Namespace):
    mapping: Path
    storage_env: str
    reason: str
    organization_id: str | None
    bank_id: str | None
    env_file: Path | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--storage-env", required=True, choices=("prod", "staging", "dev", "mvp"))
    parser.add_argument("--reason", required=True, help="Recovery incident or change reference.")
    parser.add_argument("--organization-id")
    parser.add_argument("--bank-id")
    parser.add_argument("--env-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv, namespace=Arguments())
    if args.env_file:
        try:
            load_env_file(args.env_file)
        except DisasterRecoveryError:
            raise DisasterRecoveryError("Environment file not found.") from None
    target = os.environ.get("RESTORE_DATABASE_URL", "").strip()
    if not target:
        raise DisasterRecoveryError("RESTORE_DATABASE_URL must name a dedicated recovery target.")
    protected = protected_urls_from_env()
    if not protected:
        raise DisasterRecoveryError(
            "At least one protected primary database URL must be configured."
        )
    target_url = make_url(target)
    if target_url.get_backend_name() == "postgresql":
        targets = _database_targets(target_url)
        for primary in protected:
            if targets & _database_targets(make_url(primary)):
                raise DisasterRecoveryError(PROTECTED_TARGET_REFUSAL)
    report = load_mapping(args.mapping)
    if target_url.get_backend_name() != "postgresql":
        raise DisasterRecoveryError("Recovery requires a PostgreSQL target.")
    engine = create_engine(target_url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            changed = reconcile_storage_versions(
                connection,
                report,
                storage_env=args.storage_env,
                reason=args.reason,
                organization_id=args.organization_id,
                bank_id=args.bank_id,
            )
    finally:
        engine.dispose()
    print(
        json.dumps({"reconciled_references": changed, "audit_event": "storage.version_reconciled"})
    )
    return 0


def cli(argv: list[str] | None = None) -> int:
    """Render only fixed refusals and the PostgreSQL primary diagnostic."""
    try:
        return main(argv)
    except (DisasterRecoveryError, SQLAlchemyError, ValueError, OSError) as exc:
        reason = ""
        if isinstance(exc, DisasterRecoveryError):
            reason = str(exc)
        elif isinstance(exc, DBAPIError) and isinstance(exc.orig, PostgresError):
            reason = exc.orig.diag.message_primary or ""
        message = "[reconcile] FAILED: recovery refused; transaction rolled back."
        print(f"{message} {reason}" if reason else message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
