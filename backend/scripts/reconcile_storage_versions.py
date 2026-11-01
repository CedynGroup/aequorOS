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
import sys
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import role_bypasses_rls
from scripts.backup_storage import RestoreReport
from scripts.dr_common import (
    DisasterRecoveryError,
    load_env_file,
    protected_urls_from_env,
    refuse_protected_target,
)


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
            not all(value.strip() for value in (*before, *after))
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
        load_env_file(args.env_file)
    target = os.environ.get("RESTORE_DATABASE_URL", "").strip()
    if not target:
        raise DisasterRecoveryError("RESTORE_DATABASE_URL must name a dedicated recovery target.")
    refuse_protected_target(target, protected_urls_from_env())
    report = load_mapping(args.mapping)
    target_url = make_url(target)
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DisasterRecoveryError, SQLAlchemyError, ValueError, OSError):
        # Driver errors may include credentials or object identifiers.
        print("[reconcile] FAILED: recovery refused; transaction rolled back.", file=sys.stderr)
        raise SystemExit(1) from None
