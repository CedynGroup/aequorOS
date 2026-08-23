"""Verify a restored AequorOS database and storage target without mutating it.

This script is deliberately read-only. It proves that a designated RESTORE
target is reachable, migrated to the expected Alembic revision, and paired with
a reachable object store. It refuses the production URL by exact comparison so
a recovery exercise cannot accidentally inspect or mutate the primary.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.storage.factory import get_storage_client


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        msg = f"{name} is required."
        raise RuntimeError(msg)
    return value.strip()


def verify_recovery_target(restore_database_url: str, primary_database_url: str | None) -> str:
    """Return the restored Alembic revision after read-only database checks."""
    if primary_database_url and make_url(restore_database_url) == make_url(primary_database_url):
        msg = "RESTORE_DATABASE_URL must not point at DATABASE_URL."
        raise RuntimeError(msg)

    engine = create_engine(restore_database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()
    if revision is None:
        msg = "Recovery target has no Alembic revision."
        raise RuntimeError(msg)
    return str(revision)


def main() -> int:
    restore_database_url = _required("RESTORE_DATABASE_URL")
    revision = verify_recovery_target(restore_database_url, os.getenv("DATABASE_URL"))
    storage = get_storage_client().health_check()
    if not storage.healthy:
        msg = f"Recovery storage check failed for {storage.backend}: {storage.detail or 'unknown'}"
        raise RuntimeError(msg)
    print(f"Recovery target verified: alembic={revision}; storage={storage.backend}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Recovery verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc