"""Postgres proof for explicit initial Org Owner assignment."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from alembic import command
from app.core.security import has_role
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]

ZERO_ORG = "OR-OWNR0000"
ONE_ORG = "OR-OWNR0001"
MANY_ORG = "OR-OWNR0002"


def _insert_organization(connection, organization_id: str, now: datetime) -> None:
    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": organization_id},
    )
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, created_at, updated_at) "
            "VALUES (:id, :name, :now, :now)"
        ),
        {"id": organization_id, "name": f"Owner proof {organization_id}", "now": now},
    )


def _insert_user(  # noqa: PLR0913 - fixture rows keep every eligibility dimension explicit
    connection,
    *,
    organization_id: str,
    user_id: UUID,
    email: str,
    display_name: str,
    active: bool = True,
    role: str = "admin",
    auth_provider: str = "password",
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO users
                (id, organization_id, email, display_name, is_active, role,
                 auth_provider, failed_login_attempts, authorization_version,
                 created_at, updated_at)
            VALUES
                (:id, :organization_id, :email, :display_name, :active, :role,
                 :auth_provider, 0, 1, :now, :now)
            """
        ),
        {
            "id": user_id,
            "organization_id": organization_id,
            "email": email,
            "display_name": display_name,
            "active": active,
            "role": role,
            "auth_provider": auth_provider,
            "now": now,
        },
    )


