"""Detect the worker's narrow platform entry points during staged rollout."""

from typing import cast

from sqlalchemy import text
from sqlalchemy.orm import Session

CLAIM_SIGNATURE = "aqs_worker_claim_job(timestamp with time zone,text[],text)"
RECLAIM_SIGNATURE = "aqs_worker_reclaim_jobs(timestamp with time zone,jsonb)"
STORAGE_KEY_SIGNATURE = "aqs_worker_storage_key_required(text)"
KEY_SIGNATURE = "aqs_worker_bank_key(text)"
DESK_SIGNATURE = "aqs_worker_desk_schema()"
AUDIT_SIGNATURE = "aqs_worker_audit_check(timestamp with time zone)"
ENSURE_MONTH_SIGNATURE = "aqs_worker_ensure_month_partition(regclass,date)"
ENSURE_YEAR_SIGNATURE = "aqs_worker_ensure_year_partition(regclass,date)"
DROP_SIGNATURE = "aqs_worker_drop_daily_partition(regclass,date)"
OVERDUE_SIGNATURE = "aqs_worker_overdue_jobs(double precision,text[],boolean)"
SEED_SIGNATURE = "aqs_worker_seed_ticks(timestamp with time zone)"


def has_worker_function(session: Session, signature: str) -> bool:
    """Old deployments keep their existing path until this migration is applied."""
    if session.get_bind().dialect.name != "postgresql":
        return False
    return bool(
        cast(
            bool | None,
            session.scalar(
                text(
                    "SELECT has_function_privilege(current_user, "
                    "to_regprocedure(:signature), 'EXECUTE')"
                ),
                {"signature": signature},
            ),
        )
    )
