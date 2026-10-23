"""Deployed column representations agree with the ORM's storage contract."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base
from tests.db.test_bank_organization_integrity import pytestmark
from tests.db.test_canonical_migration_structural_parity import bank_integrity_schema
from tests.db.test_postgres_migrations import MigratedPostgresSchema

__all__ = ["bank_integrity_schema", "pytestmark"]


def test_model_column_types_match_migrations_with_documented_json_storage(
    bank_integrity_schema: MigratedPostgresSchema,
) -> None:
    inspector = sa.inspect(bank_integrity_schema.app_engine)
    dialect = bank_integrity_schema.app_engine.dialect
    differences: list[str] = []
    for name, table in Base.metadata.tables.items():
        for column in inspector.get_columns(name, schema=bank_integrity_schema.schema_name):
            model_type = table.c[column["name"]].type
            migrated_type = column["type"]
            # CODEBASE_CONVENTIONS §1: generic JSON supports hermetic SQLite;
            # PostgreSQL migrations intentionally use JSONB for these documents.
            if isinstance(model_type, sa.JSON) and isinstance(migrated_type, JSONB):
                continue
            declared = model_type.compile(dialect=dialect)
            deployed = migrated_type.compile(dialect=dialect)
            if declared != deployed:
                differences.append(f"{name}.{column['name']}: {declared} != {deployed}")
    assert not differences, "\n".join(differences)
