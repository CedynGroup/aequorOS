"""Postgres migration proof for bank-scoped integration keys."""

from __future__ import annotations

import os
from datetime import UTC, datetime
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

pytestmark = pytest.mark.committing_db


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_upgrade_keeps_legacy_keys_unscoped_and_creates_no_binding(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    command.downgrade(alembic_config_for_app(), "202608290047")
    clear_database_caches()
    organization_id = "OR-KEYM0001"
    creator_id = uuid4()
    service_user_id = uuid4()
    key_id = uuid4()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:id, 'Legacy key migration proof', :now, :now)"
            ),
            {"id": organization_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, email, is_active, role, auth_provider, "
                "failed_login_attempts, authorization_version, created_at, updated_at) "
                "VALUES "
                "(:creator_id, :organization_id, 'creator@example.test', true, 'viewer', "
                "'password', 0, 1, :now, :now), "
                "(:service_user_id, :organization_id, 'legacy@service.aequoros.invalid', "
                "true, 'analyst', 'service', 0, 1, :now, :now)"
            ),
            {
                "creator_id": creator_id,
                "service_user_id": service_user_id,
                "organization_id": organization_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO integration_keys "
                "(id, organization_id, service_user_id, label, key_prefix, key_hash, "
                "created_by, created_at, updated_at) "
                "VALUES (:id, :organization_id, :service_user_id, 'Legacy feed', "
                "'aeq_live_LEGA…', :key_hash, :creator_id, :now, :now)"
            ),
            {
                "id": key_id,
                "organization_id": organization_id,
                "service_user_id": service_user_id,
                "creator_id": creator_id,
                "key_hash": "a" * 64,
                "now": now,
            },
        )

    command.upgrade(alembic_config_for_app(), "head")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        bank_id = connection.scalar(
            text("SELECT bank_id FROM integration_keys WHERE id = :key_id"),
            {"key_id": key_id},
        )
        machine_bindings = connection.scalar(
            text(
                "SELECT count(*) FROM authorization_bindings "
                "WHERE organization_id = :organization_id "
                "AND principal_user_id = :service_user_id"
            ),
            {
                "organization_id": organization_id,
                "service_user_id": service_user_id,
            },
        )

    assert bank_id is None
    assert machine_bindings == 0
    assert migrated_postgres_schema.constraints({"fk_integration_keys_bank_tenant"}) == {
        "fk_integration_keys_bank_tenant"
    }
    assert migrated_postgres_schema.indexes({"ix_integration_keys_organization_bank"}) == {
        "ix_integration_keys_organization_bank"
    }

    # The shared fixture subsequently downgrades the entire migration chain.
    # Remove the service principal before the original integration-key
    # migration restores the password/OIDC-only auth-provider constraint.
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text("DELETE FROM organizations WHERE id = :organization_id"),
            {"organization_id": organization_id},
        )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_downgrade_refuses_to_discard_issued_bank_targets(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    organization_id = "OR-KEYM0002"
    bank_id = "BK-KEYM0002"
    creator_id = uuid4()
    service_user_id = uuid4()
    key_id = uuid4()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:id, 'Scoped key downgrade proof', :now, :now)"
            ),
            {"id": organization_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO banks "
                "(id, organization_id, name, short_name, currency, jurisdiction_code, "
                "license_type, institution_type, created_at, updated_at) "
                "VALUES (:id, :organization_id, 'Scoped Key Bank', 'Scoped Key', 'GHS', "
                "'GH', 'universal_bank', 'universal_bank', :now, :now)"
            ),
            {"id": bank_id, "organization_id": organization_id, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, email, is_active, role, auth_provider, "
                "failed_login_attempts, authorization_version, created_at, updated_at) "
                "VALUES "
                "(:creator_id, :organization_id, 'creator2@example.test', true, 'viewer', "
                "'password', 0, 1, :now, :now), "
                "(:service_user_id, :organization_id, 'scoped@service.aequoros.invalid', "
                "true, 'viewer', 'service', 0, 2, :now, :now)"
            ),
            {
                "creator_id": creator_id,
                "service_user_id": service_user_id,
                "organization_id": organization_id,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO integration_keys "
                "(id, organization_id, bank_id, service_user_id, label, key_prefix, key_hash, "
                "created_by, created_at, updated_at) "
                "VALUES (:id, :organization_id, :bank_id, :service_user_id, 'Scoped feed', "
                "'aeq_live_SCOP…', :key_hash, :creator_id, :now, :now)"
            ),
            {
                "id": key_id,
                "organization_id": organization_id,
                "bank_id": bank_id,
                "service_user_id": service_user_id,
                "creator_id": creator_id,
                "key_hash": "b" * 64,
                "now": now,
            },
        )

    with pytest.raises(RuntimeError, match="Cannot safely downgrade"):
        command.downgrade(alembic_config_for_app(), "202608290047")
    clear_database_caches()

    # Leave the fixture on the prior revision so its normal base downgrade can
    # complete. This is explicit cleanup after proving the guarded refusal.
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text("UPDATE integration_keys SET bank_id = NULL WHERE id = :key_id"),
            {"key_id": key_id},
        )
    command.downgrade(alembic_config_for_app(), "202608290047")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text("DELETE FROM organizations WHERE id = :organization_id"),
            {"organization_id": organization_id},
        )
