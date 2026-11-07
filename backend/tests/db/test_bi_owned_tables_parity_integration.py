"""Model↔migration parity for EVERY BI-owned table, with the subject DERIVED.

The subject must include BI-owned tables without the ``bi_`` prefix, such as
``ai_commentary_drafts``; a prefix-only census would silently omit their column
and CHECK parity.

This module closes the CLASS rather than the instance. Its subject is not a
tuple anybody maintains; it is computed two ways and the two must agree:

* every table declared by a module under ``app.bi.models`` (found with
  ``pkgutil``, so a new model module joins by existing);
* every ``bi_``-prefixed table in ``Base.metadata`` (so a BI table declared in
  some other module joins by its name).

The union is the subject of the four parity axes below. A separate test pins
that the union of the four hand tuples equals the derived subject, so a new
table that is declared but not registered fails by name — and so does a
registered table that no module declares.

One module-scoped forward migration serves every test here: the axes are
read-only against ``head`` and the round trip is already proven elsewhere.
Postgres-gated on ``TEST_DATABASE_URL``.

This module's other-suite cases live in ``test_bi_owned_tables_parity.py``.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from collections.abc import Iterator
from types import ModuleType
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

import app.bi.models as models_package
from alembic import command
from app.bi.models.marts import MONTHLY_PARTITIONED_TABLES, YEARLY_PARTITIONED_TABLES
from app.db.base import Base
from tests.support.database import install_migration_engines, managed_engine
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

requires_postgres = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
_PG = postgresql.dialect()


def _bi_model_modules() -> tuple[ModuleType, ...]:
    """Every module under ``app.bi.models``, imported."""
    names = sorted(
        info.name
        for info in pkgutil.walk_packages(models_package.__path__, f"{models_package.__name__}.")
    )
    return tuple(importlib.import_module(name) for name in names)


def _tables_declared_by(module: ModuleType) -> frozenset[str]:
    return frozenset(
        value.__tablename__
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, Base)
        and value.__module__ == module.__name__
        and getattr(value, "__tablename__", None)
    )


BI_MODEL_MODULES: tuple[ModuleType, ...] = _bi_model_modules()
DECLARED_BY_BI_MODULES: frozenset[str] = frozenset().union(
    *(_tables_declared_by(module) for module in BI_MODEL_MODULES)
)
PREFIXED_IN_METADATA: frozenset[str] = frozenset(
    name for name in Base.metadata.tables if name.startswith("bi_")
)
#: The subject of every axis below.
BI_OWNED_TABLES: tuple[str, ...] = tuple(sorted(DECLARED_BY_BI_MODULES | PREFIXED_IN_METADATA))
#: Partition parents are ``relkind = 'p'``; everything else is a plain table.
PARTITIONED: frozenset[str] = frozenset((*MONTHLY_PARTITIONED_TABLES, *YEARLY_PARTITIONED_TABLES))


def _rendered(type_: sa.types.TypeEngine) -> str:
    return str(type_.compile(dialect=_PG))


@pytest.fixture(scope="module")
def bi_schema(request: pytest.FixtureRequest) -> Iterator[MigratedPostgresSchema]:
    """One forward migration to ``head`` for the whole module; dropped at the end."""
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_owned_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    monkeypatch = pytest.MonkeyPatch()
    admin_engine = managed_engine(
        request, test_database_url, isolation_level="AUTOCOMMIT", schema=schema_name
    )
    app_engine = managed_engine(request, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        install_migration_engines(monkeypatch, app_engine)
        command.upgrade(alembic_config_for_app(), "head")
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@requires_postgres
def test_every_bi_owned_table_exists_and_forces_row_level_security(
    bi_schema: MigratedPostgresSchema,
) -> None:
    with bi_schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policies p
                         WHERE p.schemaname = n.nspname AND p.tablename = c.relname
                           AND p.policyname = c.relname || '_tenant_isolation') AS tenant_policy
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = ANY(:names)
                  AND c.relkind IN ('r', 'p')
                """
            ),
            {"schema_name": bi_schema.schema_name, "names": list(BI_OWNED_TABLES)},
        ).all()
    state = {row.relname: row for row in rows}
    missing = sorted(set(BI_OWNED_TABLES) - set(state))
    assert not missing, f"BI-owned tables absent from the migrated schema: {missing}"
    wrong_kind = [
        name for name, row in state.items() if row.relkind != ("p" if name in PARTITIONED else "r")
    ]
    assert not wrong_kind, f"partitioned/plain disagreement with the model: {wrong_kind}"
    unprotected = sorted(
        name
        for name, row in state.items()
        if not (row.relrowsecurity and row.relforcerowsecurity and row.tenant_policy == 1)
    )
    assert not unprotected, (
        "not ENABLE + FORCE row-level security under the standard tenant policy, so one "
        f"tenant could read another's rows: {unprotected}"
    )


