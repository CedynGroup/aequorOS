"""Managed database connections for synthetic backend tests.

Use ``test_db_connections`` for a fresh per-test database, or
``isolated_database`` for an explicitly owned fixture lifetime. Engines returned
to tests, including extra pools and async engines, pass through the same hooks.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, Unpack, cast
from uuid import uuid4
from weakref import WeakSet

import psycopg
import pytest
import sqlalchemy
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import Pool

# Importing the application's listener makes session.info the authority for
# transaction-local tenant context, just as it is for application sessions.
from app.db import session as _app_session

_ = _app_session


class FinalizerOwner(Protocol):
    """The lifecycle operation supplied by a pytest fixture request."""

    def addfinalizer(self, finalizer: Callable[[], None]) -> None: ...


class PoolOptions(TypedDict, total=False):
    """Supported pool/driver controls; context and isolation remain mandatory."""

    poolclass: type[Pool]
    pool_size: int
    max_overflow: int
    pool_timeout: float
    pool_pre_ping: bool
    isolation_level: str
    echo: bool


class EngineOptions(PoolOptions, total=False):
    connect_args: Mapping[str, object]


class SessionDefaults(TypedDict, total=False):
    info: dict[str, object]
    autoflush: bool
    expire_on_commit: bool
    join_transaction_mode: Literal[
        "conditional_savepoint", "rollback_only", "control_fully", "create_savepoint"
    ]


class SessionOptions(SessionDefaults, total=False):
    bind: Engine | Connection


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[str, ...] = ()) -> object: ...
    def close(self) -> None: ...


class _DbapiConnection(Protocol):
    autocommit: bool

    def cursor(self) -> _Cursor: ...


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", value):
        raise ValueError(f"Invalid test database identifier: {value!r}")
    return f'"{value}"'


@dataclass(frozen=True)
class DatabaseContext:
    """Connection defaults; a missing tenant means explicitly unscoped access."""

    schema: str | None = None
    organization_id: str | None = None
    bank_id: str | None = None
    role: str | None = None


type EngineHook = Callable[[Engine], None]

_ENGINE_OWNERS: dict[Engine, TestDatabaseConnections] = {}


class TestDatabaseConnections:
    """Own connection resources for one fixture lifetime.

    Register isolation hooks before creating engines. Hooks receive both normal
    engines and the sync facade of async engines, before their first checkout.
    Context variants share resource ownership and hooks with their parent.
    """

    __test__ = False

    def __init__(
        self,
        database_url: str | URL,
        *,
        context: DatabaseContext | None = None,
        hooks: tuple[EngineHook, ...] = (),
    ) -> None:
        self.url: URL = make_url(database_url)
        self.context: DatabaseContext = context or DatabaseContext()
        context = self.context
        self._hooks: tuple[EngineHook, ...] = hooks
        self._resources: ExitStack = ExitStack()
        self._default_engine: Engine | None = None
        self._engines: set[Engine] = set()
        self._connections: WeakSet[Connection] = WeakSet()
        self._sessions: WeakSet[Session] = WeakSet()
        self._closed: bool = False
        if self.url.drivername.startswith("postgresql") and context.schema is None:
            raise ValueError("Postgres tests require an explicit disposable schema")
        if context.schema is not None:
            _ = _identifier(context.schema)
        if context.role is not None:
            _ = _identifier(context.role)

    @property
    def database_url(self) -> str:
        """A URL carrying the schema for consumers such as Alembic."""
        url = self.url
        if self.context.schema is not None and url.drivername.startswith("postgresql"):
            existing = " ".join(url.normalized_query.get("options", ()))
            options = f"{existing} -csearch_path={self.context.schema}"
            url = url.update_query_dict({"options": options.strip()})
        return url.render_as_string(hide_password=False)

    def _checkout(self, raw: object, _record: object, _proxy: object) -> None:
        self._check_open()
        if self.url.drivername.startswith("sqlite"):
            cursor = cast(_DbapiConnection, raw).cursor()
            try:
                _ = cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
            return
        connection = cast(_DbapiConnection, raw)
        previous = connection.autocommit
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            # Reapply at every checkout: a test may have changed these settings
            # before returning the connection to its pool.
            _ = cursor.execute("RESET ROLE")
            if self.context.role is not None:
                _ = cursor.execute(f"SET ROLE {_identifier(self.context.role)}")
            if self.context.schema is not None:
                _ = cursor.execute(f"SET search_path TO {_identifier(self.context.schema)}")
            _ = cursor.execute(
                "SELECT set_config('app.organization_id', %s, false), "
                "set_config('app.bank_id', %s, false)",
                (self.context.organization_id or "", self.context.bank_id or ""),
            )
        finally:
            cursor.close()
            connection.autocommit = previous

    def _track_connection(self, connection: Connection) -> None:
        self._connections.add(connection)

    def _configure(self, engine: Engine) -> None:
        event.listen(engine, "checkout", self._checkout)
        for hook in self._hooks:
            hook(engine)

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("Test database connections have already been closed")

    def engine(self, **options: Unpack[EngineOptions]) -> Engine:
        """An independent pool for concurrency or engine-behavior tests."""
        self._check_open()
        connect_args = dict(options.pop("connect_args", {}))
        pool_options = cast(PoolOptions, options)
        engine = create_engine(self.database_url, connect_args=connect_args, **pool_options)
        return self.adopt_engine(engine)

    def adopt_engine(self, engine: Engine) -> Engine:
        """Own an application-created engine before its first checkout.

        Keep the application cache identity while applying the shared hooks and
        cleanup. An engine cannot be adopted by two fixture lifetimes.
        """
        self._check_open()
        if engine in _ENGINE_OWNERS:
            raise ValueError("Engine already belongs to a test database factory")
        if engine.url not in (self.url, make_url(self.database_url)):
            raise ValueError("Adopted engine must use the owning database URL")
        self._resources.callback(engine.dispose)
        self._engines.add(engine)
        _ENGINE_OWNERS[engine] = self
        self._configure(engine)
        event.listen(engine, "engine_connect", self._track_connection)
        return engine

    def migration_engine(self, **options: Unpack[EngineOptions]) -> Engine:
        """An independent migration pool with the same schema, role and hooks."""
        return self.engine(**options)

    def _app_engine(self) -> Engine:
        if self._default_engine is None:
            self._default_engine = self.engine()
        return self._default_engine

    def session_factory(
        self,
        *,
        cleanup: Callable[[Callable[[], None]], None] | None = None,
        **options: Unpack[SessionOptions],
    ) -> sessionmaker[Session]:
        """A session factory, optionally bound to the test's outer transaction."""
        self._check_open()
        info = dict(options.pop("info", {}))
        bind = options.pop("bind", None)
        if bind is not None:
            engine = bind.engine if isinstance(bind, Connection) else bind
            if engine not in self._engines:
                raise ValueError("Session binds must belong to this test database factory")
        if self.context.organization_id is not None:
            info.setdefault("organization_id", self.context.organization_id)
        if self.context.bank_id is not None:
            info.setdefault("bank_id", self.context.bank_id)
        owner = self

        class ManagedSession(Session):
            def __init__(self, **kwargs: Unpack[SessionOptions]) -> None:
                owner._check_open()
                super().__init__(**kwargs)
                owner._sessions.add(self)
                if cleanup is not None:
                    cleanup(self.close)

        options.setdefault("autoflush", False)
        options.setdefault("expire_on_commit", False)
        options["bind"] = bind if bind is not None else self._app_engine()
        options["info"] = info
        return sessionmaker(class_=ManagedSession, **options)

    @contextmanager
    def session(self, **options: Unpack[SessionOptions]) -> Generator[Session]:
        """Close the session and roll back unfinished work on exit."""
        with self.session_factory(**options)() as session:
            yield session

    @contextmanager
    def connection(self) -> Generator[Connection]:
        """An application connection; unfinished transactions are rolled back."""
        self._check_open()
        with self._app_engine().connect() as connection:
            yield connection

    @contextmanager
    def raw_connection(self) -> Generator[psycopg.Connection[tuple[object, ...]]]:
        """A psycopg connection carrying the same checkout context as SQLAlchemy."""
        self._check_open()
        if self.url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("Raw connections require the PostgreSQL psycopg driver")
        raw = self._app_engine().raw_connection()
        try:
            yield cast(psycopg.Connection[tuple[object, ...]], raw.driver_connection)
        finally:
            try:
                raw.rollback()
            finally:
                raw.close()

    @asynccontextmanager
    async def async_engine(self, **options: Unpack[EngineOptions]) -> AsyncGenerator[AsyncEngine]:
        """Dispose the async pool in the event loop that used it."""
        self._check_open()
        connect_args = dict(options.pop("connect_args", {}))
        pool_options = cast(PoolOptions, options)
        engine = create_async_engine(self.database_url, connect_args=connect_args, **pool_options)
        try:
            self._configure(engine.sync_engine)
            yield engine
        finally:
            await engine.dispose()

    def with_context(self, context: DatabaseContext) -> TestDatabaseConnections:
        """A role/tenant variant in the same schema, owned by this factory."""
        self._check_open()
        if context.schema != self.context.schema:
            raise ValueError("Context variants must stay in the owning test schema")
        return self._resources.enter_context(
            TestDatabaseConnections(self.url, context=context, hooks=self._hooks)
        )

    def install_migration_engines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Apply this factory's isolation and ownership to Alembic-created pools."""

        def configured_engine(
            configuration: dict[str, object],
            prefix: str = "sqlalchemy.",
            **options: Unpack[EngineOptions],
        ) -> Engine:
            self._check_open()
            database_url = configuration.get(prefix + "url")
            if not isinstance(database_url, (str, URL)):
                raise ValueError("Migration configuration requires a database URL")
            database = database_connections(
                database_url, connect_args=options.get("connect_args"), hooks=self._hooks
            )
            source, target = self.url, database.url
            if (source.host, source.port or 5432, source.database) != (
                target.host,
                target.port or 5432,
                target.database,
            ) or database.context.schema != self.context.schema:
                raise ValueError(
                    "Migration engines must stay in the owning test database and schema"
                )
            _ = self._resources.enter_context(database)
            return database.migration_engine(**options)

        monkeypatch.setattr(sqlalchemy, "engine_from_config", configured_engine)

    def close(self) -> None:
        self._closed = True
        try:
            with ExitStack() as cleanup:
                cleanup.callback(self._resources.close)
                for connection in list(self._connections):
                    cleanup.callback(connection.close)
                for session in list(self._sessions):
                    cleanup.callback(session.close)
        finally:
            for engine in self._engines:
                if _ENGINE_OWNERS.get(engine) is self:
                    del _ENGINE_OWNERS[engine]

    def __enter__(self) -> TestDatabaseConnections:
        self._check_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@contextmanager
