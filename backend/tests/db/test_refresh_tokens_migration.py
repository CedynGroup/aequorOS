"""``refresh_tokens`` on a migrated Postgres schema (202608220028).

The hermetic suite builds the schema with ``Base.metadata.create_all``, which
never applies row-level security — so the only place the migration's RLS is
actually exercised is here, against a real Postgres. Opt-in via
``TEST_DATABASE_URL`` (a disposable schema is created and dropped per run).

One test, deliberately: the shared fixture runs the whole migration chain up and
back down again, which is minutes of wall clock per invocation.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.api.helpers import ORG_1, ORG_2
from tests.db.test_postgres_migrations import (  # noqa: F401 - fixture import
    MigratedPostgresSchema,
    migrated_postgres_schema,
)

_TABLE = "refresh_tokens"


@pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres migration smoke tests.",
)
def test_refresh_tokens_table_indexes_rls_and_tenant_isolation(
    migrated_postgres_schema: MigratedPostgresSchema,  # noqa: F811 - pytest fixture
) -> None:
    assert migrated_postgres_schema.tables({_TABLE}) == {_TABLE}
    indexes = {
        "uq_refresh_tokens_token_hash",
        "ix_refresh_tokens_family_id",
        "ix_refresh_tokens_user_id",
        "ix_refresh_tokens_organization_id",
        "ix_refresh_tokens_expires_at",
    }
    assert migrated_postgres_schema.indexes(indexes) == indexes
    assert migrated_postgres_schema.policies({_TABLE}) == {"refresh_tokens_tenant_isolation"}
    constraints = {"uq_refresh_tokens_token_hash", "ck_refresh_tokens_revoked_reason"}
    assert migrated_postgres_schema.constraints(constraints) == constraints

    user_id = uuid4()
    token_id = uuid4()

    with migrated_postgres_schema.app_engine.connect() as connection:
        role_attributes = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert role_attributes == (False, False)
        connection.commit()

        with connection.begin():
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": ORG_1},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO organizations (id, name, created_at, updated_at)
                    VALUES (:organization_id, 'Tenant One', now(), now())
                    """
                ),
                {"organization_id": ORG_1},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users
                      (id, organization_id, email, is_active, role, auth_provider,
                       failed_login_attempts, created_at, updated_at)
                    VALUES
                      (:user_id, :organization_id, 'refresh.rls@example.test', true,
                       'admin', 'password', 0, now(), now())
                    """
                ),
                {"user_id": str(user_id), "organization_id": ORG_1},
            )
            connection.execute(
                text(
                    f"""
                    INSERT INTO {_TABLE}
                      (id, organization_id, user_id, family_id, token_hash, issued_at,
                       expires_at, created_at, updated_at)
                    VALUES
                      (:token_id, :organization_id, :user_id, :token_id, :token_hash,
                       now(), now() + interval '14 days', now(), now())
                    """
                ),
                {
                    "token_id": str(token_id),
                    "organization_id": ORG_1,
                    "user_id": str(user_id),
                    "token_hash": "c" * 64,
                },
            )

            visible_to_org_one = connection.scalar(text(f"SELECT count(*) FROM {_TABLE}"))
            connection.execute(
                text("SELECT set_config('app.organization_id', :organization_id, true)"),
                {"organization_id": ORG_2},
            )
            visible_to_org_two = connection.scalar(text(f"SELECT count(*) FROM {_TABLE}"))

        assert visible_to_org_one == 1
        assert visible_to_org_two == 0
