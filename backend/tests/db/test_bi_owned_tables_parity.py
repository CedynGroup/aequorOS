"""Model↔migration parity for EVERY BI-owned table, with the subject DERIVED.

Audit A360-4 finding M1. Three suites compared the migrated BI schema to the
models, and each took its subject from a hand-maintained tuple in one model
module: ``test_bi_foundation_migration.py`` iterates ``app/models/bi.py::BI_TABLES``
(16 tables), ``test_bi_phase3_migration.py`` iterates ``BI_CONTENT_TABLES`` +
``BI_NOTIFICATION_TABLES`` (8), and ``test_bi_migration_structural_parity.py`` is
the only one that also names ``BI_COMMENTARY_TABLES`` — for primary-key order,
composite foreign keys and indexes ONLY. So ``ai_commentary_drafts``, the 25th
BI table, had no coverage at all on the two axes that bit this build before:
column type / length / nullability, and CHECK constraints. A column four
characters too narrow there would have passed every gate, exactly as A8-01 did.

This module closes the CLASS rather than the instance. Its subject is not a
tuple anybody maintains; it is computed two ways and the two must agree:

* every table declared by a model module whose name is ``bi`` or ``bi_*`` under
  ``app.models`` (found with ``pkgutil``, so a new ``app/models/bi_x.py`` joins
  by existing);
* every ``bi_``-prefixed table in ``Base.metadata`` (so a BI table declared in
  some other module joins by its name).

The union is the subject of the four parity axes below. A separate test pins
that the union of the four hand tuples equals the derived subject, so a 26th
table that is declared but not registered fails by name — and so does a
registered table that no module declares.

One module-scoped forward migration serves every test here: the axes are
read-only against ``head`` and the round trip is already proven elsewhere.
Postgres-gated on ``TEST_DATABASE_URL``.
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
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql

import app.models as models_package
from alembic import command
from app.db.base import Base
from app.models.bi import BI_TABLES, MONTHLY_PARTITIONED_TABLES, YEARLY_PARTITIONED_TABLES
from app.models.bi_commentary import BI_COMMENTARY_TABLES
from app.models.bi_content import BI_CONTENT_TABLES
from app.models.bi_notifications import BI_NOTIFICATION_TABLES
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)

_PG = postgresql.dialect()


# --- the subject, derived -----------------------------------------------------


def _bi_model_modules() -> tuple[ModuleType, ...]:
    """Every ``app.models.bi`` / ``app.models.bi_*`` module, imported."""
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(models_package.__path__)
        if info.name == "bi" or info.name.startswith("bi_")
    )
    return tuple(importlib.import_module(f"app.models.{name}") for name in names)


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

#: What the three older suites take their subjects from, unioned.
HAND_REGISTERED: frozenset[str] = frozenset(
    (*BI_TABLES, *BI_CONTENT_TABLES, *BI_NOTIFICATION_TABLES, *BI_COMMENTARY_TABLES)
)

#: Partition parents are ``relkind = 'p'``; everything else is a plain table.
PARTITIONED: frozenset[str] = frozenset((*MONTHLY_PARTITIONED_TABLES, *YEARLY_PARTITIONED_TABLES))


def _rendered(type_: sa.types.TypeEngine) -> str:
    return str(type_.compile(dialect=_PG))


@pytest.fixture(scope="module")
def bi_schema() -> Iterator[MigratedPostgresSchema]:
    """One forward migration to ``head`` for the whole module; dropped at the end."""
    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_owned_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    monkeypatch = pytest.MonkeyPatch()
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


# --- the subject is complete and the hand tuples agree with it ------------------


def test_the_subject_is_derived_from_the_model_modules_and_the_prefix() -> None:
    """Both derivations are non-empty, and the module scan found more than one
    module — a scan that silently matched ``bi.py`` alone would be the Phase 3
    blind spot again."""
    assert len(BI_MODEL_MODULES) >= 4, [module.__name__ for module in BI_MODEL_MODULES]
    assert {module.__name__.rsplit(".", 1)[1] for module in BI_MODEL_MODULES} >= {
        "bi",
        "bi_commentary",
        "bi_content",
        "bi_notifications",
    }
    # 24 since `202609290080` dropped `bi_reconciliation_results` (BI carries no
    # reconciliation verdict — founder's decision of 2026-09-29). The floor is a
    # coarse "the scan found the tables at all" guard, not a census: the exact
    # membership is proven in both directions by the next test, against the hand
    # tuples. Move this number only alongside a migration that adds or drops a
    # BI-owned table, and say which in the comment.
    assert len(BI_OWNED_TABLES) >= 24, BI_OWNED_TABLES
    # The one deliberately unprefixed BI table is in the subject BY DECLARATION,
    # which is the whole point of deriving from modules as well as from the prefix.
    assert "ai_commentary_drafts" in DECLARED_BY_BI_MODULES
    assert "ai_commentary_drafts" not in PREFIXED_IN_METADATA
    assert "ai_commentary_drafts" in BI_OWNED_TABLES


def test_every_bi_owned_table_is_registered_in_exactly_one_hand_tuple() -> None:
    """The three older suites read the tuples; this is what keeps them honest.

    Fails by name in both directions: a table a ``bi*`` module declares (or a
    ``bi_``-named table anywhere) that no tuple registers — the 26th-table case
    — and a tuple entry that no module declares.
    """
    unregistered = sorted(set(BI_OWNED_TABLES) - HAND_REGISTERED)
    undeclared = sorted(HAND_REGISTERED - set(BI_OWNED_TABLES))
    assert not unregistered, (
        "these BI-owned tables are registered in NO model tuple, so the suites that "
        "iterate BI_TABLES / BI_CONTENT_TABLES / BI_NOTIFICATION_TABLES / "
        f"BI_COMMENTARY_TABLES do not see them: {unregistered}"
    )
    assert not undeclared, f"these tuple entries name no declared table: {undeclared}"
    tuples = (BI_TABLES, BI_CONTENT_TABLES, BI_NOTIFICATION_TABLES, BI_COMMENTARY_TABLES)
    counted = [name for name in BI_OWNED_TABLES if sum(name in tuple_ for tuple_ in tuples) != 1]
    assert not counted, f"registered in more or fewer than one tuple: {counted}"


def test_the_check_axis_is_not_vacuous_for_the_unprefixed_table() -> None:
    """A CHECK parity test over a table with no CHECKs passes while proving nothing.
    The commentary table pins a status vocabulary, so it must have at least one."""
    declared = [
        constraint
        for constraint in Base.metadata.tables["ai_commentary_drafts"].constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name
    ]
    assert declared, "ai_commentary_drafts declares no named CHECK constraint"


# --- axis 1: every table exists and forces RLS under the tenant policy ---------


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


# --- axis 2: column type, length, precision, scale, nullability ----------------


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


# --- axis 3: CHECK constraint names are the same set on both sides -------------


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


# --- axis 4: every value a model CHECK admits, the migrated CHECK admits -------


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
