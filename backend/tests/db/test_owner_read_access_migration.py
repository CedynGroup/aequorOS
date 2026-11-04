"""Postgres proof for the Org Owner read-sentence backfill (202609160052)."""

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

PREVIOUS_REVISION = "202609110051"
REVISION = "202609160052"
GRANTOR = "migration:202609160052"

BARE_ORG = "OR-READ0000"  # owner holds only the ownership sentence
COVERED_ORG = "OR-READ0001"  # owner already reads everything through Analyst
INACTIVE_ORG = "OR-READ0002"  # owner binding on a deactivated user
REVOKED_ORG = "OR-READ0003"  # ownership was revoked
UNOWNED_ORG = "OR-READ0004"  # account administrator, no owner


def _set_tenant(connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _insert_binding(  # noqa: PLR0913 - every binding dimension is explicit
    connection,
    *,
    organization_id: str,
    user_id: UUID,
    role_bundle: str,
    module_scope: str,
    sensitivity_scope: str,
    now: datetime,
    revoked: bool = False,
    binding_id: UUID | None = None,
) -> UUID:
    binding_id = binding_id or uuid4()
    connection.execute(
        text(
            """
            INSERT INTO authorization_bindings
                (id, organization_id, principal_user_id, principal_type, role_bundle,
                 institution_scope, institution_id, module_scope, sensitivity_scope,
                 granted_by_type, granted_by_id, grant_reason, granted_at, status,
                 valid_from, valid_until, revoked_at, revoked_by_type, revoked_by_id,
                 revoked_reason, created_at, updated_at)
            VALUES
                (:id, :organization_id, :user_id, 'human', :role_bundle,
                 'organization', NULL, :module_scope, :sensitivity_scope,
                 'system', 'migration-test:preexisting', 'pre-existing row',
                 :now, :status, :now, NULL, :revoked_at, :revoked_by_type, :revoked_by_id,
                 :revoked_reason, :now, :now)
            """
        ),
        {
            "id": binding_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "role_bundle": role_bundle,
            "module_scope": module_scope,
            "sensitivity_scope": sensitivity_scope,
            "now": now,
            "status": "revoked" if revoked else "active",
            "revoked_at": now if revoked else None,
            "revoked_by_type": "system" if revoked else None,
            "revoked_by_id": "migration-test:revoker" if revoked else None,
            "revoked_reason": "ownership transferred" if revoked else None,
        },
    )
    return binding_id


def _read_bindings(schema: MigratedPostgresSchema, organization_id: str) -> list[dict]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        rows = connection.execute(
            text(
                """
                SELECT id, principal_user_id, role_bundle, institution_scope, institution_id,
                       module_scope, sensitivity_scope, granted_by_type, granted_by_id,
                       grant_reason, status
                FROM authorization_bindings
                WHERE organization_id = :organization_id
                  AND role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                ORDER BY principal_user_id, granted_at, id
                """
            ),
            {"organization_id": organization_id},
        ).mappings()
        return [dict(row) for row in rows]


def _owner_bindings(schema: MigratedPostgresSchema, organization_id: str) -> list[dict]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        rows = connection.execute(
            text(
                """
                SELECT id, principal_user_id, status, revoked_at
                FROM authorization_bindings
                WHERE organization_id = :organization_id AND role_bundle = 'org_owner'
                ORDER BY id
                """
            ),
            {"organization_id": organization_id},
        ).mappings()
        return [dict(row) for row in rows]


def _user_state(
    schema: MigratedPostgresSchema,
    organization_id: str,
    user_id: UUID,
    token_id: UUID,
) -> tuple[int, datetime | None, str | None]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        version = connection.scalar(
            text("SELECT authorization_version FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        token = connection.execute(
            text("SELECT revoked_at, revoked_reason FROM refresh_tokens WHERE id = :token_id"),
            {"token_id": token_id},
        ).one()
        return int(version), token.revoked_at, token.revoked_reason


ORGS = (BARE_ORG, COVERED_ORG, INACTIVE_ORG, REVOKED_ORG, UNOWNED_ORG)


def _snapshot(schema: MigratedPostgresSchema, users: dict[str, UUID], tokens: dict[UUID, UUID]):
    return {
        "reads": {org: _read_bindings(schema, org) for org in ORGS},
        "owners": {org: _owner_bindings(schema, org) for org in ORGS},
        "users": {org: _user_state(schema, org, users[org], tokens[users[org]]) for org in ORGS},
    }


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_backfill_gives_only_bare_active_owners_the_read_sentence(  # noqa: PLR0915
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    now = datetime.now(UTC)

    users = {org: uuid4() for org in ORGS}
    tokens = {user_id: uuid4() for user_id in users.values()}
    covered_analyst_id = uuid4()

    with migrated_postgres_schema.app_engine.begin() as connection:
        for org in ORGS:
            _insert_organization(connection, org, now)
            _insert_user(
                connection,
                organization_id=org,
                user_id=users[org],
                email=f"owner@{org.lower()}.example",
                display_name=f"Owner of {org}",
                # The backfill keys on the owner BINDING, never the scalar role;
                # a plain scalar role also keeps the fixture teardown's full
                # downgrade clear of 202608280046's post-migration-admin guard.
                role="viewer",
                active=org != INACTIVE_ORG,
                now=now,
            )
        for org in (BARE_ORG, COVERED_ORG, INACTIVE_ORG):
            _set_tenant(connection, org)
            _insert_binding(
                connection,
                organization_id=org,
                user_id=users[org],
                role_bundle="org_owner",
                module_scope="account",
                sensitivity_scope="all",
                now=now,
            )
        _set_tenant(connection, REVOKED_ORG)
        _insert_binding(
            connection,
            organization_id=REVOKED_ORG,
            user_id=users[REVOKED_ORG],
            role_bundle="org_owner",
            module_scope="account",
            sensitivity_scope="all",
            now=now,
            revoked=True,
        )
        _set_tenant(connection, UNOWNED_ORG)
        _insert_binding(
            connection,
            organization_id=UNOWNED_ORG,
            user_id=users[UNOWNED_ORG],
            role_bundle="account_admin",
            module_scope="account",
            sensitivity_scope="restricted",
            now=now,
        )
        _set_tenant(connection, COVERED_ORG)
        _insert_binding(
            connection,
            organization_id=COVERED_ORG,
            user_id=users[COVERED_ORG],
            role_bundle="analyst",
            module_scope="all",
            sensitivity_scope="all",
            now=now,
            binding_id=covered_analyst_id,
        )
        for org in ORGS:
            _set_tenant(connection, org)
            _insert_refresh_token(
                connection,
                organization_id=org,
                user_id=users[org],
                token_id=tokens[users[org]],
                now=now,
            )

    owners_before = {org: _owner_bindings(migrated_postgres_schema, org) for org in ORGS}

    command.upgrade(config, REVISION)
    clear_database_caches()

    # Only the bare, active owner receives the sentence — exact dimensions.
    bare = _read_bindings(migrated_postgres_schema, BARE_ORG)
    assert len(bare) == 1
    assert bare[0]["principal_user_id"] == users[BARE_ORG]
    assert bare[0]["role_bundle"] == "viewer"
    assert (bare[0]["institution_scope"], bare[0]["institution_id"]) == ("organization", None)
    assert (bare[0]["module_scope"], bare[0]["sensitivity_scope"]) == ("all", "all")
    assert (bare[0]["granted_by_type"], bare[0]["granted_by_id"]) == ("system", GRANTOR)
    assert "Org Owner read access" in bare[0]["grant_reason"]
    assert bare[0]["status"] == "active"

    covered = _read_bindings(migrated_postgres_schema, COVERED_ORG)
    assert [row["id"] for row in covered] == [covered_analyst_id]
    assert _read_bindings(migrated_postgres_schema, INACTIVE_ORG) == []
    assert _read_bindings(migrated_postgres_schema, REVOKED_ORG) == []
    assert _read_bindings(migrated_postgres_schema, UNOWNED_ORG) == []

    # Ownership itself is never created, moved, or revived.
    assert {org: _owner_bindings(migrated_postgres_schema, org) for org in ORGS} == owners_before

    # Session invalidation only for the user who actually received a row.
    version, revoked_at, reason = _user_state(
        migrated_postgres_schema, BARE_ORG, users[BARE_ORG], tokens[users[BARE_ORG]]
    )
    assert (version, reason) == (2, "authorization_changed")
    assert revoked_at is not None
    for org in (COVERED_ORG, INACTIVE_ORG, REVOKED_ORG, UNOWNED_ORG):
        assert _user_state(migrated_postgres_schema, org, users[org], tokens[users[org]]) == (
            1,
            None,
            None,
        )

    # A true rerun of the migration body changes nothing.
    before = _snapshot(migrated_postgres_schema, users, tokens)
    command.stamp(config, PREVIOUS_REVISION)
    command.upgrade(config, REVISION)
    clear_database_caches()
    assert _snapshot(migrated_postgres_schema, users, tokens) == before

    # Downgrade removes exactly the rows it wrote and invalidates only their users.
    command.downgrade(config, PREVIOUS_REVISION)
    clear_database_caches()
    assert _read_bindings(migrated_postgres_schema, BARE_ORG) == []
    assert [row["id"] for row in _read_bindings(migrated_postgres_schema, COVERED_ORG)] == [
        covered_analyst_id
    ]
    assert {org: _owner_bindings(migrated_postgres_schema, org) for org in ORGS} == owners_before
    assert (
        _user_state(migrated_postgres_schema, BARE_ORG, users[BARE_ORG], tokens[users[BARE_ORG]])[0]
        == 3
    )
    for org in (COVERED_ORG, INACTIVE_ORG, REVOKED_ORG, UNOWNED_ORG):
        assert _user_state(migrated_postgres_schema, org, users[org], tokens[users[org]])[0] == 1

    command.upgrade(config, "head")
    clear_database_caches()
