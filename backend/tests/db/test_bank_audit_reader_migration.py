"""Historical audit policies replay independently of current application SQL."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from alembic import command
from app.core import audit_attribution
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    migrated_postgres_schema,
)

__all__ = ["migrated_postgres_schema"]
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


def test_audit_reader_replays_after_application_ownership_changes(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = migrated_postgres_schema
    config = alembic_config_for_app()
    with schema.app_engine.connect() as connection:
        original = (
            connection.execute(
                text("""
                SELECT pg_get_functiondef(oid)
                FROM pg_proc
                WHERE pronamespace = current_schema()::regnamespace
                  AND proname IN ('bank_audit_previous_link', 'bank_audit_inspection_context')
                ORDER BY proname
            """)
            )
            .scalars()
            .all()
        )

    command.downgrade(config, "202610120099")
    assert schema.indexes({"ix_audit_events_subject"}) == set()

    # A future application subject must not require a table in an older schema,
    # and historical upgrades/downgrades must never consult today's SQL builders.
    monkeypatch.setitem(audit_attribution.SUBJECT_TABLES, "future_subject", "future_table")

    def refuse_current_builder(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Historical migration consulted current application ownership SQL")

    for name in ("subject_bank_sql", "staff_bank_sql", "organization_binding_sql", "uuid_sql"):
        monkeypatch.setattr(audit_attribution, name, refuse_current_builder)

    command.upgrade(config, "202610120100")
    assert schema.indexes({"ix_audit_events_subject"}) == {"ix_audit_events_subject"}
    with schema.app_engine.connect() as connection:
        replayed = (
            connection.execute(
                text("""
                SELECT pg_get_functiondef(oid)
                FROM pg_proc
                WHERE pronamespace = current_schema()::regnamespace
                  AND proname IN ('bank_audit_previous_link', 'bank_audit_inspection_context')
                ORDER BY proname
            """)
            )
            .scalars()
            .all()
        )
    assert replayed == original

    command.downgrade(config, "202610120099")
    command.upgrade(config, "202610120100")
