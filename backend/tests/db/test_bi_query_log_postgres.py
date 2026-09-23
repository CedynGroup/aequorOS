"""``bi_query_log`` is append-only on Postgres, and the writer routes partitions.

The log is the reviewable record of every BI read, so "append-only" has to be a
property of the DATABASE, not a habit of the service: a reviewer's confidence
rests on the fact that no code path — not the query path, not a later feature,
not a psql session holding the app role — can rewrite or remove a decision after
the fact. Migration ``202609220066`` states it three ways (the shared
``aequoros_append_only_guard`` row trigger, revoked UPDATE/DELETE/TRUNCATE, and
RESTRICTIVE policies), and this suite is where that is actually exercised.

It asks the question through the PARENT and directly on a CHILD partition,
because a partitioned table answers differently to each: the parent's trigger is
cloned onto every present and future child, but its revoked privileges and
RESTRICTIVE policies bind statements addressed to the parent, and a child
addressed by name carries only what the ensure-function put on it. A suite that
only tested the parent would pass over a child that could be rewritten by name.

Postgres-gated (``TEST_DATABASE_URL``) and skipped under a role that bypasses
RLS or owns the tables, because such a role proves nothing about the app role
the API actually runs as. D-011: never mocked as passing on SQLite.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command

# Imported for the side effect as much as for the name: importing
# ``app.db.session`` registers the ``after_begin`` listener that sets the GUC.
from app.db.session import set_tenant_rls_context
from app.models.bi import BiQueryLog
from app.services.bi import query_log
from tests.api.helpers import ORG_1
from tests.db.test_postgres_migrations import (
    MigratedPostgresSchema,
    alembic_config_for_app,
    clear_database_caches,
    postgres_schema_url,
)

_ = set_tenant_rls_context

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the BI query-log append-only proof.",
)

BANK_ID = "BK-BIQLOG001"
PRINCIPAL = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TABLE = BiQueryLog.__tablename__
#: The month the service writes into, and the child name the definer function
#: derives from it (``<parent>_yYYYYmMM``). The service stamps ``queried_at``
#: with the wall clock, so the month under test is the current one.
MONTH = dt.datetime.now(dt.UTC).date().replace(day=1)
CHILD = f"{TABLE}_y{MONTH:%Y}m{MONTH:%m}"
DEFAULT_PARTITION = f"{TABLE}_default"


def _record(**overrides: object) -> query_log.QueryRecord:
    fields: dict[str, object] = {
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "principal_user_id": PRINCIPAL,
        "surface": "query",
        "query_hash": "c" * 64,
        "decision": query_log.DECISION_ALLOWED,
        "catalogue_version": "1.0.0",
        "member_ids": ("loans.balance_rc",),
        "row_count": 2,
        "duration_ms": 11,
        "build_fingerprint": "d" * 64,
    }
    fields.update(overrides)
    return query_log.QueryRecord(**fields)  # type: ignore[arg-type] - a literal field map


@pytest.fixture(scope="module")
def log_schema() -> Iterator[MigratedPostgresSchema]:
    """A migrated disposable schema holding one tenant, one institution, and the
    September child created through the definer function."""

    test_database_url = os.environ["TEST_DATABASE_URL"]
    schema_name = f"risk_service_bi_qlog_{uuid4().hex}"
    database_url = postgres_schema_url(test_database_url, schema_name)
    monkeypatch = pytest.MonkeyPatch()
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    app_engine = create_engine(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        command.upgrade(alembic_config_for_app(), "head")
        with app_engine.connect() as connection:
            is_super, bypasses = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            connection.rollback()
            if is_super or bypasses:
                pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
            _seed_tenant(connection)
        yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
    finally:
        monkeypatch.undo()
        clear_database_caches()
        app_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _seed_tenant(connection: Connection) -> None:
    with connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": ORG_1},
        )
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:organization_id, 'Query log tenant', now(), now())"
            ),
            {"organization_id": ORG_1},
        )
        connection.execute(
            text(
                """
                INSERT INTO banks
                  (id, organization_id, name, short_name, currency, jurisdiction_code,
                   license_type, institution_type, created_at, updated_at)
                VALUES
                  (:bank_id, :organization_id, 'Query log bank', 'QLOG', 'GHS', 'GH',
                   'universal', 'universal_bank', now(), now())
                """
            ),
            {"bank_id": BANK_ID, "organization_id": ORG_1},
        )


def _session(schema: MigratedPostgresSchema) -> Session:
    session = Session(bind=schema.app_engine)
    session.info["organization_id"] = ORG_1
    return session


def _append(schema: MigratedPostgresSchema, **overrides: object) -> UUID:
    with _session(schema) as session:
        row = query_log.record(session, _record(**overrides))
        session.commit()
        return row.id


def _count(schema: MigratedPostgresSchema, relation: str) -> int:
    with schema.app_engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": ORG_1},
        )
        return int(connection.scalar(text(f"SELECT count(*) FROM {relation}")) or 0)


def _write(schema: MigratedPostgresSchema, statement: str) -> None:
    with schema.app_engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": ORG_1},
        )
        connection.execute(text(statement), {"organization_id": ORG_1})


# --- the writer ----------------------------------------------------------------------------


def test_the_service_creates_the_month_child_and_routes_the_row_into_it(
    log_schema: MigratedPostgresSchema,
) -> None:
    """The writer ensures its own partition: rows must never accumulate in the
    DEFAULT partition, because once they have, that month can never get a child
    (the conflicting-rows rule the migration documents)."""
    row_id = _append(log_schema)
    with log_schema.app_engine.connect() as connection, connection.begin():
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": ORG_1},
        )
        routed = connection.execute(
            text(f"SELECT tableoid::regclass::text FROM {TABLE} WHERE id = :id"),
            {"id": str(row_id)},
        ).scalar_one()
        child_exists = connection.scalar(
            text("SELECT count(*) FROM pg_class WHERE relname = :child"), {"child": CHILD}
        )
    assert child_exists == 1
    # The row for this month sits in its own child, not in DEFAULT.
    assert routed == CHILD


def test_the_stored_row_carries_every_column_the_contract_names(
    log_schema: MigratedPostgresSchema,
) -> None:
    row_id = _append(log_schema, surface="drill", row_count=7, duration_ms=42)
    with _session(log_schema) as session:
        stored = session.scalars(select(BiQueryLog).where(BiQueryLog.id == row_id)).one()
        assert stored.organization_id == ORG_1
        assert stored.bank_id == BANK_ID
        assert stored.principal_user_id == PRINCIPAL
        assert stored.principal_type == "human"
        assert stored.surface == "drill"
        assert stored.decision == query_log.DECISION_ALLOWED
        assert stored.query_hash == "c" * 64
        assert stored.member_ids == ["loans.balance_rc"]
        assert stored.denied_members == []
        assert stored.row_count == 7
        assert stored.duration_ms == 42
        assert stored.catalogue_version == "1.0.0"
        assert stored.build_fingerprint == "d" * 64
        assert stored.queried_at.tzinfo is not None


def test_the_budget_counts_only_the_rows_inside_the_window(
    log_schema: MigratedPostgresSchema,
) -> None:
    """``timestamptz`` comparison is the reason this is asserted on Postgres and
    not only on SQLite: the budget is a window over an indexed timestamp."""
    principal = uuid4()
    _append(log_schema, principal_user_id=principal)
    with _session(log_schema) as session:
        budget = query_log.budget_for(session, organization_id=ORG_1, principal_user_id=principal)
        assert budget.used == 1
        # A window that has already closed counts nothing.
        future = dt.datetime.now(dt.UTC) + dt.timedelta(
            seconds=query_log.RATE_LIMIT_WINDOW_SECONDS * 10
        )
        assert (
            query_log.budget_for(
                session,
                organization_id=ORG_1,
                principal_user_id=principal,
                now=future,
            ).used
            == 0
        )


# --- append-only ---------------------------------------------------------------------------


@pytest.mark.parametrize("relation", [TABLE, CHILD])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_a_rewrite_is_refused_through_the_parent_and_through_the_child(
    log_schema: MigratedPostgresSchema, relation: str, verb: str
) -> None:
    """Four statements, all refused: the parent and a child, updated and deleted.

    Which guard refuses is deliberately not asserted — the revoked privilege
    fires before the trigger for the app role, and either answer is the same
    promise. What is asserted is that the row is still there afterwards.
    """

    _append(log_schema)
    before = _count(log_schema, TABLE)
    assert before >= 1
    statement = (
        f"UPDATE {relation} SET row_count = 999 WHERE organization_id = :organization_id"
        if verb == "UPDATE"
        else f"DELETE FROM {relation} WHERE organization_id = :organization_id"
    )
    with pytest.raises(DBAPIError):
        _write(log_schema, statement)
    assert _count(log_schema, TABLE) == before


@pytest.mark.parametrize("relation", [TABLE, CHILD, DEFAULT_PARTITION])
def test_truncate_is_refused_everywhere(log_schema: MigratedPostgresSchema, relation: str) -> None:
    _append(log_schema)
    before = _count(log_schema, TABLE)
    with pytest.raises(DBAPIError):
        _write(log_schema, f"TRUNCATE {relation}")
    assert _count(log_schema, TABLE) == before


def test_the_rewrite_is_still_refused_when_the_privilege_is_granted_back(
    log_schema: MigratedPostgresSchema,
) -> None:
    """Defence in depth, isolated: the revoked privilege is what answers the app
    role, so it hides the other two layers. Granting UPDATE and DELETE back (the
    schema owner can, and an operator with a psql session eventually will) leaves
    the RESTRICTIVE policies and the row trigger — and a row a reviewer read
    yesterday must still say the same thing afterwards, whether the statement
    errored or matched nothing.
    """

    row_id = _append(log_schema, row_count=2)
    with log_schema.app_engine.connect() as connection, connection.begin():
        connection.execute(text(f"GRANT UPDATE, DELETE ON {TABLE} TO CURRENT_USER"))
        connection.execute(text(f"GRANT UPDATE, DELETE ON {CHILD} TO CURRENT_USER"))
    try:
        for relation in (TABLE, CHILD):
            for statement in (
                f"UPDATE {relation} SET row_count = 999 WHERE organization_id = :organization_id",
                f"DELETE FROM {relation} WHERE organization_id = :organization_id",
            ):
                # Either layer may answer — the trigger raises, a RESTRICTIVE
                # policy simply matches no row — and the promise is the same.
                with contextlib.suppress(DBAPIError):
                    _write(log_schema, statement)
        with _session(log_schema) as session:
            stored = session.scalars(select(BiQueryLog).where(BiQueryLog.id == row_id)).one()
            assert stored.row_count == 2
    finally:
        with log_schema.app_engine.connect() as connection, connection.begin():
            connection.execute(text(f"REVOKE UPDATE, DELETE ON {TABLE} FROM CURRENT_USER"))
            connection.execute(text(f"REVOKE UPDATE, DELETE ON {CHILD} FROM CURRENT_USER"))


def test_appending_is_the_one_write_that_is_admitted(
    log_schema: MigratedPostgresSchema,
) -> None:
    """The converse of the four refusals: the log must still be writable, or the
    guard would have closed the surface rather than made it append-only."""
    before = _count(log_schema, TABLE)
    _append(log_schema, surface="explain")
    _append(log_schema, surface="grid", decision=query_log.DECISION_DENIED, row_count=None)
    assert _count(log_schema, TABLE) == before + 2
    with _session(log_schema) as session:
        denied = session.scalar(
            select(func.count()).select_from(BiQueryLog).where(BiQueryLog.decision == "denied")
        )
    assert denied == 1
