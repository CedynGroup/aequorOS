"""Behavioral contract for managed synthetic test connections."""

from __future__ import annotations

import asyncio
import gc
import os
import sqlite3
import weakref
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
    adopt_application_engine,
    database_connections,
    install_application_engines,
    install_migration_engines,
    isolated_database,
    isolated_postgres_database,
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


def test_sqlite_connections_enforce_foreign_keys(
    test_db_connections: TestDatabaseConnections,
) -> None:
    if test_db_connections.url.drivername.startswith("postgresql"):
        pytest.skip("SQLite foreign-key connection setup")
    with test_db_connections.connection() as connection:
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


def test_closed_resources_can_be_collected_before_owner_exits(tmp_path: Path) -> None:
    with isolated_database(f"sqlite:///{tmp_path / 'lifetime.db'}") as db:
        engine = db.engine()
        with engine.connect() as connection:
            _ = connection.execute(text("SELECT 1"))
        connection_ref = weakref.ref(connection)
        maker = db.session_factory()
        with maker() as session:
            _ = session.execute(text("SELECT 1"))
        session_ref = weakref.ref(session)
        del connection, session
        _ = gc.collect()
        assert connection_ref() is None
        assert session_ref() is None
        with maker() as another:
            assert another.scalar(text("SELECT 2")) == 2


def test_adoption_preserves_application_engine_identity(tmp_path: Path) -> None:
    from app.db.session import get_engine  # noqa: PLC0415

    url = f"sqlite+pysqlite:///{tmp_path / 'adopted.db'}"
    engine = get_engine(url)
    hooks: list[Engine] = []
    try:
        with database_connections(url, hooks=(hooks.append,)) as owner:
            assert owner.adopt_engine(engine) is engine
            with pytest.raises(ValueError, match="already belongs"):
                _ = owner.adopt_engine(engine)
            wrong = get_engine(f"sqlite+pysqlite:///{tmp_path / 'other.db'}")
            try:
                with pytest.raises(ValueError, match="owning database URL"):
                    _ = owner.adopt_engine(wrong)
            finally:
                wrong.dispose()
            assert get_engine(url) is engine
            with managed_session_factory(None, engine)() as session:
                assert session.info == {}
                assert session.scalar(text("PRAGMA foreign_keys")) == 1
            leaked = engine.connect()
            assert hooks == [engine]
        assert leaked.closed
        with pytest.raises(RuntimeError, match="already been closed"), engine.connect():
            pass
    finally:
        get_engine.cache_clear()


def test_session_overrides_cannot_escape_owning_database(tmp_path: Path) -> None:
    with (
        isolated_database(f"sqlite:///{tmp_path / 'own.db'}") as own,
        isolated_database(f"sqlite:///{tmp_path / 'foreign.db'}") as foreign,
    ):
        maker = own.session_factory()
        foreign_engine = foreign.engine()
        with pytest.raises(ValueError, match="must belong"):
            _ = maker(bind=foreign_engine)
        maker.configure(bind=foreign_engine)
        with pytest.raises(ValueError, match="must belong"):
            _ = maker()
        maker.configure(bind=own.engine())
        with maker() as session:
            assert session.scalar(text("SELECT 42")) == 42
        with pytest.raises(ValueError, match="Multiple session binds"):
            _ = maker(binds={object(): foreign_engine})


def test_adopting_managed_engine_preserves_original_fixture_lifetime(tmp_path: Path) -> None:
    shorter = FixtureLifetime()
    with isolated_database(f"sqlite:///{tmp_path / 'owned.db'}") as database:
        engine = database.engine()
        assert adopt_application_engine(shorter, engine) is engine
        shorter.resources.close()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 42")) == 42


def test_native_sqlite_borrowers_roll_back_and_close_with_owner(tmp_path: Path) -> None:
    with isolated_database(f"sqlite:///{tmp_path / 'raw.db'}") as owner:
        with owner.sqlite_connection() as raw:
            assert raw.execute("PRAGMA foreign_keys").fetchone() == (1,)
            _ = raw.execute("CREATE TABLE probe (id integer primary key)")
            _ = raw.execute("INSERT INTO probe VALUES (1)")
            with pytest.raises(sqlite3.IntegrityError):
                _ = raw.execute("INSERT INTO probe VALUES (1)")
        with owner.sqlite_connection() as raw:
            assert raw.execute("SELECT count(*) FROM probe").fetchone() == (0,)
            owner.close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
                _ = raw.execute("SELECT 1")