def isolated_database(
    database_url: str | URL,
    *,
    organization_id: str | None = None,
    bank_id: str | None = None,
    role: str | None = None,
    hooks: tuple[EngineHook, ...] = (),
) -> Generator[TestDatabaseConnections]:
    """Own a fresh Postgres schema or a caller-supplied temporary SQLite file.

    Postgres callers must pass TEST_DATABASE_URL, never the application's URL.
    The bootstrap connection only creates/drops the generated schema; all test
    work uses the scoped factory. No model tables or financial rows are seeded.
    """
    url = make_url(database_url)
    with ExitStack() as resources:
        schema: str | None = None
        if url.drivername.startswith("postgresql"):
            schema = f"risk_service_test_{uuid4().hex}"
            admin = create_engine(url, isolation_level="AUTOCOMMIT")
            resources.callback(admin.dispose)
            with admin.connect() as connection:
                owner = f" AUTHORIZATION {_identifier(role)}" if role is not None else ""
                _ = connection.execute(text(f"CREATE SCHEMA {_identifier(schema)}{owner}"))

            def drop_schema() -> None:
                with admin.connect() as connection:
                    _ = connection.execute(text(f"DROP SCHEMA {_identifier(schema)} CASCADE"))

            resources.callback(drop_schema)
        database = resources.enter_context(
            TestDatabaseConnections(
                url,
                context=DatabaseContext(schema, organization_id, bank_id, role),
                hooks=hooks,
            )
        )
        yield database


