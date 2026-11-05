"""Unit tests for the backup / restore / drill tooling.

These are hermetic: they exercise the guards and the evidence format without a
database, because the guards are what stand between a routine exercise and an
overwritten primary, and they must hold on a laptop with no Postgres at all.

The end-to-end drill (provision, restore, verify, tear down) is a separate,
opt-in test below — it needs a real non-production cluster and is skipped rather
than faked when one is not configured.

This module's other-suite cases live in ``test_dr_tooling.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.dr_common import (
    protected_urls_from_env,
    refuse_protected_cluster,
)
from tests.support.database import isolated_postgres_database

# --- Opt-in end-to-end drill -------------------------------------------------
#
# Runs the real thing against a real cluster. Skipped, never faked, when no
# non-production cluster is configured:
#
#   DR_DRILL_CLUSTER_URL=postgresql://user@127.0.0.1:5432/postgres \
#     uv run pytest tests/scripts/test_dr_tooling.py -k end_to_end
_DRILL_CLUSTER = os.getenv("DR_DRILL_CLUSTER_URL", "").strip()


@pytest.mark.skipif(not _DRILL_CLUSTER, reason="DR_DRILL_CLUSTER_URL is not configured")
def test_end_to_end_drill_restores_and_verifies(tmp_path: Path) -> None:
    """Back up a seeded database, restore it elsewhere, and verify every check."""
    from scripts.restore_drill import DrillOptions, run_drill  # noqa: PLC0415 - opt-in path

    refuse_protected_cluster(_DRILL_CLUSTER, protected_urls_from_env())
    with isolated_postgres_database(_DRILL_CLUSTER) as source:
        source_url = source.database_url
        with source.raw_connection(autocommit=True) as conn:
            conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            conn.execute("INSERT INTO alembic_version VALUES ('test_head')")
            conn.execute("CREATE TABLE organizations (id text PRIMARY KEY, name text NOT NULL)")
            conn.execute("INSERT INTO organizations VALUES ('OR-A','A'),('OR-B','B')")
            conn.execute(
                "CREATE TABLE facts (id serial PRIMARY KEY, organization_id text NOT NULL, v int)"
            )
            conn.execute(
                "INSERT INTO facts (organization_id, v) "
                "SELECT 'OR-A', g FROM generate_series(1,25) g"
            )
            conn.execute(
                "INSERT INTO facts (organization_id, v) "
                "SELECT 'OR-B', g FROM generate_series(1,7) g"
            )
            conn.execute("ALTER TABLE facts ENABLE ROW LEVEL SECURITY")
            conn.execute("ALTER TABLE facts FORCE ROW LEVEL SECURITY")
            conn.execute(
                "CREATE POLICY tenant_isolation ON facts USING (organization_id = "
                "nullif(current_setting('app.organization_id', true), ''))"
            )
            conn.execute("ANALYZE")

        previous = os.environ.get("BACKUP_SOURCE_DATABASE_URL")
        os.environ["BACKUP_SOURCE_DATABASE_URL"] = source_url
        try:
            ok, timings = run_drill(
                _DRILL_CLUSTER, DrillOptions(out_dir=tmp_path, checksum_mode="full")
            )
        finally:
            if previous is None:
                os.environ.pop("BACKUP_SOURCE_DATABASE_URL", None)
            else:
                os.environ["BACKUP_SOURCE_DATABASE_URL"] = previous
        assert ok, "the drill must restore and verify cleanly"
        assert timings.recovery_seconds >= 0.0
