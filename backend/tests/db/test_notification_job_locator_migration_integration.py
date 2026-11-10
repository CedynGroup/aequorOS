"""The #186 locator migration is add-only and never backfills.

The typed source locator on notifications, and the locator plus enqueue
provenance on jobs, arrive as nullable columns with CHECK-pinned vocabularies
matching ``app/core/authorization.py``. Legacy rows keep all-NULL locators —
the read-path commits treat a NULL locator as hidden, so the migration must
not invent one by inference (mission D9).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.authorization import InstitutionScope, Module, Sensitivity
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    # The whole point is a MIGRATED database, so without one there is nothing
    # to assert — the same gate the rest of the Postgres migration suite uses.
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]

PREVIOUS = "202611070113"
ORG = "OR-NLJM0001"

NOTIFICATION_LOCATOR_COLUMNS = {
    "institution_scope",
    "bank_id",
    "module",
    "sensitivity",
}
JOB_PROVENANCE_COLUMNS = {
    "enqueued_by_principal",
    "enqueued_action",
    "enqueue_decision_reference",
}
JOB_LOCATOR_COLUMNS = {
    "institution_scope",
    "module",
    "sensitivity",
} | JOB_PROVENANCE_COLUMNS
INDEXES = {
    "ix_notifications_org_recipient_read_locator",
    "ix_jobs_org_status_run_locator",
}

_VOCABULARIES = [
    ("notifications", "institution_scope", InstitutionScope),
    ("notifications", "module", Module),
    ("notifications", "sensitivity", Sensitivity),
    ("jobs", "institution_scope", InstitutionScope),
    ("jobs", "module", Module),
    ("jobs", "sensitivity", Sensitivity),
]


def _columns(schema: MigratedPostgresSchema, table: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(schema.app_engine).get_columns(table, schema=schema.schema_name)
    }


def _check_expression(schema: MigratedPostgresSchema, table: str, constraint: str) -> str:
    with schema.app_engine.connect() as connection:
        expression = connection.execute(
            text(
                "SELECT pg_get_expr(c.conbin, c.conrelid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema "
                "AND t.relname = :table AND c.contype = 'c'"
            ),
            {
                "name": constraint,
                "schema": schema.schema_name,
                "table": table,
            },
        ).scalar_one()
    assert isinstance(expression, str)
    return expression


def _tenant(connection: Connection, organization: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :org, true)"), {"org": organization}
    )


def _seed_legacy_rows(schema: MigratedPostgresSchema) -> None:
    """Insert one legacy notification and one legacy job with all-NULL locators."""
    now = datetime.now(UTC).isoformat()
    with schema.app_engine.begin() as connection:
        _tenant(connection, ORG)
        connection.execute(
            text(
                "INSERT INTO notifications (id, organization_id, type, severity, title, body, "
                "created_at) VALUES (:id, :org, 'reporting.deadline.due_soon_7', 'warning', "
                "'Deadline approaching', 'A return is due soon.', :now)"
            ),
            {"id": str(uuid4()), "org": ORG, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, organization_id, job_type, status, queued_at) "
                "VALUES (:id, :org, 'derive_facts', 'queued', :now)"
            ),
            {"id": str(uuid4()), "org": ORG, "now": now},
        )


def test_the_migration_adds_the_locator_and_provenance_columns(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    assert _columns(schema, "notifications") >= NOTIFICATION_LOCATOR_COLUMNS
    assert _columns(schema, "jobs") >= JOB_LOCATOR_COLUMNS


def test_the_vocabulary_constraints_accept_every_enum_value_and_reject_unknowns(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    for table, column, vocabulary in _VOCABULARIES:
        expression = _check_expression(schema, table, f"ck_{table}_{column}")
        with schema.app_engine.connect() as connection:
            for value in ("", "bogus", "organization_maybe"):
                assert not connection.execute(
                    text(
                        f"SELECT ({expression}) IS NOT FALSE "
                        f'FROM (SELECT CAST(:value AS text) AS "{column}") AS candidate'
                    ),
                    {"value": value},
                ).scalar_one(), f"ck_{table}_{column} accepts {value!r}"
            missing = [
                member.value
                for member in vocabulary
                if not connection.execute(
                    text(
                        f"SELECT ({expression}) IS NOT FALSE "
                        f'FROM (SELECT CAST(:value AS text) AS "{column}") AS candidate'
                    ),
                    {"value": member.value},
                ).scalar_one()
            ]
        assert not missing, (
            f"ck_{table}_{column} rejects {missing}. A new value added to the enum needs a "
            "migration widening this constraint — the model derives it from the enum, so the "
            "hermetic suite will not catch this."
        )


def test_legacy_rows_keep_all_null_locators_after_a_round_trip(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """No inferred backfill: pre-existing rows stay all-NULL through downgrade/upgrade."""
    schema = forward_migrated_postgres_schema
    _seed_legacy_rows(schema)

    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS)
    clear_database_caches()
    # jobs keeps its pre-existing bank_id column; only the added columns go.
    assert not (_columns(schema, "notifications") & NOTIFICATION_LOCATOR_COLUMNS)
    assert not (_columns(schema, "jobs") & JOB_LOCATOR_COLUMNS)

    command.upgrade(config, "head")
    clear_database_caches()
    with schema.app_engine.begin() as connection:
        _tenant(connection, ORG)
        notification_locators = (
            connection.execute(
                text(
                    "SELECT institution_scope, bank_id, module, sensitivity FROM notifications "
                    "WHERE organization_id = :org"
                ),
                {"org": ORG},
            )
            .tuples()
            .all()
        )
        job_locators = (
            connection.execute(
                text(
                    "SELECT institution_scope, bank_id, module, sensitivity, "
                    "enqueued_by_principal, enqueued_action, enqueue_decision_reference FROM jobs "
                    "WHERE organization_id = :org"
                ),
                {"org": ORG},
            )
            .tuples()
            .all()
        )
    assert notification_locators == [(None, None, None, None)]
    assert job_locators == [(None, None, None, None, None, None, None)]


def test_the_read_filter_indexes_round_trip(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    assert schema.indexes(INDEXES) == INDEXES

    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS)
    clear_database_caches()
    assert not schema.indexes(INDEXES)

    command.upgrade(config, "head")
    clear_database_caches()
    assert schema.indexes(INDEXES) == INDEXES


def test_the_worker_functions_stamp_their_non_tenant_provenance(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The SQL enqueue functions are producers too: they stamp the worker principal.

    These functions insert jobs directly in SQL, bypassing ``job_queue.enqueue``;
    without the stamp their rows would look like legacy rows (all-NULL) forever
    instead of explicitly non-tenant (mission D9).
    """
    schema = forward_migrated_postgres_schema
    with schema.app_engine.connect() as connection:
        for function, action in (
            ("aqs_worker_seed_ticks", "scheduler.seed_tick"),
            ("aqs_worker_enqueue_platform_daily", "'platform.daily:' || p_kind"),
        ):
            source = connection.execute(
                text(
                    "SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n "
                    "ON n.oid = p.pronamespace WHERE p.proname = :function "
                    "AND n.nspname = :schema"
                ),
                {"function": function, "schema": schema.schema_name},
            ).scalar_one()
            assert isinstance(source, str)
            assert "enqueued_by_principal" in source, f"{function} stamps no provenance"
            assert "'worker'" in source, f"{function} does not stamp the worker principal"
            assert action in source, f"{function} does not stamp its action {action!r}"


def test_the_vocabulary_constraints_reject_invalid_writes(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """A located row with a value outside the enum cannot be stored."""
    schema = forward_migrated_postgres_schema
    now = datetime.now(UTC).isoformat()
    with schema.app_engine.begin() as connection:
        _tenant(connection, ORG)
        with pytest.raises(IntegrityError, match="ck_jobs_module"):
            connection.execute(
                text(
                    "INSERT INTO jobs (id, organization_id, job_type, status, queued_at, "
                    "institution_scope, bank_id, module, sensitivity, enqueued_by_principal, "
                    "enqueued_action) VALUES (:id, :org, 'derive_facts', 'queued', :now, "
                    "'institution', 'BK-NLJM0001', 'bogus', 'restricted', 'user:u1', "
                    "'risk:configure')"
                ),
                {"id": str(uuid4()), "org": ORG, "now": now},
            )