def _insert_refresh_token(
    connection,
    *,
    organization_id: str,
    user_id: UUID,
    token_id: UUID,
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO refresh_tokens
                (id, organization_id, user_id, family_id, token_hash, issued_at,
                 expires_at, created_at, updated_at)
            VALUES
                (:id, :organization_id, :user_id, :family_id, :token_hash, :now,
                 :expires_at, :now, :now)
            """
        ),
        {
            "id": token_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "family_id": token_id,
            "token_hash": token_id.hex * 2,
            "now": now,
            "expires_at": now + timedelta(days=14),
        },
    )


def _owner_state(schema: MigratedPostgresSchema, organization_id: str) -> dict:
    with schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        state = (
            connection.execute(
                text(
                    "SELECT * FROM organization_owner_assignments "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            .mappings()
            .one()
        )
        return dict(state)


def _owner_bindings(schema: MigratedPostgresSchema, organization_id: str) -> list[dict]:
    with schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        rows = connection.execute(
            text(
                "SELECT * FROM authorization_bindings "
                "WHERE organization_id = :organization_id AND role_bundle = 'org_owner'"
            ),
            {"organization_id": organization_id},
        ).mappings()
        return [dict(row) for row in rows]


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_initial_owner_migration_handles_zero_one_and_many_without_guessing(  # noqa: PLR0915
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()
    now = datetime.now(UTC)

    zero_inactive = uuid4()
    zero_service = uuid4()
    one_owner = uuid4()
    one_inactive = uuid4()
    one_service = uuid4()
    many_a = uuid4()
    many_b = uuid4()
    many_inactive = uuid4()
    many_service = uuid4()
    refresh_id = uuid4()

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, ZERO_ORG, now)
        _insert_user(
            connection,
            organization_id=ZERO_ORG,
            user_id=zero_inactive,
            email="inactive@zero.example",
            display_name="Inactive Admin",
            active=False,
            now=now,
        )
        _insert_user(
            connection,
            organization_id=ZERO_ORG,
            user_id=zero_service,
            email="service@zero.example",
            display_name="Service Admin",
            auth_provider="service",
            now=now,
        )

        _insert_organization(connection, ONE_ORG, now)
        _insert_user(
            connection,
            organization_id=ONE_ORG,
            user_id=one_owner,
            email="sole.owner@one.example",
            display_name="Sole Eligible Admin",
            now=now,
        )
        _insert_user(
            connection,
            organization_id=ONE_ORG,
            user_id=one_inactive,
            email="inactive@one.example",
            display_name="Inactive Admin",
            active=False,
            now=now,
        )
        _insert_user(
            connection,
            organization_id=ONE_ORG,
            user_id=one_service,
            email="service@one.example",
            display_name="Service Admin",
            auth_provider="service",
            now=now,
        )
        connection.execute(
            text(
                """
                INSERT INTO refresh_tokens
                    (id, organization_id, user_id, family_id, token_hash, issued_at,
                     expires_at, created_at, updated_at)
                VALUES
                    (:id, :organization_id, :user_id, :family_id, :token_hash, :now,
                     :expires_at, :now, :now)
                """
            ),
            {
                "id": refresh_id,
                "organization_id": ONE_ORG,
                "user_id": one_owner,
                "family_id": refresh_id,
                "token_hash": "a" * 64,
                "now": now,
                "expires_at": now + timedelta(days=14),
            },
        )

        _insert_organization(connection, MANY_ORG, now)
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_b,
            email="zeta@many.example",
            display_name="Zeta Admin",
            auth_provider="oidc",
            now=now,
        )
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_a,
            email="alpha@many.example",
            display_name="Alpha Admin",
            now=now,
        )
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_inactive,
            email="inactive@many.example",
            display_name="Inactive Admin",
            active=False,
            now=now,
        )
        _insert_user(
            connection,
            organization_id=MANY_ORG,
            user_id=many_service,
            email="service@many.example",
            display_name="Service Admin",
            auth_provider="service",
            now=now,
        )

    command.upgrade(alembic_config_for_app(), "head")
    clear_database_caches()

    zero = _owner_state(migrated_postgres_schema, ZERO_ORG)
    assert zero["status"] == "designation_required"
    assert zero["basis"] == "zero_eligible_active_human_administrators"
    assert zero["eligible_candidate_count"] == 0
    assert zero["eligible_candidates"] == []
    assert zero["owner_user_id"] is None
    assert zero["owner_binding_id"] is None
    assert _owner_bindings(migrated_postgres_schema, ZERO_ORG) == []

    one = _owner_state(migrated_postgres_schema, ONE_ORG)
    assert one["status"] == "assigned"
    assert one["basis"] == "exactly_one_eligible_active_human_administrator"
    assert one["eligible_candidate_count"] == 1
    assert one["eligible_candidates"] == [
        {
            "user_id": str(one_owner),
            "email": "sole.owner@one.example",
            "display_name": "Sole Eligible Admin",
        }
    ]
    assert one["owner_user_id"] == one_owner
    binding = _owner_bindings(migrated_postgres_schema, ONE_ORG)
    assert len(binding) == 1
    assert one["owner_binding_id"] == binding[0]["id"]
    assert binding[0]["principal_user_id"] == one_owner
    assert binding[0]["principal_type"] == "human"
    assert binding[0]["institution_scope"] == "organization"
    assert binding[0]["institution_id"] is None
    assert binding[0]["module_scope"] == "account"
    assert binding[0]["sensitivity_scope"] == "all"
    assert binding[0]["granted_by_type"] == "system"
    assert binding[0]["granted_by_id"] == "migration:202608280045"
    assert "exactly one eligible active human administrator" in binding[0]["grant_reason"]

    many = _owner_state(migrated_postgres_schema, MANY_ORG)
    assert many["status"] == "designation_required"
    assert many["basis"] == "multiple_eligible_active_human_administrators"
    assert many["eligible_candidate_count"] == 2
    assert many["eligible_candidates"] == [
        {
            "user_id": str(many_a),
            "email": "alpha@many.example",
            "display_name": "Alpha Admin",
        },
        {
            "user_id": str(many_b),
            "email": "zeta@many.example",
            "display_name": "Zeta Admin",
        },
    ]
    assert many["owner_user_id"] is None
    assert many["owner_binding_id"] is None
    assert _owner_bindings(migrated_postgres_schema, MANY_ORG) == []

    # Every legacy administrator, including excluded inactive/service accounts,
    # loses the operational superuser scalar role. Their sessions are invalidated
    # in the same transaction, and account_admin cannot pass either operational
    # gate that protects regulatory submission.
    all_admin_ids = {
        zero_inactive,
        zero_service,
        one_owner,
        one_inactive,
        one_service,
        many_a,
        many_b,
        many_inactive,
        many_service,
    }
    for organization_id in (ZERO_ORG, ONE_ORG, MANY_ORG):
        with migrated_postgres_schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )
            rows = connection.execute(
                text(
                    "SELECT id, role, authorization_version FROM users "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).mappings()
            for row in rows:
                if row["id"] in all_admin_ids:
                    assert row["role"] == "account_admin"
                    assert row["authorization_version"] == 2

    assert has_role(["account_admin"], "admin") is False
    assert has_role(["account_admin"], "analyst") is False
    assert has_role(["account_admin"], "approver") is False
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": ONE_ORG},
        )
        revoked = (
            connection.execute(
                text("SELECT revoked_at, revoked_reason FROM refresh_tokens WHERE id = :token_id"),
                {"token_id": refresh_id},
            )
            .mappings()
            .one()
        )
    assert revoked["revoked_at"] is not None
    assert revoked["revoked_reason"] == "authorization_changed"

    # The shared migration fixture downgrades all the way to base. Normalize this
    # test's synthetic non-password identities first: historical provider
    # migrations narrow auth_provider on downgrade, while FORCE RLS prevents their
    # global conversion statements from seeing tenant rows without a tenant GUC.
    # Keep the principals and organizations until the disposable schema is dropped
    # because their append-only audit evidence may not be updated by FK ON DELETE
    # SET NULL actions.
    for organization_id in (ZERO_ORG, ONE_ORG, MANY_ORG):
        with migrated_postgres_schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "UPDATE users SET auth_provider = 'password' "
                    "WHERE organization_id = :organization_id "
                    "AND auth_provider <> 'password'"
                ),
                {"organization_id": organization_id},
            )


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_downgrade_restores_only_recorded_legacy_administrators(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()
    organization_id = "OR-DOWN0001"
    user_id = uuid4()
    refresh_id = uuid4()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, organization_id, now)
        _insert_user(
            connection,
            organization_id=organization_id,
            user_id=user_id,
            email="legacy.admin@downgrade.example",
            display_name="Legacy Administrator",
            now=now,
        )

    command.upgrade(alembic_config_for_app(), "head")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        recorded = connection.execute(
            text(
                "SELECT user_id, organization_id FROM initial_admin_role_demotions "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).one()
        assert recorded == (user_id, organization_id)
        _insert_refresh_token(
            connection,
            organization_id=organization_id,
            user_id=user_id,
            token_id=refresh_id,
            now=now,
        )

    command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        restored = connection.execute(
            text("SELECT role, authorization_version FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        ).one()
        revoked = connection.execute(
            text("SELECT revoked_at, revoked_reason FROM refresh_tokens WHERE id = :token_id"),
            {"token_id": refresh_id},
        ).one()
        assert restored == ("admin", 3)
        assert revoked.revoked_at is not None
        assert revoked.revoked_reason == "authorization_changed"


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration tests.",
)
def test_downgrade_refuses_post_upgrade_account_administrators_before_mutation(
    migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()
    organization_id = "OR-DOWN0002"
    migrated_user_id = uuid4()
    new_user_id = uuid4()
    migrated_refresh_id = uuid4()
    new_refresh_id = uuid4()
    now = datetime.now(UTC)

    with migrated_postgres_schema.app_engine.begin() as connection:
        _insert_organization(connection, organization_id, now)
        _insert_user(
            connection,
            organization_id=organization_id,
            user_id=migrated_user_id,
            email="migrated.admin@downgrade.example",
            display_name="Migrated Administrator",
            now=now,
        )

    command.upgrade(alembic_config_for_app(), "head")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        _insert_user(
            connection,
            organization_id=organization_id,
            user_id=new_user_id,
            email="new.admin@downgrade.example",
            display_name="New Account Administrator",
            role="account_admin",
            now=now,
        )
        _insert_refresh_token(
            connection,
            organization_id=organization_id,
            user_id=migrated_user_id,
            token_id=migrated_refresh_id,
            now=now,
        )
        _insert_refresh_token(
            connection,
            organization_id=organization_id,
            user_id=new_user_id,
            token_id=new_refresh_id,
            now=now,
        )

    with pytest.raises(RuntimeError, match=str(new_user_id)):
        command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        users = {
            row.id: row.role
            for row in connection.execute(
                text("SELECT id, role FROM users WHERE id IN (:migrated_user_id, :new_user_id)"),
                {"migrated_user_id": migrated_user_id, "new_user_id": new_user_id},
            )
        }
        assert users == {
            migrated_user_id: "account_admin",
            new_user_id: "account_admin",
        }
        revoked = {
            row.id: row.revoked_at
            for row in connection.execute(
                text(
                    "SELECT id, revoked_at FROM refresh_tokens "
                    "WHERE id IN (:migrated_refresh_id, :new_refresh_id)"
                ),
                {
                    "migrated_refresh_id": migrated_refresh_id,
                    "new_refresh_id": new_refresh_id,
                },
            )
        }
        assert revoked == {migrated_refresh_id: None, new_refresh_id: None}
        # Make the deliberately post-migration account compatible with the
        # pre-migration role constraint without deleting its append-only audit
        # evidence. The disposable schema fixture owns final row cleanup.
        connection.execute(
            text("UPDATE users SET role = 'viewer' WHERE id = :user_id"),
            {"user_id": new_user_id},
        )

    command.downgrade(alembic_config_for_app(), "202608250044")
    clear_database_caches()

    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )
        assert (
            connection.scalar(
                text("SELECT role FROM users WHERE id = :user_id"),
                {"user_id": migrated_user_id},
            )
            == "admin"
        )
