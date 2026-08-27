"""Postgres migration proof for the authorization foundation."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from alembic import command
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_authorization_migration_creates_constraints_and_forced_rls(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    assert migrated_postgres_schema.tables({"authorization_bindings"}) == {"authorization_bindings"}
    expected_constraints = {
        "ck_users_authorization_version_positive",
        "fk_authorization_bindings_principal_tenant",
        "fk_authorization_bindings_institution_tenant",
        "ck_authorization_bindings_principal_type",
        "ck_authorization_bindings_role_bundle",
        "ck_authorization_bindings_principal_bundle",
        "ck_authorization_bindings_institution_scope",
        "ck_authorization_bindings_institution_target",
        "ck_authorization_bindings_module_scope",
        "ck_authorization_bindings_sensitivity_scope",
        "ck_authorization_bindings_status",
        "ck_authorization_bindings_grantor_type",
        "ck_authorization_bindings_grantor",
        "ck_authorization_bindings_grant_reason",
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
        refresh_revocation_constraint = connection.scalar(
            text(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = :schema_name
                  AND t.relname = 'refresh_tokens'
                  AND c.conname = 'ck_refresh_tokens_revoked_reason'
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name},
        )

    assert forced is True
    assert version_default == "1"
    assert refresh_revocation_constraint is not None
    assert "authorization_changed" in refresh_revocation_constraint


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_authorization_migration_downgrade_normalizes_revocation_reason(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    organization_id = "OR-RBCK0001"
    user_id = uuid4()
    token_id = uuid4()
    now = datetime.now(UTC)
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:id, 'Rollback proof', :now, :now)"
            ),
            {"id": organization_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, email, is_active, role, auth_provider, "
                "failed_login_attempts, authorization_version, created_at, updated_at) "
                "VALUES (:id, :organization_id, 'rollback@example.test', true, 'viewer', "
                "'password', 0, 1, :now, :now)"
            ),
            {"id": user_id, "organization_id": organization_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO refresh_tokens "
                "(id, organization_id, user_id, family_id, token_hash, issued_at, expires_at, "
                "revoked_at, revoked_reason, created_at, updated_at) "
                "VALUES (:id, :organization_id, :user_id, :family_id, :token_hash, :issued_at, "
                ":expires_at, :revoked_at, 'authorization_changed', :created_at, :updated_at)"
            ),
            {
                "id": token_id,
                "organization_id": organization_id,
                "user_id": user_id,
                "family_id": token_id,
                "token_hash": "a" * 64,
                "issued_at": now,
                "expires_at": now + timedelta(days=14),
                "revoked_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )

    command.downgrade(alembic_config_for_app(), "202608230042")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        reason = connection.scalar(
            text("SELECT revoked_reason FROM refresh_tokens WHERE id = :id"),
            {"id": token_id},
        )
        constraint_definition = connection.scalar(
            text(
                """
                SELECT pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = :schema_name
                  AND t.relname = 'refresh_tokens'
                  AND c.conname = 'ck_refresh_tokens_revoked_reason'
                """
            ),
            {"schema_name": migrated_postgres_schema.schema_name},
        )

    assert reason == "admin_revoked"
    assert constraint_definition is not None
    assert "authorization_changed" not in constraint_definition
