"""Postgres proof for the BI foundation migration (``202609220066``).

Everything this migration adds that the ORM cannot express — ``PARTITION BY``,
the DEFAULT partitions, the ``SECURITY DEFINER`` ensure-partition functions,
FORCE row-level security on every partition, and the append-only guards on
``bi_query_log`` — exists only as DDL in the migration, so it can only be
checked on a migrated Postgres. The hermetic suite builds plain tables with
``create_all`` on SQLite (D-011) and sees none of it.

``migrated_postgres_schema`` runs ``upgrade head`` then ``downgrade base``
around each test, which is the round-trip proof; the assertions in between are
about what head looks like. Postgres-gated on ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.bi.models.marts import (
    BI_TABLES,
    MONTHLY_PARTITIONED_TABLES,
    YEARLY_PARTITIONED_TABLES,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
    postgres_schema_url,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

REVISION = "202609220066"
PREVIOUS_REVISION = "202609200065"
#: The foundation objects span several revisions: marts, target and GL branch
#: tables, position-fact columns and query-log vocabulary. Replay through the
#: final foundation revision so the round trip restores every registered mart.
LAST_BI_REVISION = "202609280077"
PARTITIONED = (*MONTHLY_PARTITIONED_TABLES, *YEARLY_PARTITIONED_TABLES)
PLAIN = tuple(table for table in BI_TABLES if table not in PARTITIONED)
ORG = "OR-BIFND0001"
BANK = "BK-BIFND0001"

_RELATION_STATE = text(
    """
    SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
           (SELECT count(*) FROM pg_policies p
             WHERE p.schemaname = n.nspname AND p.tablename = c.relname
               AND p.policyname = c.relname || '_tenant_isolation') AS tenant_policies
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema_name AND c.relname = ANY(:names)
    """
)


def _relation_state(schema: MigratedPostgresSchema, names: list[str]) -> dict[str, tuple]:
    with schema.app_engine.connect() as connection:
        rows = connection.execute(
            _RELATION_STATE, {"schema_name": schema.schema_name, "names": names}
        ).all()
    return {
        row.relname: (row.relkind, row.relrowsecurity, row.relforcerowsecurity, row.tenant_policies)
        for row in rows
    }


def _set_tenant(connection: Connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _seed_tenant(connection: Connection) -> None:
    _set_tenant(connection, ORG)
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at, updated_at) "
            "VALUES (:id, 'BI foundation proof', now(), now())"
        ),
        {"id": ORG},
    )
    connection.execute(
        text(
            """
            INSERT INTO banks
              (id, organization_id, name, short_name, currency, jurisdiction_code,
               license_type, institution_type, created_at, updated_at)
            VALUES
              (:bank_id, :organization_id, 'BI proof bank', 'BIP', 'GHS', 'GH',
               'universal', 'universal_bank', now(), now())
            """
        ),
        {"bank_id": BANK, "organization_id": ORG},
    )


def _insert_query_log(connection: Connection, queried_at: datetime) -> str:
    row_id = str(uuid4())
    connection.execute(
        text(
            """
            INSERT INTO bi_query_log
              (queried_at, id, organization_id, bank_id, principal_user_id, principal_type,
               surface, query_hash, member_ids, decision, denied_members, catalogue_version)
            VALUES
              (:queried_at, :id, :organization_id, :bank_id, :principal, 'human',
               'query', :query_hash, '["m.one"]', 'allowed', '[]', 'v1')
            """
        ),
        {
            "queried_at": queried_at,
            "id": row_id,
            "organization_id": ORG,
            "bank_id": BANK,
            "principal": str(uuid4()),
            "query_hash": "a" * 64,
        },
    )
    return row_id


def test_every_bi_table_is_partitioned_as_declared_and_force_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Parents are ``relkind='p'``, each has a FORCE-RLS DEFAULT partition, plain
    tables are FORCE-RLS, and all of them carry the standard tenant policy."""
    defaults = [f"{table}_default" for table in PARTITIONED]
    state = _relation_state(migrated_postgres_schema, [*BI_TABLES, *defaults])

    assert set(state) == {*BI_TABLES, *defaults}, "a BI table or DEFAULT partition is missing"
    for table in PARTITIONED:
        assert state[table] == ("p", True, True, 1), f"{table}: {state[table]}"
        assert state[f"{table}_default"] == ("r", True, True, 1), (
            f"{table}_default: {state[f'{table}_default']}"
        )
    for table in PLAIN:
        assert state[table] == ("r", True, True, 1), f"{table}: {state[table]}"

    # The model and the migration name the same partition keys.
    with migrated_postgres_schema.app_engine.connect() as connection:
        keys = {
            key: value
            for key, value in connection.execute(
                text(
                    """
                    SELECT c.relname, a.attname
                    FROM pg_partitioned_table pt
                    JOIN pg_class c ON c.oid = pt.partrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = pt.partattrs[0]
                    WHERE n.nspname = :schema_name
                    """
                ),
                {"schema_name": migrated_postgres_schema.schema_name},
            ).tuples()
        }
    assert keys == {**MONTHLY_PARTITIONED_TABLES, **YEARLY_PARTITIONED_TABLES}


