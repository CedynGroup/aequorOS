"""Postgres proof for restoring unresolved organizations' account administration."""

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

ZERO_ORG = "OR-LOCK0000"
MANY_ORG = "OR-LOCK0001"
OWNED_ORG = "OR-LOCK0002"
EXISTING_ORG = "OR-LOCK0003"


def _insert_suitable_binding(
    connection,
    *,
    organization_id: str,
    user_id: UUID,
    binding_id: UUID,
    now: datetime,
) -> None:
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
                (:id, :organization_id, :user_id, 'human', 'account_admin',
                 'organization', NULL, 'account', 'restricted',
                 'system', 'migration-test:preexisting', 'pre-existing suitable grant',
                 :now, 'active', :now, NULL, NULL, NULL, NULL, NULL, :now, :now)
            """
        ),
        {
            "id": binding_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "now": now,
        },
    )


def _set_tenant(connection, organization_id: str) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )


def _account_bindings(schema: MigratedPostgresSchema, organization_id: str) -> list[dict]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        rows = connection.execute(
            text(
                """
                SELECT id, principal_user_id, role_bundle, institution_scope,
                       institution_id, module_scope, sensitivity_scope, granted_by_type,
                       granted_by_id, grant_reason, status, valid_from, valid_until
                FROM authorization_bindings
                WHERE organization_id = :organization_id
                  AND role_bundle = 'account_admin'
                ORDER BY principal_user_id, id
                """
            ),
            {"organization_id": organization_id},
        ).mappings()
        return [dict(row) for row in rows]


def _owner_state(schema: MigratedPostgresSchema, organization_id: str) -> dict:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        row = (
            connection.execute(
                text(
                    """
                    SELECT status, basis, eligible_candidate_count, eligible_candidates,
                           owner_user_id, owner_binding_id, created_at, updated_at
                    FROM organization_owner_assignments
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            )
            .mappings()
            .one()
        )
        return dict(row)


def _owner_bindings(
    schema: MigratedPostgresSchema,
    organization_id: str,
) -> list[tuple[str, UUID]]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        rows = connection.execute(
            text(
                """
                SELECT organization_id, principal_user_id
                FROM authorization_bindings
                WHERE organization_id = :organization_id
                  AND role_bundle = 'org_owner'
                ORDER BY organization_id, principal_user_id
                """
            ),
            {"organization_id": organization_id},
        )
        return [(str(row.organization_id), row.principal_user_id) for row in rows]


