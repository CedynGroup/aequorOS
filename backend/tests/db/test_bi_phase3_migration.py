"""Postgres parity for the Phase 3 BI tables, which nothing else covers.

``test_bi_foundation_migration.py`` iterates ``app/models/bi.py::BI_TABLES`` and
gives the marts four kinds of coverage no hermetic test can: the migrated
column's TYPE, LENGTH and nullability against the model's, the CHECK constraint
NAMES as sets, the values each migrated CHECK admits, and FORCE row-level
security. Phase 3 added eight tables in two other model modules, so none of them
was covered by any of that.

That is not a theoretical gap. It is exactly how audit finding A8-01 reached a
commit: the model and the migration AGREED on a column four characters too narrow
for the values copied into it, every parity test passed because both sides said
the same wrong thing, and SQLite ignores VARCHAR lengths entirely, so the hermetic
suite could not see it either. The defect was only findable by reading the two
against the columns the data comes FROM.

So this file covers the Phase 3 tables on the same four axes, deriving the table
list from the model modules' own tuples rather than restating it — a ninth table
added to either module is covered the moment it is declared.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text

from app.db.base import Base
from app.models.bi_content import BI_CONTENT_TABLES, BI_CONTENT_UNALTERABLE_TABLES
from app.models.bi_notifications import BI_NOTIFICATION_TABLES
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    migrated_postgres_schema,
    postgres_schema_url,
)

__all__ = ["migrated_postgres_schema", "postgres_schema_url"]

pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration tests.",
    ),
]

#: Derived, never restated: a table added to either module joins this list by
#: being declared, so it cannot be built without Postgres coverage.
PHASE3_TABLES: tuple[str, ...] = (*BI_CONTENT_TABLES, *BI_NOTIFICATION_TABLES)

_PG = sa.dialects.postgresql.dialect()


def _rendered(type_: sa.types.TypeEngine) -> str:
    return str(type_.compile(dialect=_PG))


def test_the_table_list_is_not_empty() -> None:
    """A parity suite over an empty list passes while proving nothing."""
    assert len(PHASE3_TABLES) == 8, PHASE3_TABLES
    assert all(name.startswith("bi_") for name in PHASE3_TABLES)


def test_every_phase3_table_exists_and_forces_row_level_security(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """RLS ENABLED is not enough: the table owner bypasses it without FORCE."""
    with migrated_postgres_schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policies p
                         WHERE p.schemaname = n.nspname AND p.tablename = c.relname
                           AND p.policyname = c.relname || '_tenant_isolation') AS tenant_policy
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = ANY(:names)
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name, "names": list(PHASE3_TABLES)},
        ).all()
    state = {row.relname: row for row in rows}
    assert set(state) == set(PHASE3_TABLES), (
        "a Phase 3 BI table is missing from the migrated schema: "
        f"{sorted(set(PHASE3_TABLES) - set(state))}"
    )
    unprotected = [
        name
        for name, row in state.items()
        if not (row.relrowsecurity and row.relforcerowsecurity and row.tenant_policy == 1)
    ]
    assert not unprotected, (
        "these Phase 3 tables are not ENABLE + FORCE row-level security under the "
        f"standard tenant policy, so one tenant could read another's: {sorted(unprotected)}"
    )


def test_migrated_columns_match_the_model_type_length_and_nullability(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """The A8-01 axis. A model widened without its migration fails only here."""
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in PHASE3_TABLES:
        model_table = Base.metadata.tables[table_name]
        migrated = {
            column["name"]: column
            for column in inspector.get_columns(
                table_name, schema=migrated_postgres_schema.schema_name
            )
        }
        for column in model_table.columns:
            actual = migrated.get(column.name)
            if actual is None:
                drift.append(f"{table_name}.{column.name}: missing from the migration")
                continue
            if _rendered(column.type) != _rendered(actual["type"]):
                drift.append(
                    f"{table_name}.{column.name}: model {_rendered(column.type)} "
                    f"!= migrated {_rendered(actual['type'])}"
                )
            if bool(column.nullable) != bool(actual["nullable"]):
                drift.append(
                    f"{table_name}.{column.name}: model "
                    f"{'NULL' if column.nullable else 'NOT NULL'} != migrated "
                    f"{'NULL' if actual['nullable'] else 'NOT NULL'}"
                )
        extra = sorted(set(migrated) - {column.name for column in model_table.columns})
        drift.extend(f"{table_name}.{name}: in the migration only" for name in extra)
    assert not drift, (
        "the Phase 3 BI models and migration 202609270072 disagree about these "
        "columns. The model is what the hermetic suite builds and the migration is "
        "what a real database has, so a difference is a latent Postgres-only "
        "failure — edit BOTH: " + "; ".join(drift)
    )


def test_migrated_check_constraints_are_exactly_the_models(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    inspector = inspect(migrated_postgres_schema.app_engine)
    drift: list[str] = []
    for table_name in PHASE3_TABLES:
        declared = {
            str(constraint.name)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, sa.CheckConstraint) and constraint.name
        }
        migrated = {
            str(constraint["name"])
            for constraint in inspector.get_check_constraints(
                table_name, schema=migrated_postgres_schema.schema_name
            )
            if constraint.get("name")
        }
        drift.extend(
            f"{table_name}: {name} is in the model only" for name in sorted(declared - migrated)
        )
        drift.extend(
            f"{table_name}: {name} is in the migration only" for name in sorted(migrated - declared)
        )
    assert not drift, (
        "the Phase 3 BI models and their migration declare different CHECK "
        "constraints: " + "; ".join(drift)
    )


def test_migrated_checks_admit_every_value_the_model_admits(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """A value added to a model tuple without a constraint migration fails here.

    It passes the whole hermetic suite and then refuses a real INSERT, which is
    the hazard ``202609200065`` records and the reason every migration in this
    chain pins its CHECK literals rather than importing them.
    """
    with migrated_postgres_schema.app_engine.connect() as connection:
        migrated = {
            key: value
            for key, value in connection.execute(
                text(
                    """
                    SELECT c.conname, pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = :schema_name AND c.contype = 'c'
                      AND t.relname = ANY(:names)
                    """
                ),
                {"schema_name": migrated_postgres_schema.schema_name, "names": list(PHASE3_TABLES)},
            ).tuples()
        }

    missing: list[str] = []
    for table_name in PHASE3_TABLES:
        for constraint in Base.metadata.tables[table_name].constraints:
            name = getattr(constraint, "name", None)
            sqltext = getattr(constraint, "sqltext", None)
            if not name or sqltext is None:
                continue
            assert name in migrated, f"{table_name}: CHECK {name} is not in the migrated schema"
            literal = str(sqltext)
            definition = migrated[str(name)]
            if " IN (" not in literal:
                continue
            for chunk in literal.split(" IN (")[1:]:
                values = chunk.rsplit(")", 1)[0]
                missing.extend(
                    f"{name}: {value}"
                    for value in (v.strip() for v in values.split(","))
                    if value.startswith("'") and value not in definition
                )
    assert not missing, f"migrated CHECKs reject model values: {missing}"


def test_the_append_only_table_refuses_update_and_delete(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """A dashboard's version history is evidence; the database refuses to rewrite it.

    Asserted at the STATEMENT level, which holds whether or not a row exists, so
    the test does not depend on seeding a whole tenant. The row-level trigger is
    the second guard behind this one.
    """
    assert BI_CONTENT_UNALTERABLE_TABLES, "no append-only Phase 3 table is declared"
    engine = migrated_postgres_schema.app_engine
    for table_name in BI_CONTENT_UNALTERABLE_TABLES:
        for statement in (
            f"UPDATE {table_name} SET version = version + 1",
            f"DELETE FROM {table_name}",
        ):
            with (
                engine.connect() as connection,
                pytest.raises(sa.exc.DBAPIError) as refused,
                connection.begin(),
            ):
                connection.execute(text("SET LOCAL app.organization_id = 'OR-DEM00001'"))
                connection.execute(text(statement))
            message = str(refused.value)
            assert "permission denied" in message or "append-only" in message, (
                f"{statement!r} was not refused: {message}"
            )