def test_ensure_partition_functions_are_idempotent_and_install_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    engine = migrated_postgres_schema.app_engine
    schema_name = migrated_postgres_schema.schema_name

    with engine.begin() as connection:
        first = connection.scalar(
            text("SELECT bi_ensure_month_partition('bi_fact_position_daily', DATE '2026-09-17')")
        )
        second = connection.scalar(
            text("SELECT bi_ensure_month_partition('bi_fact_position_daily', DATE '2026-09-01')")
        )
        yearly = connection.scalar(
            text("SELECT bi_ensure_year_partition('bi_fact_position_eom', DATE '2026-04-30')")
        )
        # The query log ranges on a timestamptz. Its bounds must be UTC
        # midnight regardless of the CALLING session's TimeZone (audit A4-01):
        # a New York session used to produce ('2026-09-01 04:00+00') bounds,
        # which then overlapped October created from a UTC session and left a
        # four-hour hole for rows to fall into DEFAULT.
        connection.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        query_log_child = connection.scalar(
            text("SELECT bi_ensure_month_partition('bi_query_log', DATE '2026-09-30')")
        )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        query_log_next = connection.scalar(
            text("SELECT bi_ensure_month_partition('bi_query_log', DATE '2026-10-01')")
        )
    assert first == second == "bi_fact_position_daily_y2026m09"
    assert yearly == "bi_fact_position_eom_y2026"
    assert query_log_child == "bi_query_log_y2026m09"
    assert query_log_next == "bi_query_log_y2026m10"

    state = _relation_state(migrated_postgres_schema, [first, yearly, query_log_child])
    assert state[first] == ("r", True, True, 1)
    assert state[yearly] == ("r", True, True, 1)
    assert state[query_log_child] == ("r", True, True, 1)

    with engine.connect() as connection:
        # Rendered in UTC so the timestamptz bounds read as what is stored.
        connection.execute(text("SET TIME ZONE 'UTC'"))
        bounds = {
            key: value
            for key, value in connection.execute(
                text(
                    """
                    SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema_name AND c.relname = ANY(:names)
                    """
                ),
                {
                    "schema_name": schema_name,
                    "names": [first, yearly, query_log_child, query_log_next],
                },
            ).tuples()
        }
        # The query-log child inherits the parent's append-only denial and its
        # cloned row trigger, so a partition addressed by name is as closed as
        # the parent.
        child_policies = set(
            connection.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :schema_name AND tablename = :table"
                ),
                {"schema_name": schema_name, "table": query_log_child},
            ).scalars()
        )
        child_triggers = set(
            connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema_name AND c.relname = :table AND NOT t.tgisinternal"
                ),
                {"schema_name": schema_name, "table": query_log_child},
            ).scalars()
        )
    assert bounds[first] == "FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')"
    assert bounds[yearly] == "FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')"
    assert bounds[query_log_child] == (
        "FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00')"
    )
    assert bounds[query_log_next] == (
        "FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00')"
    )
    assert child_policies == {
        f"{query_log_child}_tenant_isolation",
        f"{query_log_child}_no_update",
        f"{query_log_child}_no_delete",
    }
    assert child_triggers == {"bi_query_log_append_only"}

    # Definer functions must not be a general-purpose CREATE TABLE gadget, and
    # each accepts only the parents of its own cadence (audit A4-02): a yearly
    # child on the monthly daily fact would block every monthly child of that
    # year and never match retention's monthly names.
    refusals = (
        (
            "SELECT bi_ensure_month_partition('authorization_bindings', DATE '2026-09-01')",
            "is not a partitioned table",
        ),
        (
            "SELECT bi_ensure_year_partition('bi_fact_position_daily', DATE '2027-06-01')",
            "is not a yearly-partitioned BI table",
        ),
        (
            "SELECT bi_ensure_month_partition('bi_fact_position_eom', DATE '2027-06-01')",
            "is not a monthly-partitioned BI table",
        ),
        (
            "SELECT bi_drop_year_partition('bi_fact_position_daily', DATE '2026-09-01')",
            "is not a yearly-partitioned BI table",
        ),
        (
            "SELECT bi_drop_month_partition('bi_fact_position_eom', DATE '2026-01-01')",
            "is not a monthly-partitioned BI table",
        ),
    )
    for statement, message in refusals:
        with engine.connect() as connection, pytest.raises(DBAPIError) as refused:
            connection.execute(text(statement))
        assert message in str(refused.value), statement
    state = _relation_state(
        migrated_postgres_schema,
        ["bi_fact_position_daily_y2027", "bi_fact_position_eom_y2027m06", first, yearly],
    )
    assert set(state) == {first, yearly}, "a cross-cadence child was created or one was dropped"