def _user_state(
    schema: MigratedPostgresSchema,
    organization_id: str,
    user_id: UUID,
    token_id: UUID | None = None,
) -> tuple[int, datetime | None, str | None]:
    with schema.app_engine.begin() as connection:
        _set_tenant(connection, organization_id)
        version = connection.scalar(
            text("SELECT authorization_version FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        if token_id is None:
            return int(version), None, None
        token = connection.execute(
            text("SELECT revoked_at, revoked_reason FROM refresh_tokens WHERE id = :token_id"),
            {"token_id": token_id},
        ).one()
        return int(version), token.revoked_at, token.revoked_reason


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_restoration_grants_only_unresolved_eligible_administrators_and_is_idempotent(  # noqa: PLR0915
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    config = alembic_config_for_app()
    command.downgrade(config, "202608250044")
    clear_database_caches()
    now = datetime.now(UTC)

    zero_inactive = uuid4()
    zero_service = uuid4()
    many_a = uuid4()
    many_b = uuid4()
    owned_admin = uuid4()
    existing_a = uuid4()
    existing_b = uuid4()
    existing_binding_id = uuid4()
    tokens = {user_id: uuid4() for user_id in (many_a, many_b, owned_admin, existing_a, existing_b)}

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, ZERO_ORG, now)
        _insert_user(
            connection,
            organization_id=ZERO_ORG,
            user_id=zero_inactive,
            email="inactive@zero.example",
            display_name="Inactive Administrator",
            active=False,
            now=now,
        )
        _insert_user(
            connection,
            organization_id=ZERO_ORG,
            user_id=zero_service,
            email="service@zero.example",
            display_name="Service Administrator",
            auth_provider="service",
            now=now,
        )

        _insert_organization(connection, MANY_ORG, now)
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_b,
            email="zeta@many.example",
            display_name="Zeta Administrator",
            now=now,
        )
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_a,
            email="alpha@many.example",
            display_name="Alpha Administrator",
            now=now,
        )

        _insert_organization(connection, OWNED_ORG, now)
        _insert_user(
            connection,
            organization_id=OWNED_ORG,
            user_id=owned_admin,
            email="owner@owned.example",
            display_name="Owned Administrator",
            now=now,
        )

        _insert_organization(connection, EXISTING_ORG, now)
        _insert_user(
            connection,
            organization_id=EXISTING_ORG,
            user_id=existing_a,
            email="bound@existing.example",
            display_name="Already Bound Administrator",
            now=now,
        )
        _insert_user(
            connection,
            organization_id=EXISTING_ORG,
            user_id=existing_b,
            email="unbound@existing.example",
            display_name="Unbound Administrator",
            now=now,
        )

    command.upgrade(config, "202608290047")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, EXISTING_ORG)
        _insert_suitable_binding(
            connection,
            organization_id=EXISTING_ORG,
            user_id=existing_a,
            binding_id=existing_binding_id,
            now=now,
        )
        connection.execute(
            text(
                "UPDATE users SET authorization_version = 7 "
                "WHERE organization_id = :organization_id AND id = :user_id"
            ),
            {"organization_id": EXISTING_ORG, "user_id": existing_a},
        )

    for organization_id, user_id in (
        (MANY_ORG, many_a),
        (MANY_ORG, many_b),
        (OWNED_ORG, owned_admin),
        (EXISTING_ORG, existing_a),
        (EXISTING_ORG, existing_b),
    ):
        with migrated_postgres_schema.app_engine.begin() as connection:
            _set_tenant(connection, organization_id)
            _insert_refresh_token(
                connection,
                organization_id=organization_id,
                user_id=user_id,
                token_id=tokens[user_id],
                now=now,
            )

    assignment_states_before = {
        organization_id: _owner_state(migrated_postgres_schema, organization_id)
        for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
    }

    command.upgrade(config, "head")
    clear_database_caches()

    assert _account_bindings(migrated_postgres_schema, ZERO_ORG) == []
    assert _account_bindings(migrated_postgres_schema, OWNED_ORG) == []

    many_bindings = _account_bindings(migrated_postgres_schema, MANY_ORG)
    assert {row["principal_user_id"] for row in many_bindings} == {many_a, many_b}
    for binding in many_bindings:
        assert binding["institution_scope"] == "organization"
        assert binding["institution_id"] is None
        assert binding["module_scope"] == "account"
        assert binding["sensitivity_scope"] == "restricted"
        assert binding["granted_by_type"] == "system"
        assert binding["granted_by_id"] == "migration:202609090051"
        assert "Org Owner designation remains outstanding" in binding["grant_reason"]

    existing_bindings = _account_bindings(migrated_postgres_schema, EXISTING_ORG)
    assert len(existing_bindings) == 2
    existing_by_user = {row["principal_user_id"]: row for row in existing_bindings}
    assert existing_by_user[existing_a]["id"] == existing_binding_id
    assert existing_by_user[existing_a]["granted_by_id"] == "migration-test:preexisting"
    assert existing_by_user[existing_b]["granted_by_id"] == "migration:202609090051"

    assert {
        organization_id: _owner_bindings(migrated_postgres_schema, organization_id)
        for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
    } == {
        ZERO_ORG: [],
        MANY_ORG: [],
        OWNED_ORG: [(OWNED_ORG, owned_admin)],
        EXISTING_ORG: [],
    }
    assert {
        organization_id: _owner_state(migrated_postgres_schema, organization_id)
        for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
    } == assignment_states_before

    for organization_id, user_id in (
        (MANY_ORG, many_a),
        (MANY_ORG, many_b),
        (EXISTING_ORG, existing_b),
    ):
        version, revoked_at, revoked_reason = _user_state(
            migrated_postgres_schema,
            organization_id,
            user_id,
            tokens[user_id],
        )
        assert version == 3
        assert revoked_at is not None
        assert revoked_reason == "authorization_changed"

    assert _user_state(
        migrated_postgres_schema,
        EXISTING_ORG,
        existing_a,
        tokens[existing_a],
    ) == (7, None, None)
    assert _user_state(
        migrated_postgres_schema,
        OWNED_ORG,
        owned_admin,
        tokens[owned_admin],
    ) == (2, None, None)
    assert _user_state(migrated_postgres_schema, ZERO_ORG, zero_inactive)[0] == 2
    assert _user_state(migrated_postgres_schema, ZERO_ORG, zero_service)[0] == 2

    snapshot = {
        "bindings": {
            organization_id: _account_bindings(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "assignments": {
            organization_id: _owner_state(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "owners": {
            organization_id: _owner_bindings(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "users": {
            (organization_id, user_id): _user_state(
                migrated_postgres_schema,
                organization_id,
                user_id,
                tokens.get(user_id),
            )
            for organization_id, user_id in (
                (ZERO_ORG, zero_inactive),
                (ZERO_ORG, zero_service),
                (MANY_ORG, many_a),
                (MANY_ORG, many_b),
                (OWNED_ORG, owned_admin),
                (EXISTING_ORG, existing_a),
                (EXISTING_ORG, existing_b),
            )
        },
    }

    command.stamp(config, "202608290047")
    command.upgrade(config, "head")
    clear_database_caches()

    assert {
        "bindings": {
            organization_id: _account_bindings(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "assignments": {
            organization_id: _owner_state(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "owners": {
            organization_id: _owner_bindings(migrated_postgres_schema, organization_id)
            for organization_id in (ZERO_ORG, MANY_ORG, OWNED_ORG, EXISTING_ORG)
        },
        "users": {
            (organization_id, user_id): _user_state(
                migrated_postgres_schema,
                organization_id,
                user_id,
                tokens.get(user_id),
            )
            for organization_id, user_id in (
                (ZERO_ORG, zero_inactive),
                (ZERO_ORG, zero_service),
                (MANY_ORG, many_a),
                (MANY_ORG, many_b),
                (OWNED_ORG, owned_admin),
                (EXISTING_ORG, existing_a),
                (EXISTING_ORG, existing_b),
            )
        },
    } == snapshot

    # Historical auth-provider downgrades accept only password/OIDC identities.
    # Normalize the synthetic service candidate before the fixture walks the
    # disposable schema back to base.
    with migrated_postgres_schema.app_engine.begin() as connection:
        _set_tenant(connection, ZERO_ORG)
        connection.execute(
            text("UPDATE users SET auth_provider = 'password' WHERE id = :user_id"),
            {"user_id": zero_service},
        )
