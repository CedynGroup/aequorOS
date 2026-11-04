"""Proof for the index trim (``202610100087``).

The migration and the models must agree on which indexes exist: the hermetic
suite builds its schema with ``create_all`` from the models, while every real
database gets its schema from the migration chain. So the trim is proven from
both sides:

* no model still declares a dropped index, so a fresh ``create_all`` schema
  matches a migrated one;
* on a migrated Postgres schema the indexes are gone at head, come back with
  their original columns on downgrade, and go again on the next upgrade.

This module's other-suite cases live in

``test_trim_covered_indexes_migration_integration.py``.
"""

from __future__ import annotations

from tests.db.test_trim_covered_indexes_migration_integration import COVERED, DROPPED

__all__ = ["migrated_postgres_schema"]
from sqlalchemy import UniqueConstraint

from app.db.base import Base
from tests.db.test_postgres_migrations_integration import (
    migrated_postgres_schema,
)


def test_no_model_declares_a_dropped_index() -> None:
    declared = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert declared.isdisjoint(index for _table, index, _columns in DROPPED)
    for table, _index, columns, covering in COVERED:
        constraint = next(
            item for item in Base.metadata.tables[table].constraints if item.name == covering
        )
        assert isinstance(constraint, UniqueConstraint)
        assert tuple(column.name for column in constraint.columns)[: len(columns)] == columns
