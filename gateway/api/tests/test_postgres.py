# pyright: standard
"""Postgres-only behaviour: advisory-locked gapless appends, gapless receipt numbers and
the API role's grants. Runs when GATEWAY_TEST_DATABASE_URL is set."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from app.audit.chain import SYSTEM, append, verify
from app.core.sequences import receipt_id
from app.db.migrate import upgrade
from app.db.models import AuditEntry
from tests.harness import Harness, pg_url


def test_concurrent_appends_stay_gapless_and_linear(pg_harness: Harness) -> None:
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            barrier.wait()
            for i in range(10):
                with pg_harness.ctx.db.session() as session:
                    append(
                        session,
                        pg_harness.ctx.keys,
                        pg_harness.ctx.clock,
                        actor=SYSTEM,
                        category="system",
                        action=f"worker {n} entry {i}",
                        object_type="test",
                        object_ref=f"{n}-{i}",
                    )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    with pg_harness.ctx.db.session() as session:
        seqs = [row[0] for row in session.query(AuditEntry.seq).order_by(AuditEntry.seq).all()]
        assert seqs == list(range(1, 81))
        assert verify(session, pg_harness.ctx.keys).intact


def test_receipt_numbers_are_gapless_under_concurrency_and_rollback(pg_harness: Harness) -> None:
    from datetime import UTC, datetime

    moment = datetime(2026, 11, 21, tzinfo=UTC)
    with pytest.raises(RuntimeError), pg_harness.ctx.db.session() as session:
        receipt_id(session, moment)
        raise RuntimeError("roll back")
    numbers: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(5):
            with pg_harness.ctx.db.session() as session:
                value = receipt_id(session, moment)
            with lock:
                numbers.append(value)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(numbers) == [f"SRG-26-{n:07d}" for n in range(1, 31)]


@pytest.fixture
def role_schema() -> Iterator[tuple[str, str, str]]:
    url = pg_url()
    if url is None:
        pytest.skip("GATEWAY_TEST_DATABASE_URL is not set")
    suffix = uuid.uuid4().hex[:8]
    schema, role = f"srg_grants_{suffix}", f"srg_api_{suffix}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
        connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
    try:
        upgrade(url, schema=schema, app_role=role)
        yield url, schema, role
    finally:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def test_api_role_cannot_update_or_delete_evidence(role_schema: tuple[str, str, str]) -> None:
    url, schema, role = role_schema
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    insert_entry = text(
        "INSERT INTO audit_entries (seq, occurred_at, actor_type, actor_ref, actor_label, actor_via, category, action,"
        " object_type, object_ref, institution_code, detail, prev_hash, entry_hash, signature, signature_kid)"
        " VALUES (1, now(), 'system', 's', 'S', 'v', 'system', 'a', 't', 'r', NULL, '{}', :z, :h, 'sig', 'k')"
    )
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL ROLE {role}"))
        connection.execute(insert_entry, {"z": "0" * 64, "h": "1" * 64})
        assert connection.execute(text("SELECT count(*) FROM audit_entries")).scalar() == 1
        connection.execute(
            text("UPDATE counters SET value = value WHERE false")
        )  # ordinary tables are writable
    for statement in (
        "UPDATE audit_entries SET action = 'x'",
        "DELETE FROM audit_entries",
        "DELETE FROM submissions",
        "DELETE FROM receipts",
        "TRUNCATE audit_entries",
    ):
        with pytest.raises(Exception, match="permission denied"), engine.begin() as connection:
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            connection.execute(text(statement))
    engine.dispose()


def test_the_api_role_reaches_every_table(role_schema: tuple[str, str, str]) -> None:
    """A table the API role cannot read is a feature that fails only once deployed.

    Locally the API connects as the schema owner, so a migration that forgets its
    grant passes every other test (staff_accounts and submission_documents did,
    2026-11-23: sign-in and filing would both have failed under the deployed role).
    """
    url, schema, role = role_schema
    engine = create_engine(url)
    with engine.begin() as connection:
        tables = connection.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = :schema "
                "AND tablename <> 'alembic_version'"
            ),
            {"schema": schema},
        ).scalars()
        unreachable = [
            table
            for table in tables
            if not connection.execute(
                text("SELECT has_table_privilege(:role, :table, 'SELECT, INSERT')"),
                {"role": role, "table": f"{schema}.{table}"},
            ).scalar()
        ]
        assert unreachable == []

        def may(table: str, privilege: str) -> object:
            return connection.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": role, "table": f"{schema}.{table}", "privilege": privilege},
            ).scalar()

        assert may("staff_accounts", "UPDATE")
        assert not may("staff_accounts", "DELETE")
        assert not may("submission_documents", "UPDATE")
        assert not may("submission_documents", "DELETE")
    engine.dispose()
