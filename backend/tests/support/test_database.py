"""Behavioral contract for managed synthetic test connections."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import IntegrityError, ResourceClosedError

from tests.support.database import (
    DatabaseContext,
    TestDatabaseConnections,
    database_connections,
    install_migration_engines,
    isolated_database,
    managed_engine,
    managed_session_factory,
)


def test_connections_share_data_and_close_resources(tmp_path: Path) -> None:
    calls: list[Engine] = []
    with isolated_database(f"sqlite:///{tmp_path / 'managed.db'}", hooks=(calls.append,)) as db:
        engine = db.engine()
        with engine.begin() as connection:
            _ = connection.execute(text("CREATE TABLE probe (id integer primary key)"))
            _ = connection.execute(text("INSERT INTO probe VALUES (1)"))
        with db.session() as session:
            assert session.scalar(text("SELECT count(*) FROM probe")) == 1
        # Every extra pool takes the hook, and sees this test's same database.
        with db.engine().connect() as other:
            assert other.scalar(text("SELECT count(*) FROM probe")) == 1
        maker = db.session_factory()
        leaked_session = maker()
        _ = leaked_session.execute(text("INSERT INTO probe VALUES (2)"))
        leaked_connection = engine.connect()
        assert len(calls) == 3
    assert leaked_connection.closed
    assert not leaked_session.in_transaction()
    with pytest.raises(RuntimeError, match="already been closed"):
        _ = maker()
    with pytest.raises(RuntimeError, match="already been closed"):
        _ = db.engine()
    with pytest.raises(ResourceClosedError):
        _ = leaked_connection.execute(text("SELECT 1"))
    with pytest.raises(RuntimeError, match="already been closed"), engine.connect():
        pass
    with (
        isolated_database(f"sqlite:///{tmp_path / 'managed.db'}") as reopened,
        reopened.connection() as connection,
    ):
        assert connection.scalar(text("SELECT count(*) FROM probe")) == 1


def test_session_factory_preserves_outer_rollback_and_context(tmp_path: Path) -> None:
    with isolated_database(f"sqlite:///{tmp_path / 'rollback.db'}") as db:
        with db.engine().begin() as setup:
            _ = setup.execute(text("CREATE TABLE probe (value integer)"))
        tenant = db.with_context(DatabaseContext(organization_id="OR-TEST", bank_id="BK-TEST"))
        with tenant.connection() as connection:
            transaction = connection.begin()
            _ = connection.exec_driver_sql("BEGIN")
            with tenant.session(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as session:
                assert session.info["organization_id"] == "OR-TEST"
                assert session.info["bank_id"] == "BK-TEST"
                _ = session.execute(text("INSERT INTO probe VALUES (1)"))
                session.commit()
            transaction.rollback()
        with db.connection() as connection:
            assert connection.scalar(text("SELECT count(*) FROM probe")) == 0


def test_sqlite_connections_enforce_foreign_keys(tmp_path: Path) -> None:
    # This contract is specifically SQLite even in a Postgres integration job.
    with (
        isolated_database(f"sqlite:///{tmp_path / 'foreign-keys.db'}") as db,
        db.connection() as connection,
    ):
        _ = connection.execute(text("CREATE TABLE parent (id integer primary key)"))
        _ = connection.execute(text("CREATE TABLE child (parent_id integer REFERENCES parent(id))"))
        connection.commit()
        with pytest.raises(IntegrityError):
            _ = connection.execute(text("INSERT INTO child VALUES (123)"))


def test_context_validation_fails_before_connecting() -> None:
    with pytest.raises(ValueError, match="explicit disposable schema"):
        _ = TestDatabaseConnections("postgresql+psycopg://unused/test")
    with pytest.raises(ValueError, match="Invalid test database identifier"):
        _ = TestDatabaseConnections(
            "postgresql+psycopg://unused/test", context=DatabaseContext(schema="unsafe; SQL")
        )
    with TestDatabaseConnections("sqlite://") as db:
        with pytest.raises(ValueError, match="owning test schema"):
            _ = db.with_context(DatabaseContext(schema="another_test"))
        with pytest.raises(ValueError, match="psycopg driver"), db.raw_connection():
            pass
        with (
            TestDatabaseConnections("sqlite://") as other,
            pytest.raises(ValueError, match="must belong"),
        ):
            _ = db.session_factory(bind=other.engine())


postgres_only = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")


def _assert_context(db: TestDatabaseConnections, engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_schema()")) == db.context.schema
        assert connection.scalar(text("SELECT current_user")) == db.context.role
        assert connection.scalar(text("SELECT current_setting('app.organization_id')")) == "OR-TEST"
        assert connection.scalar(text("SELECT current_setting('app.bank_id')")) == "BK-TEST"


@postgres_only
def test_postgres_raw_extra_role_and_migration_connections_are_scoped() -> None:
    url = os.environ["TEST_DATABASE_URL"]
    calls: list[Engine] = []
    schema: str | None = None
    with isolated_database(url, hooks=(calls.append,)) as owner:
        schema = owner.context.schema
        with owner.connection() as connection:
            role = cast(str, connection.scalar(text("SELECT current_user")))
        db = owner.with_context(
            replace(owner.context, organization_id="OR-TEST", bank_id="BK-TEST", role=role)
        )
        engine = db.engine(pool_size=1, max_overflow=0)
        _assert_context(db, engine)
        with engine.connect() as connection:
            _ = connection.execute(text("SET search_path TO public"))
            _ = connection.execute(
                text("SELECT set_config('app.organization_id', 'polluted', false)")
            )
            connection.commit()
        _assert_context(db, engine)
        _assert_context(db, db.migration_engine())
        with db.session() as session:
            assert (
                session.scalar(text("SELECT current_setting('app.organization_id')")) == "OR-TEST"
            )
            session.commit()
            assert session.scalar(text("SELECT current_setting('app.bank_id')")) == "BK-TEST"
        with db.raw_connection() as raw:
            for _ in range(2):
                row = raw.execute(
                    "SELECT current_schema(), current_user, "
                    "current_setting('app.organization_id'), current_setting('app.bank_id')"
                ).fetchone()
                assert row == (schema, role, "OR-TEST", "BK-TEST")
                raw.rollback()
        assert len(calls) == 4
    # The schema is removed even though several independently pooled engines used it.
    with isolated_database(url) as observer, observer.connection() as connection:
        assert schema not in inspect(connection).get_schema_names()


@postgres_only
def test_async_connections_receive_isolation_hooks_and_context() -> None:
    url = os.environ["TEST_DATABASE_URL"]
    seen: list[str | None] = []

    def hook(engine: Engine) -> None:
        def on_connect(_connection: object, _record: object) -> None:
            seen.append(db.context.schema)

        event.listen(engine, "connect", on_connect)

    with isolated_database(url, organization_id="OR-ASYNC", hooks=(hook,)) as db:

        async def run() -> None:
            async with db.async_engine() as engine, engine.connect() as connection:
                assert await connection.scalar(text("SELECT current_schema()")) == db.context.schema
                assert (
                    await connection.scalar(text("SELECT current_setting('app.organization_id')"))
                    == "OR-ASYNC"
                )

        asyncio.run(run())
    assert seen == [db.context.schema]


@postgres_only
def test_role_variant_changes_database_authority() -> None:
    with isolated_database(os.environ["TEST_DATABASE_URL"]) as db:
        with db.connection() as connection:
            available = cast(
                bool,
                connection.scalar(
                    text(
                        "SELECT CASE WHEN EXISTS "
                        "(SELECT 1 FROM pg_roles WHERE rolname = 'aqs_owner') "
                        "THEN pg_has_role(current_user, 'aqs_owner', 'MEMBER') ELSE false END"
                    )
                ),
            )
            if not available:
                pytest.skip("Role-admin test setup required for distinct-role coverage")
            original_role = cast(str, connection.scalar(text("SELECT current_user")))
            assert original_role != "aqs_owner"
            schema = db.context.schema
            assert schema is not None
            _ = connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO aqs_owner'))
            connection.commit()
        role_db = db.with_context(replace(db.context, role="aqs_owner"))
        with role_db.connection() as connection:
            assert connection.scalar(text("SELECT current_user")) == "aqs_owner"
            assert connection.scalar(text("SELECT current_schema()")) == schema
        with db.connection() as connection:
            assert connection.scalar(text("SELECT current_user")) == original_role


class FixtureLifetime:
    def __init__(self) -> None:
        self.resources: ExitStack = ExitStack()

    def addfinalizer(self, finalizer: Callable[[], None]) -> None:
        _ = self.resources.callback(finalizer)


def test_fixture_factory_closes_sessions_and_preserves_explicit_tenant(tmp_path: Path) -> None:
    lifetime = FixtureLifetime()
    url = f"sqlite:///{tmp_path / 'fixture.db'}"
    engine = managed_engine(lifetime, url)
    with engine.begin() as connection:
        _ = connection.execute(text("CREATE TABLE probe (value integer)"))
    maker = managed_session_factory(lifetime, engine, info={"organization_id": "OR-EXPLICIT"})
    session = maker()
    assert session.info["organization_id"] == "OR-EXPLICIT"
    assert session.autoflush
    assert session.expire_on_commit
    _ = session.execute(text("INSERT INTO probe VALUES (1)"))
    lifetime.resources.close()
    assert not session.in_transaction()
    with pytest.raises(RuntimeError, match="already been closed"), engine.connect():
        pass
    with database_connections(url) as database, database.connection() as connection:
        assert connection.scalar(text("SELECT count(*) FROM probe")) == 0


def test_alembic_factory_keeps_database_hooks_and_ownership(tmp_path: Path) -> None:
    hooks: list[Engine] = []
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    with (
        database_connections(url, hooks=(hooks.append,)) as database,
        pytest.MonkeyPatch.context() as patch,
    ):
        engine = database.engine()
        install_migration_engines(patch, engine)
        migrated = sqlalchemy.engine_from_config({"sqlalchemy.url": url})
        with migrated.begin() as connection:
            _ = connection.execute(text("CREATE TABLE probe (value integer)"))
        with engine.connect() as connection:
            assert "probe" in inspect(connection).get_table_names()
        assert hooks == [engine, migrated]
        with pytest.raises(ValueError, match="owning test database and schema"):
            _ = sqlalchemy.engine_from_config(
                {"sqlalchemy.url": f"sqlite:///{tmp_path / 'another.db'}"}
            )
    with pytest.raises(RuntimeError, match="already been closed"), migrated.connect():
        pass
