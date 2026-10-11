"""Exercise the real PostgreSQL chain, including privileged tampering."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app import worker
from app.core import audit_integrity
from app.core.audit_integrity import AuditIntegrityTask, scheduled_verification, verify_chains
from app.core.config import get_settings
from app.db.session import force_rls_suspended
from tests.db import test_governance_append_only as governance_fixtures
from tests.db import test_postgres_migrations as migration_fixtures
from tests.db.test_postgres_migrations import MigratedPostgresSchema
from tests.support.helpers import ORG_1

governance_schema = governance_fixtures.governance_schema
tenant_connection = governance_fixtures.connection
forward_migrated_postgres_schema = migration_fixtures.forward_migrated_postgres_schema

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


@pytest.fixture
def connection(tenant_connection: Connection) -> Iterator[Connection]:
    with force_rls_suspended(tenant_connection, "audit_events", "operator_audit_log"):
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
    AuditIntegrityTask().report(results)
    assert not results[0].valid
    assert results[1].valid
    assert len(calls) == 1
    assert pages == [("chain_mismatch", "audit_events")]


@pytest.mark.parametrize(
    "table", ["audit_events", "operator_audit_log", "audit_chain_entries", "audit_chain_heads"]
)
def test_runtime_role_cannot_alter_or_rewrite_history(
    connection: Connection, governance_schema: MigratedPostgresSchema, table: str
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    _append(connection)
    connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    connection.execute(text("SET LOCAL row_security = on"))
    assert not connection.scalar(
        text("SELECT pg_has_role(current_user, 'aequoros_audit_owner', 'MEMBER')")
    )
    for statement in (
        f"ALTER TABLE {table} DISABLE TRIGGER USER",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"DELETE FROM {table}",
        f"UPDATE {table} SET "
        + (
            "event_type = 'rewritten'"
            if table == "audit_events"
            else "action = 'rewritten'"
            if table == "operator_audit_log"
            else "entry_hash = repeat('0', 64)"
        ),
        f"TRUNCATE {table} CASCADE",
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="permission denied|must be owner"):
            connection.execute(text(statement))
        savepoint.rollback()
    connection.execute(text("RESET ROLE"))
    connection.execute(text("SET LOCAL row_security = off"))
    with Session(bind=connection) as session:
        assert all(result.valid for result in verify_chains(session))


def test_runtime_insert_uses_definer_but_cannot_forge_links(
    connection: Connection, governance_schema: MigratedPostgresSchema
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    with Session(bind=connection) as session:
        before = verify_chains(session)
    connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    connection.execute(text("SET LOCAL row_security = on"))
    _append(connection)
    connection.execute(
        text("""
        INSERT INTO operator_audit_log (id, operator_email, auth_mode, action, detail, created_at)
        VALUES (:id, 'synthetic@example.test', 'dev', 'integrity.probe', '{}', now())
    """),
        {"id": uuid4()},
    )
    for statement in (
        "INSERT INTO audit_chain_entries VALUES "
        "('audit_events', 999, gen_random_uuid(), '', '', '{}')",
        "SELECT aequoros_chain_append('audit_events', gen_random_uuid(), '{}'::jsonb)",
        "CREATE OR REPLACE FUNCTION aequoros_append_only_guard() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        "CREATE OR REPLACE FUNCTION aequoros_audit_chain_insert() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        "CREATE OR REPLACE FUNCTION aequoros_chain_append(text, uuid, jsonb) RETURNS void "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$",
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(DatabaseError, match="permission denied|must be owner"):
            connection.execute(text(statement))
        savepoint.rollback()
    connection.execute(text("RESET ROLE"))
    connection.execute(text("SET LOCAL row_security = off"))
    with Session(bind=connection) as session:
        results = verify_chains(session)
        assert all(result.valid for result in results)
        assert [result.entries for result in results] == [result.entries + 1 for result in before]


@pytest.mark.parametrize("failure", ["mismatch", "unavailable"])
def test_platform_schedule_runs_once_hourly_without_banks(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    now = datetime(2026, 10, 10, 12, 15, tzinfo=UTC)
    pages: list[str] = []
    verifications: list[Session] = []
    polls: list[tuple[str, ...]] = []
    stop = threading.Event()
    times = iter([now, now, now.replace(minute=59), now + timedelta(hours=1)])

    def verify(session: Session) -> list[audit_integrity.ChainVerification]:
        verifications.append(session)
        if failure == "unavailable":
            raise RuntimeError("verification unavailable")
        return [audit_integrity.ChainVerification("operator_audit_log", 1, False)]

    monkeypatch.setattr(audit_integrity, "verify_chains", verify)

    def page(reason: str, stream: str | None = None) -> bool:
        pages.append(reason)
        return True

    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    with Session(forward_migrated_postgres_schema.app_engine) as session:
        assert session.scalar(text("SELECT count(*) FROM banks")) == 0

    def poll(types: tuple[str, ...], *, worker_id: str) -> bool:
        polls.append(types)
        if len(polls) == 4:
            stop.set()
        return True

    def ignore(*args: object, **kwargs: object) -> None:
        pass

    monkeypatch.setattr(worker, "assert_worker_database_access", ignore)
    monkeypatch.setattr(worker, "_heartbeat_safely", ignore)
    monkeypatch.setattr(worker, "_reap_stale", ignore)
    monkeypatch.setattr(
        worker, "_new_session", lambda: Session(forward_migrated_postgres_schema.app_engine)
    )
    monkeypatch.setattr(worker, "run_once", poll)
    monkeypatch.setattr(worker, "utc_now", lambda: next(times))
    worker.run_worker(job_types=("scheduled_tick",), stop_event=stop)
    assert polls == [("scheduled_tick",)] * 4
    assert len(verifications) == 2
    reason = "chain_mismatch" if failure == "mismatch" else "verification_unavailable"
    assert pages == [reason]
    with Session(forward_migrated_postgres_schema.app_engine) as session:
        assert session.scalar(
            text("SELECT run_after FROM audit_integrity_schedule")
        ) == now.replace(hour=14, minute=0)


def test_peer_worker_cannot_repeat_locked_platform_sweep(
    governance_schema: MigratedPostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "true")
    get_settings.cache_clear()
    with governance_schema.app_engine.begin() as connection:
        connection.execute(text("UPDATE audit_integrity_schedule SET run_after = '-infinity'"))
    calls: list[Session] = []
    pages: list[tuple[str, str | None]] = []

    def page(reason: str, stream: str | None = None) -> bool:
        pages.append((reason, stream))
        return True

    monkeypatch.setattr(audit_integrity, "publish_alert", page)
    now = datetime(2026, 10, 10, 12, 15, tzinfo=UTC)
    with Session(governance_schema.app_engine) as peer:

        def verify(session: Session) -> list[audit_integrity.ChainVerification]:
            calls.append(session)
            assert not AuditIntegrityTask().run(lambda: nullcontext(peer), now)
            return [audit_integrity.ChainVerification("audit_events", 0, True)]

        monkeypatch.setattr(audit_integrity, "scheduled_verification", verify)
        with Session(governance_schema.app_engine) as first:
            assert AuditIntegrityTask().run(lambda: nullcontext(first), now)
            assert not AuditIntegrityTask().run(lambda: nullcontext(peer), now)
            assert calls == [first]
            assert pages == []


def test_chain_rls_hides_rows_even_with_runtime_select(
    connection: Connection, governance_schema: MigratedPostgresSchema
) -> None:
    role = governance_schema.runtime_role
    assert role is not None
    _append(connection)
    connection.execute(text(f'GRANT SELECT ON audit_chain_entries, audit_chain_heads TO "{role}"'))
    connection.execute(text(f'SET LOCAL ROLE "{role}"'))
    connection.execute(text("SET LOCAL row_security = on"))
    assert connection.scalar(text("SELECT count(*) FROM audit_chain_heads")) == 0
    assert connection.scalar(text("SELECT count(*) FROM audit_chain_entries")) == 0
    connection.execute(text("RESET ROLE"))
