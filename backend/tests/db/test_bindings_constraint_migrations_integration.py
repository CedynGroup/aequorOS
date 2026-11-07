"""Every enum value the code can store in a binding must be one the database accepts.

This suite exists because of a live 500. ``RoleBundle.VALIDATOR`` was added in
code, offered in the grant composer, and allowed by the evaluator and the
separation-of-duties decision — and then the INSERT hit
``ck_authorization_bindings_role_bundle``, which had last been written before
the bundle existed. The module vocabulary has the same shape:
``ck_authorization_bindings_module_scope`` was written from a literal list by
the foundation migration, so a module added to ``ModuleScope`` needs its own
widening migration (``202609220067`` for ``credit``, ``202609300081`` for
``institution``).
``ck_authorization_access_requests_module_scope`` carries the same module
vocabulary, because a permission request names the exact module a denied page
needs, so it is checked the same way.

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
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authentication, authorization
from app.models import Organization, User
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
    migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema", "migrated_postgres_schema"]

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
    ("table", "column", "vocabulary"),
    [
        ("authorization_bindings", "role_bundle", RoleBundle),
        ("authorization_bindings", "module_scope", ModuleScope),
        # A permission request names the exact module the denied page needs,
        # so it must admit every module a binding can be scoped to.
        ("authorization_access_requests", "module_scope", ModuleScope),
    ],
    ids=["role_bundle", "module_scope", "access_request_module_scope"],
)
def test_the_bindings_constraint_accepts_every_vocabulary_value(
    migrated_postgres_schema: MigratedPostgresSchema,
    table: str,
    column: str,
    vocabulary: type[StrEnum],
) -> None:
    constraint = f"ck_{table}_{column}"
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
                "AND t.relname = :table AND c.contype = 'c'"
            ),
            {
                "name": constraint,
                "schema": migrated_postgres_schema.schema_name,
                "table": table,
            },
        ).scalar_one()

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


def _authority_snapshot(schema: MigratedPostgresSchema) -> tuple[str, ...]:
    """Capture tenant identity, binding, session and audit state for exact comparison."""
    with schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
        snapshots: list[str] = []
        for table in ("users", "authorization_bindings", "refresh_tokens", "audit_events"):
            # SQL text results are untyped; validate serialized snapshots at this boundary.
            result = cast(
                object,
                connection.scalar(
                    text(
                        "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text, '[]') "
                        f"FROM {table} t"
                    )
                ),
            )
            assert isinstance(result, str)
            snapshots.append(result)
        return tuple(snapshots)


ORG = "OR-CFGM0001"


def test_configurator_migration_grants_nothing_and_preserves_existing_authority(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    """Widen and narrow the CHECK without altering preexisting authority or sessions."""
    # Post-migration Account Admins cannot round-trip through the historical
    # ownership migration. This test round-trips only the Configurator migration.
    schema = forward_migrated_postgres_schema
    config = alembic_config_for_app()
    command.downgrade(config, "202611030001")
    principal_ids: list[UUID] = []
    with Session(schema.app_engine) as db:
        db.info["organization_id"] = ORG
        db.add(Organization(id=ORG, name="Configurator migration test"))
        db.flush()
        for bundle in (
            RoleBundle.ANALYST,
            RoleBundle.APPROVER,
            RoleBundle.ACCOUNT_ADMIN,
            RoleBundle.ORG_OWNER,
        ):
            user = User(
                id=uuid4(),
                organization_id=ORG,
                email=f"{bundle.value}@example.test",
                role="account_admin" if bundle is RoleBundle.ORG_OWNER else bundle.value,
            )
            db.add(user)
            db.flush()
            authorization.create_role_binding(
                db,
                organization_id=ORG,
                principal_user_id=user.id,
                principal_type=PrincipalType.HUMAN,
                role_bundle=bundle,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION, None, ModuleScope.ALL, SensitivityScope.ALL
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "migration-test"),
                reason="Existing explicit authority",
                commit=False,
            )
            _ = authentication.issue_tokens(db, user)
            principal_ids.append(user.id)
        db.commit()
    before = _authority_snapshot(schema)
    command.upgrade(config, "head")
    assert _authority_snapshot(schema) == before
    with schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG}
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM authorization_bindings WHERE role_bundle = 'configurator'"
                )
            )
            == 0
        )
        # Insert a new generation: authoritative binding rows are sealed.
        candidate = text(
            "INSERT INTO authorization_bindings "
            "(id, organization_id, principal_user_id, principal_type, role_bundle, "
            "institution_scope, institution_id, module_scope, sensitivity_scope, "
            "granted_by_type, granted_by_id, grant_reason_category, grant_reason, "
            "granted_at, status, valid_from, created_at, updated_at) "
            "SELECT :id, organization_id, principal_user_id, principal_type, :bundle, "
            "institution_scope, institution_id, module_scope, sensitivity_scope, "
            "granted_by_type, granted_by_id, grant_reason_category, grant_reason, "
            "granted_at, status, valid_from, created_at, updated_at "
            "FROM authorization_bindings WHERE principal_user_id = :user"
        )
        with connection.begin_nested() as savepoint:
            inserted = connection.execute(
                candidate,
                {"id": uuid4(), "bundle": "configurator", "user": principal_ids[0]},
            )
            assert inserted.rowcount == 1
            savepoint.rollback()
        with (
            pytest.raises(IntegrityError, match="ck_authorization_bindings_role_bundle"),
            connection.begin_nested(),
        ):
            connection.execute(
                candidate,
                {"id": uuid4(), "bundle": "unknown_bundle", "user": principal_ids[0]},
            )
    command.downgrade(config, "202611030001")
    assert _authority_snapshot(schema) == before
    command.upgrade(config, "head")
