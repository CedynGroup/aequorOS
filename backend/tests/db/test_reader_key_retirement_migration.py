"""Retire every tenant's reader identity without changing writer authority."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from alembic import command
from app.models import AuthorizationBinding, IntegrationKey, Organization, RefreshToken, User
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    forward_migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema"]
pytestmark = pytest.mark.committing_db


@pytest.mark.skipif(os.getenv("TEST_DATABASE_URL") is None, reason="Postgres is required.")
def test_retirement_revokes_readers_across_tenants_and_retains_history(  # noqa: PLR0915
    forward_migrated_postgres_schema: MigratedPostgresSchema,
) -> None:
    schema = forward_migrated_postgres_schema
    config = alembic_config_for_app()
    command.downgrade(config, "202610100090")
    clear_database_caches()
    now = datetime.now(UTC)
    prior_revocation = now - timedelta(days=1)
    cases = [
        ("OR-RDR00001", "reader"),
        ("OR-RDR00002", "retired"),
        ("OR-RDR00003", "orphan"),
        ("OR-RDR00004", "writer"),
    ]
    identities: dict[str, UUID] = {}
    for org_id, kind in cases:
        with schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org_id}
            )
            with Session(connection) as db:
                db.add(Organization(id=org_id, name="Synthetic retirement proof"))
                db.flush()
                user = User(
                    organization_id=org_id,
                    email=f"{kind}@service.aequoros.invalid",
                    auth_provider="service",
                    role="viewer",
                    is_active=True,
                    authorization_version=7,
                )
                db.add(user)
                db.flush()
                binding = AuthorizationBinding(
                    organization_id=org_id,
                    principal_user_id=user.id,
                    principal_type="machine",
                    role_bundle="integration_writer" if kind == "writer" else "bi_reader",
                    institution_scope="organization",
                    institution_id=None,
                    module_scope="data" if kind == "writer" else "all",
                    sensitivity_scope="restricted" if kind == "writer" else "aggregated",
                    granted_by_type="system",
                    granted_by_id="test-suite",
                    grant_reason="Synthetic migration evidence",
                    status="revoked" if kind == "retired" else "active",
                    revoked_at=prior_revocation if kind == "retired" else None,
                    revoked_by_type="system" if kind == "retired" else None,
                    revoked_by_id="previous-revocation" if kind == "retired" else None,
                    revoked_reason="Previous retirement" if kind == "retired" else None,
                )
                db.add(binding)
                if kind != "orphan":
                    db.add(
                        IntegrationKey(
                            organization_id=org_id,
                            service_user_id=user.id,
                            label=kind,
                            key_prefix="aeq_live_TEST…",
                            key_hash=uuid4().hex * 2,
                            revoked_at=prior_revocation if kind == "retired" else None,
                        )
                    )
                db.add(
                    RefreshToken(
                        organization_id=org_id,
                        user_id=user.id,
                        family_id=uuid4(),
                        token_hash=uuid4().hex * 2,
                        issued_at=now,
                        expires_at=now + timedelta(days=1),
                    )
                )
                db.flush()
                identities[org_id] = user.id
                db.commit()

    command.upgrade(config, "head")
    clear_database_caches()
    for org_id, kind in cases:
        with schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": org_id}
            )
            with Session(connection) as db:
                user = db.get(User, identities[org_id])
                assert user is not None
                binding = db.scalar(select(AuthorizationBinding))
                token = db.scalar(select(RefreshToken))
                key = db.scalar(
                    select(IntegrationKey).where(IntegrationKey.organization_id == org_id)
                )
                assert binding is not None and token is not None
                if kind == "writer":
                    assert user.is_active and user.authorization_version == 7
                    assert binding.status == "active" and binding.revoked_at is None
                    assert token.revoked_at is None
                    assert key is not None and key.revoked_at is None
                else:
                    assert not user.is_active and user.authorization_version == 8
                    assert binding.status == "revoked" and binding.revoked_at is not None
                    assert token.revoked_at is not None
                    assert token.revoked_reason == "authorization_changed"
                    if kind == "orphan":
                        assert key is None
                    else:
                        assert key is not None and key.revoked_at is not None
                    if kind == "retired":
                        assert binding.revoked_at == prior_revocation
                        assert binding.revoked_reason == "Previous retirement"
                        assert key is not None and key.revoked_at == prior_revocation
                    else:
                        assert binding.revoked_by_id == "migration:202610110090"

    # Rolling back code/schema must not reactivate any credential or session.
    command.downgrade(config, "202610100090")
    clear_database_caches()
    with schema.app_engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM integration_keys WHERE revoked_at IS NULL")
            )
            == 1
        )