#: Set in CI. When set, a privilege test may not SKIP for want of ``CREATEROLE``
#: — it must run or fail. Audit finding A9-06: the one test that proves the
#: ``SECURITY DEFINER`` partition functions work for a NON-OWNER worker skipped
#: itself on every CI run, because the restricted migration role was created
#: ``NOCREATEROLE`` and the test needs a second role to be a non-owner OF. The
#: wholesale guard (``.github/scripts/assert_pytest_ran.py --min-executed``)
#: cannot see one test abstaining inside a suite of hundreds that ran. A skip is
#: the right behaviour on a developer's restricted connection and the wrong
#: behaviour in the gate; this variable is how the two are told apart.
_PRIVILEGE_TESTS_REQUIRED = "POSTGRES_PRIVILEGE_TESTS_REQUIRED"


def _require_or_skip_privileges(reason: str) -> None:
    """Skip locally, FAIL in the gate.

    ``CREATEROLE`` does not imply ``BYPASSRLS``, so granting it to the CI test
    role leaves every tenant-isolation assertion in this suite exercised exactly
    as before — which is the property the workflow's own comment says must never
    be weakened. On PostgreSQL 16 and later a ``CREATEROLE`` role may administer
    only the roles it created itself, so the grant confers nothing over the
    roles that already exist.
    """

    if os.getenv(_PRIVILEGE_TESTS_REQUIRED):
        pytest.fail(
            f"{reason} — but {_PRIVILEGE_TESTS_REQUIRED} is set, so this "
            "environment is required to be able to run privilege tests. Grant "
            "CREATEROLE to the test role (it does not confer BYPASSRLS) rather "
            "than letting this proof abstain."
        )
    pytest.skip(reason)