@postgres_only
def test_raw_autocommit_is_explicit_and_leaked_borrowers_close() -> None:
    with isolated_database(os.environ["TEST_DATABASE_URL"]) as owner:
        with owner.raw_connection(autocommit=True) as raw:
            assert raw.autocommit
            autocommit_connection = raw
            _ = raw.execute("CREATE TABLE raw_probe (value integer)")
            _ = raw.execute("INSERT INTO raw_probe VALUES (1)")
        assert not autocommit_connection.autocommit
        with owner.raw_connection() as raw:
            assert not raw.autocommit
            _ = raw.execute("INSERT INTO raw_probe VALUES (2)")
        with owner.raw_connection() as raw:
            assert raw.execute("SELECT count(*) FROM raw_probe").fetchone() == (1,)
            owner.close()
            assert raw.closed


@pytest.mark.skipif(not os.getenv("POSTGRES_ADMIN_URL"), reason="Disposable cluster admin required")
def test_database_level_fixture_drops_its_database_and_closes_raw_borrowers() -> None:
    url = os.environ["POSTGRES_ADMIN_URL"]
    with isolated_postgres_database(url) as owner:
        name = owner.url.database
        assert name is not None
        assert name.startswith("risk_service_test_")
        with owner.raw_connection(autocommit=True) as raw:
            assert raw.execute("SELECT current_schema()").fetchone() == ("public",)
            _ = raw.execute("CREATE TABLE source_probe (value integer)")
            _ = raw.execute("INSERT INTO source_probe VALUES (1)")
        lease = owner.raw_connection()
        borrower = lease.__enter__()
        assert borrower.execute("SELECT count(*) FROM source_probe").fetchone() == (1,)
    assert borrower.closed
    lease.__exit__(None, None, None)
    with database_connections(url, administrative=True) as observer, observer.connection() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"), {"name": name}
            )
            == 0
        )


@pytest.mark.skipif(not os.getenv("POSTGRES_ADMIN_URL"), reason="Disposable cluster admin required")
def test_real_application_factory_receives_hooks_before_internal_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import session as application  # noqa: PLC0415

    url = os.environ["POSTGRES_ADMIN_URL"]
    checkouts: list[str | None] = []

    def hook(engine: Engine) -> None:
        def checked_out(_raw: object, _record: object, _proxy: object) -> None:
            checkouts.append(owner.context.schema)

        event.listen(engine, "checkout", checked_out)

    application.get_engine.cache_clear()
    with isolated_database(
        url, hooks=(hook,), organization_id="OR-TEST", bank_id="BK-TEST"
    ) as owner:
        source = owner.engine()
        install_application_engines(monkeypatch, source)
        engine = application.get_cross_tenant_engine(owner.database_url, "aequoros-worker", "TEST")
        assert checkouts == [owner.context.schema]
        assert application.get_engine(owner.database_url, "aequoros-worker") is engine
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == owner.context.schema
            assert connection.scalar(text("SHOW application_name")) == "aequoros-worker"
            assert connection.scalar(text("SHOW statement_timeout")) == "30min"
            assert (
                connection.scalar(text("SELECT current_setting('app.organization_id')"))
                == "OR-TEST"
            )
            assert connection.scalar(text("SELECT current_setting('app.bank_id')")) == "BK-TEST"
        with pytest.raises(ValueError, match="owning test database and schema"):
            _ = application.get_engine(
                engine.url.set(database="foreign").render_as_string(hide_password=False)
            )
    application.get_engine.cache_clear()
    with pytest.raises(RuntimeError, match="already been closed"), engine.connect():
        pass


@postgres_only
def test_plain_postgres_raw_url_keeps_schema_hooks_and_cleanup() -> None:
    hooks: list[Engine] = []
    with isolated_database(os.environ["TEST_DATABASE_URL"]) as schema:
        with (
            database_connections(
                schema.url.set(drivername="postgresql"),
                schema=schema.context.schema,
                hooks=(hooks.append,),
            ) as owner,
            owner.raw_connection() as raw,
        ):
            assert raw.execute("SELECT current_schema()").fetchone() == (schema.context.schema,)
            assert len(hooks) == 1
        assert raw.closed
