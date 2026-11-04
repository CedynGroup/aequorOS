"""The migrated data-scope constraint agrees with the Credit-only grant API."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from alembic import command
from app.core.authorization import DataScope, ModuleScope
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required"),
]


def _constraint_expression(schema: MigratedPostgresSchema) -> str | None:
    with schema.app_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT pg_get_expr(c.conbin, c.conrelid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = 'ck_authorization_bindings_narrowed_module' "
                "AND n.nspname = :schema AND t.relname = 'authorization_bindings'"
            ),
            {"schema": schema.schema_name},
        ).scalar_one_or_none()


def test_credit_only_constraint_upgrade_and_downgrade(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    config = alembic_config_for_app()
    command.downgrade(config, "202609300082")
    assert _constraint_expression(schema) is None
    command.upgrade(config, "head")
    expression = _constraint_expression(schema)
    assert expression is not None
    with schema.app_engine.connect() as connection:
        for module in ModuleScope:
            for kind in DataScope:
                accepted = connection.scalar(
                    text(
                        f"SELECT ({expression}) IS TRUE FROM "
                        "(SELECT CAST(:module AS text) AS module_scope, "
                        "CAST(:kind AS text) AS data_scope_kind) AS candidate"
                    ),
                    {"module": module.value, "kind": kind.value},
                )
                assert accepted == (kind == DataScope.ALL or module == ModuleScope.CREDIT)
    command.downgrade(config, "202609300082")
    assert _constraint_expression(schema) is None
    command.upgrade(config, "head")
    assert _constraint_expression(schema) is not None