def _temporary_non_owner_role(admin: Connection, name: str) -> bool:
    """Create a NOLOGIN role the test connection can ``SET ROLE`` to.

    Mirrors the production topology, where the BYPASSRLS worker is a distinct
    role from the app role that owns every table. BYPASSRLS is requested when
    the creating role may confer it and dropped otherwise — DDL ownership,
    not row visibility, is what this role is for. ``False`` when the test
    database's role may not create roles at all.
    """
    try:
        admin.execute(text("SET createrole_self_grant = 'set, inherit'"))
        try:
            admin.execute(text(f"CREATE ROLE {name} NOLOGIN BYPASSRLS"))
        except DBAPIError:
            admin.rollback()
            admin.execute(text("SET createrole_self_grant = 'set, inherit'"))
            admin.execute(text(f"CREATE ROLE {name} NOLOGIN"))
    except DBAPIError:
        admin.rollback()
        return False
    admin.commit()
    return True


def test_a_non_owner_worker_drops_and_recreates_children_only_through_the_functions(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Retention runs as the BYPASSRLS worker, which owns nothing (audit A4-03).

    A non-owner cannot ``DROP`` or ``DETACH`` a partition directly; the definer
    drop functions are what make ``bi_retention`` possible, and they must
    refuse the DEFAULT partition (unreachable: the name is derived from a
    date) and be a no-op for an absent child.
    """
    engine = migrated_postgres_schema.app_engine
    schema_name = migrated_postgres_schema.schema_name
    role = f"bi_worker_{uuid4().hex[:12]}"
    functions = (
        "bi_ensure_month_partition(regclass, date)",
        "bi_ensure_year_partition(regclass, date)",
        "bi_drop_month_partition(regclass, date)",
        "bi_drop_year_partition(regclass, date)",
    )

    with engine.connect() as admin:
        if not _temporary_non_owner_role(admin, role):
            _require_or_skip_privileges(
                "the TEST_DATABASE_URL role cannot create a second role (CREATEROLE), "
                "so there is no non-owner to prove the definer functions for"
            )
    try:
        # The documented path for a worker role created AFTER the migration:
        # the owner grants USAGE and EXECUTE; it never owns a table.
        with engine.begin() as admin:
            admin.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO {role}'))
            for signature in functions:
                admin.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}"))

        worker_engine = create_engine(
            postgres_schema_url(
                engine.url.render_as_string(hide_password=False), schema_name, role=role
            )
        )
        try:
            with worker_engine.begin() as worker:
                assert worker.scalar(text("SELECT current_user")) == role
                created = worker.scalar(
                    text(
                        "SELECT bi_ensure_month_partition('bi_fact_loan_event', DATE '2025-12-01')"
                    )
                )
                yearly = worker.scalar(
                    text(
                        "SELECT bi_ensure_year_partition('bi_fact_position_eom', DATE '2025-03-01')"
                    )
                )
            assert created == "bi_fact_loan_event_y2025m12"
            assert yearly == "bi_fact_position_eom_y2025"

            for statement in (
                f"DROP TABLE {created}",
                f"ALTER TABLE bi_fact_loan_event DETACH PARTITION {created}",
                "DROP TABLE bi_fact_loan_event_default",
            ):
                with worker_engine.connect() as worker, pytest.raises(DBAPIError) as refused:
                    worker.execute(text(statement))
                assert "must be owner" in str(refused.value), statement

            with worker_engine.begin() as worker:
                dropped = worker.scalar(
                    text("SELECT bi_drop_month_partition('bi_fact_loan_event', DATE '2025-12-15')")
                )
                absent = worker.scalar(
                    text("SELECT bi_drop_month_partition('bi_fact_loan_event', DATE '2025-12-15')")
                )
                dropped_year = worker.scalar(
                    text("SELECT bi_drop_year_partition('bi_fact_position_eom', DATE '2025-09-09')")
                )
                recreated = worker.scalar(
                    text(
                        "SELECT bi_ensure_month_partition('bi_fact_loan_event', DATE '2025-12-01')"
                    )
                )
            assert (dropped, absent, dropped_year, recreated) == (True, False, True, created)
            state = _relation_state(
                migrated_postgres_schema,
                [created, yearly, "bi_fact_loan_event_default", "bi_fact_position_eom_default"],
            )
            assert set(state) == {
                created,
                "bi_fact_loan_event_default",
                "bi_fact_position_eom_default",
            }
            assert state[created] == ("r", True, True, 1)
        finally:
            worker_engine.dispose()
    finally:
        with engine.begin() as admin:
            admin.execute(text(f'REVOKE ALL ON SCHEMA "{schema_name}" FROM {role}'))
            for signature in functions:
                admin.execute(text(f"REVOKE ALL ON FUNCTION {signature} FROM {role}"))
            admin.execute(text(f"DROP ROLE {role}"))


def test_query_log_is_append_only_through_the_parent_and_on_a_partition(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    engine = migrated_postgres_schema.app_engine
    with engine.connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        connection.rollback()
        if role[0] or role[1]:
            pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")

    in_child = datetime(2026, 9, 10, 10, 0, tzinfo=UTC)
    in_default = datetime(2031, 1, 5, 10, 0, tzinfo=UTC)
    with engine.begin() as connection:
        _seed_tenant(connection)
        connection.execute(
            text("SELECT bi_ensure_month_partition('bi_query_log', :day)"),
            {"day": in_child.date()},
        )
        child_row = _insert_query_log(connection, in_child)
        default_row = _insert_query_log(connection, in_default)
        routed = {
            key: value
            for key, value in connection.execute(
                text("SELECT id::text, tableoid::regclass::text FROM bi_query_log")
            ).tuples()
        }
    assert routed == {
        child_row: "bi_query_log_y2026m09",
        default_row: "bi_query_log_default",
    }

    attempts = (
        "UPDATE bi_query_log SET row_count = 1",
        "DELETE FROM bi_query_log",
        "TRUNCATE bi_query_log",
        "UPDATE bi_query_log_y2026m09 SET row_count = 1",
        "DELETE FROM bi_query_log_y2026m09",
        "UPDATE bi_query_log_default SET row_count = 1",
        "DELETE FROM bi_query_log_default",
    )
    for statement in attempts:
        with (
            engine.connect() as connection,
            pytest.raises(DBAPIError) as refused,
            connection.begin(),
        ):
            _set_tenant(connection, ORG)
            connection.execute(text(statement))
        message = str(refused.value)
        assert "permission denied" in message or "append-only" in message, (
            f"{statement!r} was not refused as append-only: {message}"
        )

    with engine.begin() as connection:
        _set_tenant(connection, ORG)
        surviving = connection.scalar(text("SELECT count(*) FROM bi_query_log"))
    assert surviving == 2


def test_downgrade_removes_every_bi_object_and_upgrade_restores_them(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Down to before the BI chain, then back up through every revision in it.

    The downgrade half is the stronger one: it asserts that NO ``bi_`` relation
    and no partition function survives, runtime-created children included, so a
    revision that creates something without dropping it fails here rather than
    leaving an orphan on a real database. The upgrade half then has to reach
    ``LAST_BI_REVISION`` rather than ``REVISION``, because ``bi_fact_target``
    is created by the later revision and ``BI_TABLES`` names it.
    """
    config = alembic_config_for_app()
    schema_name = migrated_postgres_schema.schema_name
    engine = migrated_postgres_schema.app_engine
    with engine.begin() as connection:
        connection.execute(
            text("SELECT bi_ensure_month_partition('bi_fact_loan_event', DATE '2026-09-01')")
        )

    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    with engine.connect() as connection:
        leftover_relations = list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema_name AND c.relname LIKE 'bi\\_%' "
                    "AND c.relkind IN ('r', 'p')"
                ),
                {"schema_name": schema_name},
            ).scalars()
        )
        leftover_functions = list(
            connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = :schema_name AND p.proname LIKE 'bi\\_ensure%'"
                ),
                {"schema_name": schema_name},
            ).scalars()
        )
    assert leftover_relations == []
    assert leftover_functions == []

    command.upgrade(config, LAST_BI_REVISION)
    clear_database_caches()
    state = _relation_state(migrated_postgres_schema, list(BI_TABLES))
    assert set(state) == set(BI_TABLES)
    assert all(row[1] and row[2] and row[3] == 1 for row in state.values())