@requires_postgres
def test_migrated_columns_match_the_model_type_length_and_nullability(
    bi_schema: MigratedPostgresSchema,
) -> None:
    """The A8-01 axis, over the derived subject.

    Both sides compile with the same PostgreSQL dialect, so ``VARCHAR(24)`` from
    the model is compared to ``VARCHAR(24)`` reflected from the database and
    there is no hand-written type map to fall behind.
    """
    inspector = inspect(bi_schema.app_engine)
    drift: list[str] = []
    for table_name in BI_OWNED_TABLES:
        model = Base.metadata.tables[table_name]
        migrated = {
            column["name"]: column
            for column in inspector.get_columns(table_name, schema=bi_schema.schema_name)
        }
        for column in model.columns:
            actual = migrated.get(column.name)
            if actual is None:
                drift.append(f"{table_name}.{column.name}: in the model, not in the migration")
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
        drift.extend(
            f"{table_name}.{name}: in the migration, not in the model"
            for name in sorted(set(migrated) - {column.name for column in model.columns})
        )
    assert not drift, (
        "the BI models and their migrations disagree about these columns. The model is "
        "what the hermetic suite builds on SQLite (which ignores VARCHAR lengths) and the "
        "migration is what a real database has, so a difference here is a Postgres-only "
        "failure inside a tenant's nightly build — edit BOTH: " + "; ".join(drift)
    )


@requires_postgres
def test_migrated_check_constraints_are_exactly_the_models(
    bi_schema: MigratedPostgresSchema,
) -> None:
    inspector = inspect(bi_schema.app_engine)
    drift: list[str] = []
    for table_name in BI_OWNED_TABLES:
        declared = {
            str(constraint.name)
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, sa.CheckConstraint) and constraint.name
        }
        migrated = {
            str(constraint["name"])
            for constraint in inspector.get_check_constraints(
                table_name, schema=bi_schema.schema_name
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
        "the BI models and their migrations declare different CHECK constraints: "
        + "; ".join(drift)
    )


@requires_postgres
def test_migrated_checks_admit_every_value_the_model_admits(
    bi_schema: MigratedPostgresSchema,
) -> None:
    """A value added to a model tuple without a constraint migration passes the
    whole hermetic suite and then refuses a real INSERT — the ``202609200065``
    hazard, and the reason every migration in this chain pins its literals."""
    with bi_schema.app_engine.connect() as connection:
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
                {"schema_name": bi_schema.schema_name, "names": list(BI_OWNED_TABLES)},
            ).tuples()
        }

    compared = 0
    missing: list[str] = []
    for table_name in BI_OWNED_TABLES:
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
                # The IN-list closes at the FIRST ')': its members are bare quoted
                # literals. Taking the LAST one (the older suites' idiom) swallows
                # the closing paren of an enclosing NOT (...) and reports a
                # phantom value — which is how this parser convicted
                # ck_ai_commentary_drafts_no_output on a schema with no drift.
                values = chunk.split(")", 1)[0]
                for value in (v.strip() for v in values.split(",")):
                    if not value.startswith("'"):
                        continue
                    compared += 1
                    if value not in definition:
                        missing.append(f"{name}: {value}")
    assert compared > 0, "no IN (...) vocabulary was compared; the axis is vacuous"
    assert not missing, f"migrated CHECKs reject model values: {missing}"
