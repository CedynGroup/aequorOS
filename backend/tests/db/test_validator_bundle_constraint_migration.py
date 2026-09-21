"""Every role bundle the code can issue must be a value the database accepts.

This suite exists because of a live 500. ``RoleBundle.VALIDATOR`` was added in
code, offered in the grant composer, and allowed by the evaluator and the
separation-of-duties decision — and then the INSERT hit
``ck_authorization_bindings_role_bundle``, which had last been written before
the bundle existed.

The hermetic suite could not catch it: it builds its schema with
``Base.metadata.create_all``, and the model derives that constraint from
``tuple(RoleBundle)``, so a freshly created database always agrees with the
enum while a MIGRATED one may not. The disagreement only exists on real
databases, so the guard has to run against one.

It is deliberately written against the enum rather than a literal list: a new
bundle added without a constraint migration fails here instead of in a user's
face.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from app.core.authorization import RoleBundle
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = [
    pytest.mark.committing_db,
    # The whole point is a MIGRATED database, so without one there is nothing
    # to assert — the same gate the rest of the Postgres migration suite uses.
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE_URL") is None,
        reason="TEST_DATABASE_URL is required for Postgres migration smoke tests.",
    ),
]

_CONSTRAINT = "ck_authorization_bindings_role_bundle"


def test_the_bindings_constraint_accepts_every_role_bundle(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    with migrated_postgres_schema.app_engine.connect() as connection:
        definition = connection.execute(
            # Scoped to THIS migrated schema: the disposable test schemas are
            # created alongside each other, so the constraint name alone is not
            # unique across the database.
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema"
            ),
            {"name": _CONSTRAINT, "schema": migrated_postgres_schema.schema_name},
        ).scalar_one()

    missing = [
        bundle.value for bundle in RoleBundle if f"'{bundle.value}'" not in definition
    ]
    assert not missing, (
        f"{_CONSTRAINT} rejects {missing}. A new RoleBundle needs a migration "
        "widening this constraint — the model derives it from the enum, so the "
        "hermetic suite will not catch this."
    )
