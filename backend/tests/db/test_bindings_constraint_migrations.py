"""Every enum value the code can store in a binding must be one the database accepts.

This suite exists because of a live 500. ``RoleBundle.VALIDATOR`` was added in
code, offered in the grant composer, and allowed by the evaluator and the
separation-of-duties decision — and then the INSERT hit
``ck_authorization_bindings_role_bundle``, which had last been written before
the bundle existed. The module vocabulary has the same shape:
``ck_authorization_bindings_module_scope`` was written from a literal list by
the foundation migration, so a module added to ``ModuleScope`` needs its own
widening migration (``202609200066`` for ``credit`` and ``institution``).

The hermetic suite could not catch either: it builds its schema with
``Base.metadata.create_all``, and the model derives these constraints from
``tuple(RoleBundle)`` and ``tuple(ModuleScope)``, so a freshly created database
always agrees with the enum while a MIGRATED one may not. The disagreement only
exists on real databases, so the guard has to run against one.

It is deliberately written against the enums rather than literal lists: a new
value added without a constraint migration fails here instead of in a user's
face.
"""

from __future__ import annotations

import os
from enum import StrEnum

import pytest
from sqlalchemy import text

from app.core.authorization import ModuleScope, RoleBundle
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


@pytest.mark.parametrize(
    ("constraint", "vocabulary"),
    [
        ("ck_authorization_bindings_role_bundle", RoleBundle),
        ("ck_authorization_bindings_module_scope", ModuleScope),
    ],
    ids=["role_bundle", "module_scope"],
)
def test_the_bindings_constraint_accepts_every_vocabulary_value(
    migrated_postgres_schema: MigratedPostgresSchema,
    constraint: str,
    vocabulary: type[StrEnum],
) -> None:
    with migrated_postgres_schema.app_engine.connect() as connection:
        expression = connection.execute(
            # Scoped to THIS migrated schema: the disposable test schemas are
            # created alongside each other, so the constraint name alone is not
            # unique across the database.
            text(
                "SELECT pg_get_expr(c.conbin, c.conrelid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND n.nspname = :schema "
                "AND t.relname = 'authorization_bindings' AND c.contype = 'c'"
            ),
            {"name": constraint, "schema": migrated_postgres_schema.schema_name},
        ).scalar_one()

        column = constraint.removeprefix("ck_authorization_bindings_")
        missing = []
        for member in vocabulary:
            with connection.begin_nested() as savepoint:
                accepted = connection.execute(
                    text(
                        f"SELECT ({expression}) IS NOT FALSE "
                        f'FROM (SELECT CAST(:value AS text) AS "{column}") AS candidate'
                    ),
                    {"value": member.value},
                ).scalar_one()
                if not accepted:
                    missing.append(member.value)
                savepoint.rollback()

    assert not missing, (
        f"{constraint} rejects {missing}. A new {vocabulary.__name__} value needs a "
        "migration widening this constraint — the model derives it from the enum, "
        "so the hermetic suite will not catch this."
    )
