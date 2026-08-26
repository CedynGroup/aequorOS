"""Postgres migration proof for the authorization foundation."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.db.test_postgres_migrations import MigratedPostgresSchema, migrated_postgres_schema

__all__ = ["migrated_postgres_schema"]


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_authorization_migration_creates_constraints_and_forced_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    assert migrated_postgres_schema.tables({"authorization_bindings"}) == {
        "authorization_bindings"
    }
    expected_constraints = {
        "ck_users_authorization_version_positive",
        "fk_authorization_bindings_principal_tenant",
        "fk_authorization_bindings_institution_tenant",
        "ck_authorization_bindings_principal_bundle",
        "ck_authorization_bindings_institution_target",
        "ck_authorization_bindings_validity_window",
        "ck_authorization_bindings_revocation_state",
    }
    assert migrated_postgres_schema.constraints(expected_constraints) == expected_constraints
    assert migrated_postgres_schema.policies({"authorization_bindings"}) == {
        "authorization_bindings_tenant_isolation"
    }

    with migrated_postgres_schema.app_engine.connect() as connection:
        forced = connection.scalar(
            text(
                """
                SELECT c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name
                  AND c.relname = 'authorization_bindings'
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name},
        )
        version_default = connection.scalar(
            text(
                """
                SELECT column_default
                FROM information_schema.columns
                WHERE table_schema = :schema_name
                  AND table_name = 'users'
                  AND column_name = 'authorization_version'
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name},
        )

    assert forced is True
    assert version_default == "1"
