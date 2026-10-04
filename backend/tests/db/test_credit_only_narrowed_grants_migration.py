"""Deployment proof: revoke unsupported scopes across tenants, never widen them."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.session import force_rls_suspended
from app.models import AuthorizationBinding, Bank, IntegrationKey, User
from tests.db.test_initial_owner_migration import (
    _insert_organization,
    _insert_refresh_token,
    _insert_user,
)
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required",
    ),
]


def test_revoke_unsupported_grants_keys_and_sessions_without_widening(  # noqa: PLR0915 - deployment lifecycle proof
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    config = alembic_config_for_app()
    command.downgrade(config, "202609300082")
    now = datetime.now(UTC)
    tenants = []
    with (
        schema.app_engine.begin() as connection,
        force_rls_suspended(
            connection, "authorization_bindings", "users", "refresh_tokens", "banks"
        ),
    ):
        for index, org in enumerate(("OR-NARR0001", "OR-NARR0002"), start=1):
            _insert_organization(connection, org, now)
            bank_id = f"BK-NARR000{index}"
            connection.execute(
                insert(Bank).values(
                    id=bank_id,
                    organization_id=org,
                    name="Migration fixture",
                    short_name="Fixture",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type="universal_bank",
                )
            )
            affected, untouched, machine = uuid4(), uuid4(), uuid4()
            for user_id in (affected, untouched, machine):
                _insert_user(
                    connection,
                    organization_id=org,
                    user_id=user_id,
                    email=f"{user_id}@example.test",
                    display_name="Migration fixture",
                    role="viewer",
                    auth_provider="service" if user_id == machine else "password",
                    now=now,
                )
                _insert_refresh_token(
                    connection, organization_id=org, user_id=user_id, token_id=uuid4(), now=now
                )
            rows = []
            for user_id, module, kind in (
                (affected, "liq", "branch"),
                (affected, "all", "region"),
                (affected, "credit", "branch"),
                (untouched, "credit", "region"),
                (untouched, "all", "all"),
                (machine, "risk", "branch"),
                (machine, "credit", "all"),
            ):
                binding_id = uuid4()
                connection.execute(
                    insert(AuthorizationBinding).values(
                        id=binding_id,
                        organization_id=org,
                        principal_user_id=user_id,
                        principal_type="machine" if user_id == machine else "human",
                        role_bundle="bi_reader" if user_id == machine else "viewer",
                        institution_scope="institution",
                        institution_id=bank_id,
                        module_scope=module,
                        sensitivity_scope="all",
                        data_scope_kind=kind,
                        data_scope_values=None if kind == "all" else ["B1"],
                        granted_by_type="system",
                        granted_by_id="migration-fixture",
                        grant_reason="Exercise unsupported legacy grants",
                        granted_at=now,
                        status="active",
                        valid_from=now,
                    )
                )
                rows.append((binding_id, user_id, module, kind))
            key_id = uuid4()
            connection.execute(
                insert(IntegrationKey).values(
                    id=key_id,
                    organization_id=org,
                    bank_id=bank_id,
                    service_user_id=machine,
                    label="Legacy narrowed reader",
                    key_prefix="aeq_live_fixture",
                    key_hash=key_id.hex * 2,
                )
            )
            tenants.append((org, affected, untouched, machine, rows, key_id))
    command.upgrade(config, "head")
    assert schema.constraints({"ck_authorization_bindings_narrowed_module"}) == {
        "ck_authorization_bindings_narrowed_module"
    }
    for org, affected, untouched, machine, rows, key_id in tenants:
        with schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org}
            )
            bindings = {
                row.id: row for row in connection.execute(select(AuthorizationBinding.__table__))
            }
            for binding_id, user_id, module, kind in rows:
                row = bindings[binding_id]
                revoked = user_id == machine or (kind != "all" and module != "credit")
                assert row.status == ("revoked" if revoked else "active")
                assert row.data_scope_kind == kind
                assert row.data_scope_values == (None if kind == "all" else ["B1"])
                if revoked:
                    assert row.revoked_at is not None
                    assert row.revoked_by_id == "migration:202610040083"
                    assert row.revoked_reason
            users = {row.id: row for row in connection.execute(select(User.__table__))}
            assert users[affected].authorization_version == 2
            assert users[untouched].authorization_version == 1
            assert users[machine].authorization_version == 2
            assert users[machine].is_active is False
            assert users[affected].is_active is True
            assert connection.scalar(
                select(IntegrationKey.revoked_at).where(IntegrationKey.id == key_id)
            )
            tokens = connection.execute(
                text("SELECT user_id, revoked_at FROM refresh_tokens")
            ).all()
            assert all(bool(revoked_at) == (user_id != untouched) for user_id, revoked_at in tokens)
        with schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org}
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "UPDATE authorization_bindings SET status = 'active', revoked_at = NULL, "
                        "revoked_by_type = NULL, revoked_by_id = NULL, "
                        "revoked_reason = NULL WHERE id = :id"
                    ),
                    {"id": rows[0][0]},
                )
    command.downgrade(config, "202609300082")
    command.upgrade(config, "head")
    with (
        schema.app_engine.begin() as connection,
        force_rls_suspended(connection, "users", "authorization_bindings"),
    ):
        for _org, affected, _untouched, _machine, rows, _key_id in tenants:
            assert (
                connection.scalar(select(User.authorization_version).where(User.id == affected))
                == 2
            )
            assert (
                connection.scalar(
                    select(AuthorizationBinding.status).where(AuthorizationBinding.id == rows[0][0])
                )
                == "revoked"
            )
