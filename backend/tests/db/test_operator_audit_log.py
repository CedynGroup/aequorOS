"""Operator append-only enforcement against the migrated PostgreSQL schema."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db import test_postgres_migrations as migration_fixtures
from tests.db.test_postgres_migrations import MigratedPostgresSchema

forward_migrated_postgres_schema = migration_fixtures.forward_migrated_postgres_schema


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the Postgres append-only trigger test.",
)
def test_operator_audit_log_update_and_delete_blocked_on_postgres(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    app_engine = forward_migrated_postgres_schema.app_engine
    with app_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO operator_audit_log
                    (id, operator_email, auth_mode, action, target_org, detail, created_at)
                VALUES
                    (gen_random_uuid(), 'dev@aequoros.com', 'dev', 'tenants.provision',
                     NULL, '{}', now())
                """
            )
        )
    # Either the privilege denial or the append-only trigger may fire first.
    for statement in (
        "UPDATE operator_audit_log SET action = 'tampered'",
        "DELETE FROM operator_audit_log",
    ):
        with (
            pytest.raises(DBAPIError, match=r"append-only|permission denied"),
            app_engine.begin() as connection,
        ):
            connection.execute(text(statement))
    with app_engine.connect() as connection:
        assert connection.execute(
            text("SELECT action FROM operator_audit_log")
        ).scalars().all() == ["tenants.provision"]
