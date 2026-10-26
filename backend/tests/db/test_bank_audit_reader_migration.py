"""Historical audit policies and indexes survive migration rollback and replay."""

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
    lineage_indexes = {
        "ix_canonical_positions_lineage_id",
        "ix_canonical_position_snapshots_lineage_id",
    }
    assert schema.indexes(lineage_indexes) == lineage_indexes

    def assert_audit_index_columns(expected: dict[str, list[str]]) -> None:
        with schema.app_engine.connect() as connection:
            rows = connection.execute(
                text("""
                    SELECT index_class.relname, array_agg(attribute.attname ORDER BY key.ordinality)
                    FROM pg_index AS idx
                    JOIN pg_class AS index_class ON index_class.oid = idx.indexrelid
                    CROSS JOIN LATERAL unnest(idx.indkey)
                        WITH ORDINALITY AS key(attnum, ordinality)
                    JOIN pg_attribute AS attribute
                      ON attribute.attrelid = idx.indrelid AND attribute.attnum = key.attnum
                    WHERE idx.indrelid = 'audit_events'::regclass
                      AND index_class.relname IN (
                          'ix_audit_events_subject', 'ix_audit_events_organization_id'
                      )
                    GROUP BY index_class.relname
                """)
            ).all()
        assert {row[0]: row[1] for row in rows} == expected

    assert_audit_index_columns({"ix_audit_events_subject": ["organization_id", "entity_id"]})
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

    command.downgrade(config, "202610140100")
    assert schema.indexes(lineage_indexes) == lineage_indexes
    assert schema.indexes({"ix_audit_events_subject"}) == set()
    assert_audit_index_columns({"ix_audit_events_organization_id": ["organization_id"]})

    # A future application subject must not require a table in an older schema,
    # and historical upgrades/downgrades must never consult today's SQL builders.
    monkeypatch.setitem(audit_attribution.SUBJECT_TABLES, "future_subject", "future_table")

    def refuse_current_builder(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Historical migration consulted current application ownership SQL")

    for name in ("subject_bank_sql", "staff_bank_sql", "organization_binding_sql", "uuid_sql"):
        monkeypatch.setattr(audit_attribution, name, refuse_current_builder)

    command.upgrade(config, "202610120100")
    assert schema.indexes(lineage_indexes) == lineage_indexes
    assert schema.indexes({"ix_audit_events_subject"}) == {"ix_audit_events_subject"}
    assert_audit_index_columns({"ix_audit_events_subject": ["organization_id", "entity_id"]})
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

    command.downgrade(config, "202610140100")
    assert schema.indexes(lineage_indexes) == lineage_indexes
    assert_audit_index_columns({"ix_audit_events_organization_id": ["organization_id"]})
    command.upgrade(config, "202610120100")
    assert schema.indexes(lineage_indexes) == lineage_indexes
    assert_audit_index_columns({"ix_audit_events_subject": ["organization_id", "entity_id"]})
