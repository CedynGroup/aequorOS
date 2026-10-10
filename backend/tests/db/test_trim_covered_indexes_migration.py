"""Proof for the index trim (``202610100087``).

The migration and the models must agree on which indexes exist: the hermetic
suite builds its schema with ``create_all`` from the models, while every real
database gets its schema from the migration chain. So the trim is proven from
both sides:

* no model still declares a dropped index, so a fresh ``create_all`` schema
  matches a migrated one;
* on a migrated Postgres schema the indexes are gone at head, come back with
  their original columns on downgrade, and go again on the next upgrade.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest
from sqlalchemy import UniqueConstraint, text

import app.models
from alembic import command
from app.db.base import Base
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

PREVIOUS_REVISION = "202610080086"
_ = app.models  # Registers every table on Base.metadata.


class _TrimMigration(Protocol):
    COVERED_INDEXES: tuple[tuple[str, str, tuple[str, ...], str], ...]


def _load_migration() -> _TrimMigration:
    """The migration module, by path: ``alembic/versions`` is not a package and
    the file name starts with a digit, so it cannot be imported by name."""
    path = (
        Path(__file__).parents[2] / "alembic" / "versions" / "202610100087_trim_covered_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("trim_covered_indexes_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(_TrimMigration, module)


COVERED = _load_migration().COVERED_INDEXES
DROPPED = tuple((table, index, columns) for table, index, columns, _covering in COVERED)


def _indexes(schema: MigratedPostgresSchema) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``index -> (table, key columns)`` for every dropped index still present."""
    with schema.app_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT i.relname, t.relname,
                       array_agg(a.attname ORDER BY k.ordinality)
                FROM pg_index x
                JOIN pg_class i ON i.oid = x.indexrelid
                JOIN pg_class t ON t.oid = x.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                CROSS JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                WHERE n.nspname = :schema AND i.relname = ANY(:names) AND x.indisvalid
                GROUP BY i.relname, t.relname
                """
            ),
            {"schema": schema.schema_name, "names": [index for _table, index, _cols in DROPPED]},
        )
        entries = cast(list[tuple[str, str, list[str]]], rows.tuples().all())
        return {index: (table, tuple(columns)) for index, table, columns in entries}


def test_no_model_declares_a_dropped_index() -> None:
    declared = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert declared.isdisjoint(index for _table, index, _columns in DROPPED)
    for table, _index, columns, covering in COVERED:
        constraint = next(
            item for item in Base.metadata.tables[table].constraints if item.name == covering
        )
        assert isinstance(constraint, UniqueConstraint)
        assert tuple(column.name for column in constraint.columns)[: len(columns)] == columns


@pytest.mark.committing_db
@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_indexes_are_dropped_at_head_and_restored_by_downgrade(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    assert _indexes(migrated_postgres_schema) == {}
    with migrated_postgres_schema.app_engine.connect() as connection:
        for table, _index, columns, covering in COVERED:
            row = connection.execute(
                text(
                    """
                    SELECT x.indisunique, x.indisvalid, x.indpred IS NULL,
                           array_agg(a.attname ORDER BY k.ordinality)
                    FROM pg_index x
                    JOIN pg_class i ON i.oid = x.indexrelid
                    JOIN pg_class t ON t.oid = x.indrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    CROSS JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                    WHERE n.nspname = :schema AND t.relname = :table AND i.relname = :covering
                    GROUP BY x.indisunique, x.indisvalid, x.indpred
                    """
                ),
                {
                    "schema": migrated_postgres_schema.schema_name,
                    "table": table,
                    "covering": covering,
                },
            ).one()
            assert row[0] and row[1] and row[2]
            assert tuple(cast(list[str], row[3]))[: len(columns)] == columns

    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    assert _indexes(migrated_postgres_schema) == {
        index: (table, columns) for table, index, columns in DROPPED
    }

    command.upgrade(config, "head")
    clear_database_caches()
    assert _indexes(migrated_postgres_schema) == {}
