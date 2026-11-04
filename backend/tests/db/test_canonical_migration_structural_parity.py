"""Canonical model contracts must exist in the schema a deployment receives."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from app.db.base import Base
from tests.db.test_bank_organization_integrity import pytestmark
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    migrate_isolated_postgres_schema,
)

__all__ = ["bank_integrity_schema", "pytestmark"]
TABLES = (
    *tuple(name for name in Base.metadata.tables if name.startswith("canonical_")),
    "ingestion_batches",
    "lineage_records",
)


@pytest.fixture(scope="module")
def bank_integrity_schema() -> Iterator[MigratedPostgresSchema]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


def test_every_canonical_foreign_key_matches_its_complete_migrated_signature(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    for name in TABLES:
        model = {
            (
                tuple(element.parent.name for element in fk.elements),
                fk.referred_table.name,
                tuple(element.column.name for element in fk.elements),
                fk.ondelete,
                fk.onupdate,
                fk.deferrable,
                fk.initially,
            )
            for fk in Base.metadata.tables[name].foreign_key_constraints
        }
        migrated = {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk.get("options", {}).get("ondelete"),
                fk.get("options", {}).get("onupdate"),
                fk.get("options", {}).get("deferrable"),
                fk.get("options", {}).get("initially"),
            )
            for fk in inspector.get_foreign_keys(name, schema=bank_integrity_schema.schema_name)
        }
        assert model == migrated, f"{name}: model {model} != migrated {migrated}"


def test_canonical_primary_and_unique_keys_match_the_migrated_schema(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    for name in TABLES:
        table = Base.metadata.tables[name]
        assert [column.name for column in table.primary_key] == inspector.get_pk_constraint(
            name, schema=bank_integrity_schema.schema_name
        ).get("constrained_columns"), name
        model = {
            tuple(column.name for column in key.columns)
            for key in table.constraints
            if isinstance(key, sa.UniqueConstraint)
        }
        migrated = {
            tuple(key["column_names"])
            for key in inspector.get_unique_constraints(
                name, schema=bank_integrity_schema.schema_name
            )
        }
        assert model == migrated, name


def test_every_declared_canonical_index_exists_in_the_migrated_schema(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    for name in TABLES:
        declared = {index.name for index in Base.metadata.tables[name].indexes}
        migrated = {
            index["name"]
            for index in inspector.get_indexes(name, schema=bank_integrity_schema.schema_name)
        }
        assert declared <= migrated, (name, declared - migrated)