def _startup_settings(url: URL, connect_args: Mapping[str, object]) -> dict[str, str]:
    options = connect_args.get("options")
    if not isinstance(options, str):
        options = " ".join(url.normalized_query.get("options", ()))
    tokens = iter(shlex.split(options))
    settings: dict[str, str] = {}
    for token in tokens:
        setting = next(tokens, "") if token == "-c" else token.removeprefix("-c")
        if "=" in setting:
            key, value = setting.split("=", 1)
            settings[key] = value
    return settings


def database_connections(
    database_url: str | URL,
    *,
    schema: str | None = None,
    administrative: bool = False,
    connect_args: Mapping[str, object] | None = None,
    hooks: tuple[EngineHook, ...] = (),
) -> TestDatabaseConnections:
    """Own connections to an existing fixture's schema without creating it.

    Startup schema and role options are inherited, including options supplied
    through driver arguments. Administration without a tenant schema is explicit
    and uses pg_catalog: bootstrap/role probes cannot accidentally create tables
    in public. Callers use a context manager or register close with their fixture.
    """
    url = make_url(database_url)
    settings = _startup_settings(url, connect_args or {})
    context = DatabaseContext(
        schema=schema or settings.get("search_path") or ("pg_catalog" if administrative else None),
        role=settings.get("role"),
        organization_id=settings.get("app.organization_id"),
        bank_id=settings.get("app.bank_id"),
    )
    return TestDatabaseConnections(url, context=context, hooks=hooks)


def managed_engine(
    request: FinalizerOwner,
    database_url: str | URL,
    *,
    schema: str | None = None,
    administrative: bool = False,
    **options: Unpack[EngineOptions],
) -> Engine:
    """Construct an engine owned by the calling test or fixture's scope."""
    database = database_connections(
        database_url,
        schema=schema,
        administrative=administrative,
        connect_args=options.get("connect_args"),
    )
    request.addfinalizer(database.close)
    return database.engine(**options)


def managed_session_factory(
    request: FinalizerOwner | None,
    bind: Engine | Connection,
    **options: Unpack[SessionDefaults],
) -> sessionmaker[Session]:
    """Create sessions over a managed engine, closed by the calling fixture."""
    engine = bind.engine if isinstance(bind, Connection) else bind
    owner = _ENGINE_OWNERS.get(engine)
    if owner is None:
        raise ValueError("Session factories require an engine from tests.support.database")
    options.setdefault("autoflush", True)
    options.setdefault("expire_on_commit", True)
    session_options: SessionOptions = {**options, "bind": bind}
    cleanup = request.addfinalizer if request is not None else None
    return owner.session_factory(cleanup=cleanup, **session_options)


def install_migration_engines(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> None:
    """Route Alembic-created engines through the supplied engine's owning fixture."""
    owner = _ENGINE_OWNERS.get(engine)
    if owner is None:
        raise ValueError("Migrations require an engine from tests.support.database")
    owner.install_migration_engines(monkeypatch)
