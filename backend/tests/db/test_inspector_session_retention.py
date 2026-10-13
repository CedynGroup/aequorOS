"""Real PostgreSQL enforces indefinite inspector evidence retention."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
)
from tests.db.test_postgres_migrations import (
    migrated_postgres_schema as migrated_postgres_schema,  # noqa: PLC0414 - shared fixture
)
from tests.support.helpers import ORG_1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
def test_expired_ended_inspector_evidence_cannot_be_deleted_or_truncated(
    migrated_postgres_schema: MigratedPostgresSchema,  # noqa: F811 - shared fixture
) -> None:
    identifier = uuid4()
    with migrated_postgres_schema.app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
        connection.execute(
            text("""
                INSERT INTO organizations (id, name, created_at, updated_at)
                VALUES (:org, 'Synthetic retention tenant', now(), now())
            """),
            {"org": ORG_1},
        )
        connection.execute(
            text("""
                INSERT INTO operator_inspector_sessions
                  (id, organization_id, started_by, mode, reason, read_only,
                   started_at, expires_at)
                VALUES (:id, :org, 'synthetic@example.test', 'consent',
                        'Synthetic inspection', true,
                        now() - interval '10 years', now() - interval '10 years')
            """),
            {"id": identifier, "org": ORG_1},
        )
        # Ending an inspection still works; expiry never grants disposal authority.
        connection.execute(
            text("""
                UPDATE operator_inspector_sessions
                SET ended_at = now(), ended_by = 'synthetic@example.test'
                WHERE id = :id
            """),
            {"id": identifier},
        )
        for statement in (
            "DELETE FROM operator_inspector_sessions WHERE id = :id",
            "DELETE FROM organizations WHERE id = :org",
            "TRUNCATE operator_inspector_sessions",
            "TRUNCATE organizations CASCADE",
        ):
            with connection.begin_nested() as savepoint:
                with pytest.raises(DatabaseError, match="append-only|permission denied"):
                    connection.execute(text(statement), {"id": identifier, "org": ORG_1})
                savepoint.rollback()
        assert (
            connection.scalar(
                text("SELECT ended_by FROM operator_inspector_sessions WHERE id = :id"),
                {"id": identifier},
            )
            == "synthetic@example.test"
        )
