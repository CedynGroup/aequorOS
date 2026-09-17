"""Postgres proof for the active-member baseline backfill (202609160053)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from alembic import command
from tests.db.test_initial_owner_migration import (
    _insert_organization,
    _insert_refresh_token,
    _insert_user,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

pytestmark = pytest.mark.committing_db

PREVIOUS_REVISION = "202609160052"
REVISION = "202609160053"
ORGS = ("OR-MEMB0001", "OR-MEMB0002")


def _set_tenant(connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _member_rows(
    schema: MigratedPostgresSchema,
    organization_id: str,
) -> list[dict]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        return [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT principal_user_id, principal_type, role_bundle,
                           institution_scope, institution_id, module_scope,
                           sensitivity_scope, granted_by_type, granted_by_id,
                           status
                    FROM authorization_bindings
                    WHERE organization_id = :organization_id
                      AND role_bundle = 'member'
                    ORDER BY principal_user_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
        ]


def _user_version(
    schema: MigratedPostgresSchema,
    organization_id: str,
    user_id: UUID,
) -> int:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        return int(
            connection.scalar(
                text("SELECT authorization_version FROM users WHERE id = :user_id"),
                {"user_id": user_id},
            )
        )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_backfill_covers_each_active_human_once_and_downgrades_cleanly(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    now = datetime.now(UTC)

    active = {organization_id: [uuid4(), uuid4()] for organization_id in ORGS}
    inactive = {organization_id: uuid4() for organization_id in ORGS}
    service = {organization_id: uuid4() for organization_id in ORGS}
    tokens = {user_id: uuid4() for users in active.values() for user_id in users}

    with migrated_postgres_schema.app_engine.begin() as connection:
        for organization_id in ORGS:
            _insert_organization(connection, organization_id, now)
            for index, user_id in enumerate(active[organization_id], start=1):
                _insert_user(
                    connection,
                    organization_id=organization_id,
                    user_id=user_id,
                    email=f"active{index}@{organization_id.lower()}.example",
                    display_name=f"Active {index}",
                    role="viewer",
                    now=now,
                )
                _insert_refresh_token(
                    connection,
                    organization_id=organization_id,
                    user_id=user_id,
                    token_id=tokens[user_id],
                    now=now,
                )
            _insert_user(
                connection,
                organization_id=organization_id,
                user_id=inactive[organization_id],
                email=f"inactive@{organization_id.lower()}.example",
                display_name="Inactive",
                active=False,
                role="viewer",
                now=now,
            )
            _insert_user(
                connection,
                organization_id=organization_id,
                user_id=service[organization_id],
                email=f"service@{organization_id.lower()}.example",
                display_name="Service",
                role="viewer",
                auth_provider="service",
                now=now,
            )

    command.upgrade(config, REVISION)
    clear_database_caches()

    for organization_id in ORGS:
        rows = _member_rows(migrated_postgres_schema, organization_id)
        assert [row["principal_user_id"] for row in rows] == sorted(
            active[organization_id], key=str
        )
        assert all(
            (
                row["principal_type"],
                row["role_bundle"],
                row["institution_scope"],
                row["institution_id"],
                row["module_scope"],
                row["sensitivity_scope"],
                row["granted_by_type"],
                row["granted_by_id"],
                row["status"],
            )
            == (
                "human",
                "member",
                "organization",
                None,
                "account",
                "restricted",
                "system",
                "migration:202609160053",
                "active",
            )
            for row in rows
        )
        assert inactive[organization_id] not in {row["principal_user_id"] for row in rows}
        assert service[organization_id] not in {row["principal_user_id"] for row in rows}
        for user_id in active[organization_id]:
            assert _user_version(migrated_postgres_schema, organization_id, user_id) == 2

        with migrated_postgres_schema.app_engine.begin() as connection:
            _set_tenant(connection, organization_id)
            audit_count = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM audit_events
                    WHERE organization_id = :organization_id
                      AND event_type = 'authorization.binding_granted'
                      AND details ->> 'role_bundle' = 'member'
                    """
                ),
                {"organization_id": organization_id},
            )
            assert audit_count == len(active[organization_id])
            revoked_tokens = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM refresh_tokens
                    WHERE organization_id = :organization_id
                      AND revoked_reason = 'authorization_changed'
                    """
                ),
                {"organization_id": organization_id},
            )
            assert revoked_tokens == len(active[organization_id])

    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()

    for organization_id in ORGS:
        assert _member_rows(migrated_postgres_schema, organization_id) == []
        with migrated_postgres_schema.app_engine.begin() as connection:
            _set_tenant(connection, organization_id)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM users WHERE organization_id = :organization_id"),
                    {"organization_id": organization_id},
                )
                == 4
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM organizations WHERE id = :organization_id"),
                    {"organization_id": organization_id},
                )
                == 1
            )

    # The shared fixture subsequently downgrades the entire schema to base.
    # Historical auth-provider constraints predate service identities, so
    # normalize only that synthetic fixture dimension before crossing them.
    for organization_id in ORGS:
        with migrated_postgres_schema.app_engine.begin() as connection:
            _set_tenant(connection, organization_id)
            connection.execute(
                text(
                    "UPDATE users SET auth_provider = 'password' "
                    "WHERE organization_id = :organization_id "
                    "AND auth_provider = 'service'"
                ),
                {"organization_id": organization_id},
            )

    command.upgrade(config, "head")
    clear_database_caches()
