"""Exercise the real PostgreSQL chain, including privileged tampering."""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.core.audit_integrity import scheduled_verification, verify_chains
from app.db.session import force_rls_suspended
from tests.db import test_governance_append_only as governance_fixtures
from tests.db.test_postgres_migrations import MigratedPostgresSchema
from tests.support.helpers import ORG_1

governance_schema = governance_fixtures.governance_schema
tenant_connection = governance_fixtures.connection

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


@pytest.fixture
def connection(tenant_connection: Connection) -> Iterator[Connection]:
    with force_rls_suspended(tenant_connection, "audit_events", "operator_audit_log"):
        tenant_connection.execute(text("SET LOCAL row_security = off"))
        tenant_connection.execute(
            text(
                "GRANT UPDATE, DELETE, TRUNCATE ON audit_events, operator_audit_log, "
                "audit_chain_entries TO CURRENT_USER"
            )
        )
        yield tenant_connection


def _append(connection: Connection) -> None:
    connection.execute(
        text("""
        INSERT INTO audit_events (id, organization_id, event_type, details, created_at)
        VALUES (:id, :org, 'integrity.probe', '{"value": 1}', now())
    """),
        {"id": uuid4(), "org": ORG_1},
    )


def test_concurrent_writers_form_one_complete_chain(
    governance_schema: MigratedPostgresSchema,
) -> None:
    def write(_: int) -> None:
        with governance_schema.app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            for _ in range(5):
                _append(connection)

    with ThreadPoolExecutor(max_workers=4) as writers:
        list(writers.map(write, range(4)))
    with (
        governance_schema.app_engine.begin() as connection,
        force_rls_suspended(connection, "audit_events", "operator_audit_log"),
    ):
        connection.execute(text("SET LOCAL row_security = off"))
        with Session(bind=connection) as session:
            result = verify_chains(session)
            assert result[0].entries >= 20
            assert all(item.valid for item in result)


def test_added_source_columns_preserve_existing_hashes(connection: Connection) -> None:
    _append(connection)
    connection.execute(
        text("ALTER TABLE audit_events ADD COLUMN integrity_probe text DEFAULT 'new'")
    )
    _append(connection)
    with Session(bind=connection) as session:
        assert all(item.valid for item in verify_chains(session))


def test_chain_serializes_inserts_and_rolls_back_with_event(connection: Connection) -> None:
    _append(connection)
    with force_rls_suspended(connection, "audit_events", "operator_audit_log"):
        connection.execute(text("SET LOCAL row_security = off"))
        with Session(bind=connection) as session:
            before = verify_chains(session)
            assert all(item.valid for item in before)
            savepoint = connection.begin_nested()
            _append(connection)
            assert verify_chains(session)[0].entries == before[0].entries + 1
            savepoint.rollback()
            assert verify_chains(session) == before


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_audit_and_chain_are_append_only(connection: Connection, operation: str) -> None:
    _append(connection)
    connection.execute(
        text("""
        INSERT INTO operator_audit_log (id, operator_email, auth_mode, action, detail, created_at)
        VALUES (:id, 'synthetic@example.test', 'dev', 'integrity.probe', '{}', now())
    """),
        {"id": uuid4()},
    )
    with Session(bind=connection) as session:
        assert all(item.valid for item in verify_chains(session))
    for table, assignment in (
        ("audit_events", "event_type = 'rewritten'"),
        ("operator_audit_log", "action = 'rewritten'"),
        ("audit_chain_entries", "entry_hash = repeat('0', 64)"),
    ):
        savepoint = connection.begin_nested()
        sql = (
            f"UPDATE {table} SET {assignment}" if operation == "UPDATE" else f"DELETE FROM {table}"
        )
        with pytest.raises(DatabaseError, match="append-only"):
            connection.execute(text(sql))
        savepoint.rollback()


@pytest.mark.parametrize("tamper", ["content", "tail", "middle"])
def test_privileged_tampering_is_detected_and_alerted(
    connection: Connection,
    tamper: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _append(connection)
    _append(connection)
    # Model a migration owner overriding the guard; application roles cannot.
    connection.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
    connection.execute(text("ALTER TABLE audit_chain_entries DISABLE TRIGGER USER"))
    if tamper == "content":
        connection.execute(text("UPDATE audit_events SET details = '{\"value\": 99}'"))
    else:
        direction = "DESC" if tamper == "tail" else "ASC"
        connection.execute(
            text(f"""
            DELETE FROM audit_chain_entries WHERE stream = 'audit_events'
            AND sequence = (SELECT sequence FROM audit_chain_entries
                WHERE stream = 'audit_events' ORDER BY sequence {direction} LIMIT 1)
        """)
        )
    calls: list[object] = []

    def capture(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("app.core.audit_integrity.emit", capture)
    pages: list[tuple[str, str | None]] = []

    def page(reason: str, stream: str | None = None) -> bool:
        pages.append((reason, stream))
        return True

    monkeypatch.setattr("app.core.audit_integrity.publish_alert", page)
    with force_rls_suspended(connection, "audit_events", "operator_audit_log"):
        connection.execute(text("SET LOCAL row_security = off"))
        with Session(bind=connection) as session:
            results = scheduled_verification(session)
    assert not results[0].valid
    assert results[1].valid
    assert len(calls) == 1
    assert pages == [("chain_mismatch", "audit_events")]
