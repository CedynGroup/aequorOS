"""Unit tests for the backup / restore / drill tooling.

These are hermetic: they exercise the guards and the evidence format without a
database, because the guards are what stand between a routine exercise and an
overwritten primary, and they must hold on a laptop with no Postgres at all.

The end-to-end drill (provision, restore, verify, tear down) is a separate,
opt-in test below — it needs a real non-production cluster and is skipped rather
than faked when one is not configured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from scripts.dr_common import (
    DisasterRecoveryError,
    assert_client_can_dump,
    assert_dump_role_sees_all_rows,
    describe,
    refuse_protected_cluster,
    refuse_protected_target,
    same_cluster,
    same_database,
    to_libpq,
    tool_env,
)
from scripts.dr_manifest import BackupManifest, TableFingerprint, TenantProbe, compare
from scripts.restore_database import filter_toc, maintenance_url, target_database_name

SECRET = "hunter2superSecret"
PRIMARY = f"postgresql+psycopg://app:{SECRET}@pg.example.com:5433/aequoros_db"


class TestUrlHandling:
    def test_driver_prefix_is_stripped_for_libpq(self) -> None:
        assert to_libpq(PRIMARY).startswith("postgresql://")

    def test_describe_never_reveals_a_credential(self) -> None:
        rendered = describe(PRIMARY)
        assert SECRET not in rendered
        assert "app" not in rendered
        assert rendered == "pg.example.com:5433/aequoros_db"

    def test_same_database_ignores_role_and_driver(self) -> None:
        """A different username pointing at the primary is still the primary."""
        assert same_database(PRIMARY, "postgresql://other:x@pg.example.com:5433/aequoros_db")

    def test_same_database_distinguishes_the_database(self) -> None:
        assert not same_database(PRIMARY, "postgresql://app:x@pg.example.com:5433/restore_target")

    def test_same_cluster_ignores_the_database(self) -> None:
        assert same_cluster(PRIMARY, "postgresql://app:x@pg.example.com:5433/postgres")
        assert not same_cluster(PRIMARY, "postgresql://app:x@drill.example.com:5433/postgres")


class TestGuards:
    def test_restore_refuses_the_primary(self) -> None:
        with pytest.raises(DisasterRecoveryError, match="protected production database"):
            refuse_protected_target(
                "postgresql://other:x@pg.example.com:5433/aequoros_db", [PRIMARY]
            )

    def test_restore_allows_a_dedicated_target(self) -> None:
        refuse_protected_target("postgresql://x:y@restore.example.com:5432/aequoros", [PRIMARY])

    def test_drill_refuses_the_primary_cluster_even_for_another_database(self) -> None:
        with pytest.raises(DisasterRecoveryError, match="creates and drops databases"):
            refuse_protected_cluster("postgresql://x:y@pg.example.com:5433/postgres", [PRIMARY])

    def test_guards_tolerate_unset_environment_entries(self) -> None:
        refuse_protected_target("postgresql://x:y@a.example.com:5432/b", [None, "", "   "])

    def test_dump_refuses_a_role_that_rls_would_filter(self) -> None:
        """The silent-empty-backup guard: no BYPASSRLS means no rows, not an error."""
        with pytest.raises(DisasterRecoveryError, match="silently empty"):
            assert_dump_role_sees_all_rows(role="risk_app", superuser=False, bypassrls=False)

    @pytest.mark.parametrize(
        ("superuser", "bypassrls"), [(True, False), (False, True), (True, True)]
    )
    def test_dump_accepts_a_role_that_sees_every_row(
        self, superuser: bool, bypassrls: bool
    ) -> None:
        assert_dump_role_sees_all_rows(role="worker", superuser=superuser, bypassrls=bypassrls)

    def test_older_client_is_refused_against_a_newer_server(self) -> None:
        with pytest.raises(DisasterRecoveryError, match="cannot dump a PostgreSQL 15.13"):
            assert_client_can_dump(14, "15.13")

    @pytest.mark.parametrize("client_major", [15, 16, 17])
    def test_equal_or_newer_client_is_accepted(self, client_major: int) -> None:
        assert_client_can_dump(client_major, "15.13")


class TestToolEnvironment:
    def test_credential_travels_in_the_environment_not_the_command_line(self) -> None:
        env = tool_env(PRIMARY)
        assert env["PGPASSWORD"] == SECRET
        assert env["PGHOST"] == "pg.example.com"
        assert env["PGPORT"] == "5433"
        assert env["PGDATABASE"] == "aequoros_db"

    def test_backup_sessions_are_pinned_read_only(self) -> None:
        env = tool_env(PRIMARY, read_only=True)
        assert "default_transaction_read_only=on" in env["PGOPTIONS"]

    def test_restore_sessions_are_not_pinned_read_only(self) -> None:
        assert "default_transaction_read_only" not in tool_env(PRIMARY).get("PGOPTIONS", "")


class TestManifest:
    def _manifest(self) -> BackupManifest:
        return BackupManifest(
            taken_at="2026-08-22T00:00:00+00:00",
            source="pg.example.com:5433/aequoros_db",
            server_version="15.13",
            client_major=15,
            alembic_revision="202608210026",
            checksum_mode="full",
            archive_name="a.dump",
            archive_bytes=10,
            archive_sha256="deadbeef",
            extensions=["plpgsql", "timescaledb"],
            tables=[TableFingerprint("banks", 4, "11"), TableFingerprint("facts", 900, "22")],
            tenant_probe=TenantProbe(table="banks", counts={"OR-A": 3, "OR-B": 1}),
        )

    def test_round_trips_through_json(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        self._manifest().write(path)
        assert BackupManifest.read(path) == self._manifest()

    def test_manifest_carries_no_credential(self, tmp_path: Path) -> None:
        path = tmp_path / "m.json"
        self._manifest().write(path)
        assert SECRET not in path.read_text(encoding="utf-8")

    def test_total_rows_sums_every_table(self) -> None:
        assert self._manifest().total_rows == 904

    def test_manifest_is_valid_json(self) -> None:
        assert json.loads(self._manifest().to_json())["alembic_revision"] == "202608210026"


class TestComparison:
    BASE = [TableFingerprint("banks", 4, "11"), TableFingerprint("facts", 900, "22")]

    def test_identical_fingerprints_produce_no_differences(self) -> None:
        assert compare(self.BASE, list(self.BASE)) == []

    def test_missing_rows_are_reported_as_a_row_count_difference(self) -> None:
        actual = [TableFingerprint("banks", 4, "11"), TableFingerprint("facts", 0, "0")]
        diffs = compare(self.BASE, actual)
        assert [(d.table, d.kind) for d in diffs] == [("facts", "row_count")]

    def test_altered_values_are_reported_as_a_digest_difference(self) -> None:
        """Same row count, different content — the case counts alone cannot see."""
        actual = [TableFingerprint("banks", 4, "11"), TableFingerprint("facts", 900, "99")]
        diffs = compare(self.BASE, actual)
        assert [(d.table, d.kind) for d in diffs] == [("facts", "content_digest")]

    def test_a_dropped_table_is_reported(self) -> None:
        diffs = compare(self.BASE, [TableFingerprint("banks", 4, "11")])
        assert [(d.table, d.kind) for d in diffs] == [("facts", "missing_table")]

    def test_an_extra_table_is_reported(self) -> None:
        actual = [*self.BASE, TableFingerprint("stowaway", 1, "3")]
        diffs = compare(self.BASE, actual)
        assert [(d.table, d.kind) for d in diffs] == [("stowaway", "unexpected_table")]


class TestTableOfContentsFilter:
    TOC = (
        ";\n"
        "5; 2615 2200 SCHEMA - public postgres\n"
        "6; 0 0 COMMENT - SCHEMA public postgres\n"
        "3; 3079 16384 EXTENSION - timescaledb \n"
        "4; 0 0 COMMENT - EXTENSION timescaledb \n"
        "5; 3079 16385 EXTENSION - pgcrypto \n"
        "215; 1259 16456 TABLE public banks postgres\n"
    )

    def test_unavailable_extension_entries_are_dropped(self) -> None:
        filtered = filter_toc(self.TOC, drop_extensions={"timescaledb"})
        assert "timescaledb" not in filtered

    def test_available_extensions_and_tables_survive(self) -> None:
        filtered = filter_toc(self.TOC, drop_extensions={"timescaledb"})
        assert "pgcrypto" in filtered
        assert "TABLE public banks" in filtered

    def test_no_exclusions_leaves_the_toc_untouched(self) -> None:
        assert filter_toc(self.TOC, drop_extensions=set()) == self.TOC

    def test_an_already_present_schema_is_dropped(self) -> None:
        """Every fresh database has ``public``; replaying CREATE SCHEMA public errors."""
        filtered = filter_toc(self.TOC, drop_schemas={"public"})
        assert "SCHEMA - public" not in filtered
        assert "COMMENT - SCHEMA public" not in filtered

    def test_dropping_a_schema_keeps_tables_in_that_schema(self) -> None:
        filtered = filter_toc(self.TOC, drop_schemas={"public"})
        assert "TABLE public banks" in filtered

    def test_extensions_and_schemas_can_be_dropped_together(self) -> None:
        filtered = filter_toc(
            self.TOC, drop_extensions={"timescaledb"}, drop_schemas={"public"}
        )
        assert "timescaledb" not in filtered
        assert "SCHEMA - public" not in filtered
        assert "pgcrypto" in filtered
        assert "TABLE public banks" in filtered


class TestRestoreUrlHelpers:
    def test_maintenance_url_keeps_the_cluster_and_swaps_the_database(self) -> None:
        assert maintenance_url("postgresql://u:p@h:5432/target") == "postgresql://u:p@h:5432/postgres"

    def test_target_database_name_is_extracted(self) -> None:
        assert target_database_name("postgresql+psycopg://u:p@h:5432/aequoros_restore") == (
            "aequoros_restore"
        )


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

    source_db = "aequoros_dr_pytest_src"
    with psycopg.connect(to_libpq(_DRILL_CLUSTER), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(source_db)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source_db)))
    source_url = maintenance_url(_DRILL_CLUSTER, database=source_db)
    try:
        with psycopg.connect(to_libpq(source_url), autocommit=True) as conn:
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
    finally:
        with psycopg.connect(to_libpq(_DRILL_CLUSTER), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(source_db)))
