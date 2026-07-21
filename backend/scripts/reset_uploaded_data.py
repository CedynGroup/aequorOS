"""Wipe a bank's ingested Data Engine artifacts for a clean reload.

DESTRUCTIVE and user-gated (--yes): use only when deliberately clearing a bank
before re-ingesting (e.g. a fresh history-simulator load). There is no seeded
fallback state — after a wipe the bank is EMPTY until data flows back in
through the Data Engine (uploads, core adapters, API push).

Deletes, for one bank in one organization:
- every Data Engine artifact: ingestion batches, lineage records, translation
  failures, mapping configs, canonical entities (gl accounts, counterparties,
  products, positions, snapshots) and canonical reference rows;
- every reporting period that was created by data activation (identified by
  facts carrying attributes.source == "data_engine"), together with that
  period's facts and regulatory runs;
- optionally (--purge-storage) every object in the configured S3/MinIO bucket.

Periods/facts not created by data activation (legacy rows, if any) are left
untouched. Audit events are never deleted (immutable log).

Usage:
    DATABASE_URL=postgresql+psycopg://... python scripts/reset_uploaded_data.py --yes
    python scripts/reset_uploaded_data.py --database-url ... --purge-storage --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import UUID

import boto3
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on path

from app.core.config import get_settings  # noqa: E402

DEMO_ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
SAMPLE_BANK_ID = UUID("77000000-0000-4000-8000-000000000001")

# Leaf -> root so foreign keys are satisfied: lineage_records references
# ingestion_batches (cleared via its parent batch — it has no bank_id), and
# ingestion_batches references mapping_configs, so batches precede configs.
DATA_ENGINE_TABLES: tuple[str, ...] = (
    "canonical_position_snapshots",
    "canonical_positions",
    "canonical_products",
    "canonical_counterparties",
    "canonical_gl_accounts",
    "canonical_reference_rows",
    "translation_failures",
    "lineage_records",
    "ingestion_batches",
    "mapping_configs",
)


def reset(
    session: Session,
    org_id: UUID,
    bank_id: UUID,
    *,
    wipe_all_periods: bool = False,
) -> dict[str, int]:
    """Remove uploaded data; with wipe_all_periods also remove every seeded
    reporting period (runs, facts, periods) so the bank is completely empty.
    The bank row, regulatory parameters, and audit log are always preserved —
    parameters are jurisdiction configuration the engines need to compute on
    freshly uploaded data, and the bank row anchors the UI and storage slug.
    Restore the synthetic baseline anytime with scripts/seed_sample_bank.py.
    """
    params = {"org": str(org_id), "bank": str(bank_id)}
    deleted: dict[str, int] = {}

    if wipe_all_periods:
        for table in ("regulatory_runs", "bank_financial_facts", "bank_reporting_periods"):
            result = session.execute(
                text(
                    f"DELETE FROM {table} "  # noqa: S608 - fixed allowlist
                    "WHERE organization_id = :org AND bank_id = :bank"
                ),
                params,
            )
            deleted[table] = deleted.get(table, 0) + (result.rowcount or 0)

    # 1. Periods created by data activation (facts tagged source=data_engine).
    period_rows = session.execute(
        text(
            "SELECT DISTINCT reporting_period_id FROM bank_financial_facts "
            "WHERE organization_id = :org AND bank_id = :bank "
            "AND attributes ->> 'source' = 'data_engine'"
        ),
        params,
    ).fetchall()
    period_ids = [str(row[0]) for row in period_rows]

    for period_id in period_ids:
        page = {**params, "period": period_id}
        for table, where in (
            ("regulatory_runs", "reporting_period_id = :period"),
            ("bank_financial_facts", "reporting_period_id = :period"),
            ("bank_reporting_periods", "id = :period"),
        ):
            result = session.execute(
                text(
                    f"DELETE FROM {table} "  # noqa: S608 - fixed allowlist
                    f"WHERE organization_id = :org AND bank_id = :bank AND {where}"
                ),
                page,
            )
            deleted[table] = deleted.get(table, 0) + (result.rowcount or 0)

    # 2. Data Engine artifacts.
    for table in DATA_ENGINE_TABLES:
        if table == "lineage_records":
            statement = (
                "DELETE FROM lineage_records WHERE ingestion_batch_id IN "
                "(SELECT id FROM ingestion_batches "
                " WHERE organization_id = :org AND bank_id = :bank)"
            )
        else:
            statement = (
                f"DELETE FROM {table} "  # noqa: S608 - fixed allowlist
                "WHERE organization_id = :org AND bank_id = :bank"
            )
        result = session.execute(text(statement), params)
        deleted[table] = deleted.get(table, 0) + (result.rowcount or 0)

    deleted["_activation_periods_removed"] = len(period_ids)
    return deleted


def purge_storage() -> int:
    """Delete every object in the configured RISK_S3 bucket. Returns count."""
    bucket = os.environ.get("RISK_S3_BUCKET", "risk-local")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("RISK_S3_ENDPOINT_URL", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("RISK_S3_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("RISK_S3_SECRET_ACCESS_KEY", "minioadmin"),
        region_name=os.environ.get("RISK_S3_REGION", "us-east-1"),
    )
    removed = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            removed += len(keys)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--org-id", default=str(DEMO_ORG_ID))
    parser.add_argument("--bank-id", default=str(SAMPLE_BANK_ID))
    parser.add_argument("--purge-storage", action="store_true")
    parser.add_argument(
        "--wipe-all-periods",
        action="store_true",
        help="Also delete every seeded reporting period (runs, facts, periods) "
        "so only the bank shell and regulatory parameters remain.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation.")
    args = parser.parse_args()

    # Fall back to the app's configured URL (env or backend/.env) — same source
    # the running service uses. No hard-coded local default.
    if not args.database_url:
        args.database_url = get_settings().database.database_url
    if not args.database_url:
        print(
            "DATABASE_URL is required (env, backend/.env, or --database-url).",
            file=sys.stderr,
        )
        return 2
    if not args.yes:
        print("Refusing to delete without --yes.", file=sys.stderr)
        return 2

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        deleted = reset(
            session,
            UUID(args.org_id),
            UUID(args.bank_id),
            wipe_all_periods=args.wipe_all_periods,
        )
        session.commit()

    for table, count in sorted(deleted.items()):
        print(f"  {table:36} {count:>8}")

    if args.purge_storage:
        removed = purge_storage()
        print(f"  {'storage objects purged':36} {removed:>8}")

    if args.wipe_all_periods:
        print(
            "Reset complete — bank is empty (parameters and bank shell kept). "
            "Restore the synthetic baseline with scripts/seed_sample_bank.py."
        )
    else:
        print("Reset complete — bank is back to its seeded state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
