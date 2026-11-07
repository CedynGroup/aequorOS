"""Shared Postgres migration schema fixtures for migration and schema tests."""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, make_url

from alembic import command
from app.core.config import get_settings
from app.db.session import get_engine
from tests.support.database import database_connections


@dataclass(frozen=True)
class MigratedPostgresSchema:
    app_engine: Engine
    schema_name: str
    runtime_role: str | None = None

    def constraints(self, names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT c.conname
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE n.nspname = :schema_name
                          AND c.conname = ANY(:names)
                        """
                    ),
                    {"schema_name": self.schema_name, "names": sorted(names)},
                ).scalars()
            )

    def policies(self, table_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT policyname
                        FROM pg_policies
                        WHERE schemaname = :schema_name
                          AND tablename = ANY(:table_names)
                        """
                    ),
                    {
                        "schema_name": self.schema_name,
                        "table_names": sorted(table_names),
                    },
                ).scalars()
            )

    def tables(self, table_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = :schema_name
                          AND tablename = ANY(:table_names)
                        """
                    ),
                    {
                        "schema_name": self.schema_name,
                        "table_names": sorted(table_names),
                    },
                ).scalars()
            )

    def indexes(self, index_names: set[str]) -> set[str]:
        with self.app_engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = :schema_name
                          AND indexname = ANY(:index_names)
                        """
                    ),
                    {
                        "schema_name": self.schema_name,
                        "index_names": sorted(index_names),
                    },
                ).scalars()
            )


def postgres_schema_url(database_url: str, schema_name: str, *, role: str | None = None) -> str:
    url = make_url(database_url)
    options = f"-csearch_path={schema_name}"
    if role is not None:
        options = f"{options} -crole={role}"
    return url.update_query_dict({"options": options}).render_as_string(hide_password=False)


@pytest.fixture
def migrated_postgres_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigratedPostgresSchema]:
    """A schema migrated to head and downgraded to base again on teardown."""
    yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=True)


@pytest.fixture
def forward_migrated_postgres_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MigratedPostgresSchema]:
    """A schema migrated to head and dropped without downgrading.

    For suites whose seeded rows the older schemas cannot carry back (issued
    bank-scoped integration keys, ``enterprise_stress`` runs, ``bsd`` packages,
    service identities); the round trip itself is proven by
    :func:`migrated_postgres_schema`.
    """
    yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


@pytest.fixture(scope="module")
def catalog_postgres_schema() -> Iterator[MigratedPostgresSchema]:
    """Consumers share this schema and must leave its migrated state unchanged."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("TLS_ALLOW_PLAINTEXT", "1")
        yield from migrate_isolated_postgres_schema(monkeypatch, downgrade=False)


def migrate_isolated_postgres_schema(
    monkeypatch: pytest.MonkeyPatch, *, downgrade: bool, revision: str = "head"
) -> Generator[MigratedPostgresSchema]:
    """Migrate an isolated schema for a caller-owned fixture lifetime."""
    test_database_url = os.environ["TEST_DATABASE_URL"]
    if not make_url(test_database_url).drivername.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to Postgres.")

    schema_name = f"risk_service_migration_{uuid4().hex}"
    alembic_config = alembic_config_for_app()
    with ExitStack() as connections:
        admin_connections = connections.enter_context(
            database_connections(test_database_url, schema=schema_name)
        )
        admin_engine = admin_connections.engine(isolation_level="AUTOCOMMIT")
        migration_role = _prepare_migration_role(admin_engine)
        database_url = postgres_schema_url(test_database_url, schema_name, role=migration_role)
        app_connections = connections.enter_context(database_connections(database_url))
        app_engine = app_connections.engine()
        app_connections.install_migration_engines(monkeypatch)
        monkeypatch.setenv("DATABASE_URL", database_url)
        clear_database_caches()

        with admin_engine.connect() as connection:
            authorization = (
                f' AUTHORIZATION "{migration_role}"' if migration_role is not None else ""
            )
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"{authorization}'))

        try:
            command.upgrade(alembic_config, revision)
            yield MigratedPostgresSchema(app_engine=app_engine, schema_name=schema_name)
            if downgrade:
                command.downgrade(alembic_config, "base")
        finally:
            clear_database_caches()
            app_engine.dispose()
            with admin_engine.connect() as connection:
                _drop_worker_view_schema(connection, schema_name)
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                if migration_role is not None:
                    database_name = cast(str, connection.scalar(text("SELECT current_database()")))
                    quoted_database = connection.dialect.identifier_preparer.quote_identifier(
                        database_name
                    )
                    connection.execute(
                        text(f'REVOKE CREATE ON DATABASE {quoted_database} FROM "{migration_role}"')
                    )
                    connection.execute(text(f'DROP ROLE "{migration_role}"'))
            admin_engine.dispose()


def _prepare_migration_role(admin_engine: Engine) -> str | None:
    with admin_engine.connect() as connection:
        is_superuser = cast(
            "bool | None",
            connection.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")),
        )
    migration_role = f"risk_service_migrator_{uuid4().hex}" if is_superuser else None
    if is_superuser:
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(4130901)"))
            try:
                connection.execute(
                    text(
                        Path(__file__)
                        .parents[2]
                        .joinpath("scripts/database_roles_202610120096.sql")
                        .read_text()
                    )
                )
                connection.execute(
                    text(
                        Path(__file__)
                        .parents[2]
                        .joinpath("scripts/database_session_timeouts.sql")
                        .read_text()
                    )
                )
                # The per-grant ADMIN/INHERIT/SET options are PostgreSQL 16+. CI
                # runs 17 and production 15.13, so a fixture pinned to the newer
                # syntax cannot catch a migration that uses it — which is how one
                # reached production and took the API down.
                server_version = cast(
                    int, connection.exec_driver_sql("SHOW server_version_num").scalar_one()
                )
                # Match the effective inherited owner grants on PostgreSQL 15,
                # where inheritance is a role attribute rather than a grant
                # option. Ownership handoffs in the historical audit migrations
                # need these rights after ALTER TABLE ... OWNER.
                inheritance = "NOINHERIT" if int(server_version) >= 160000 else "INHERIT"
                connection.execute(
                    text(
                        f'CREATE ROLE "{migration_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS '
                        f"{inheritance} NOCREATEDB NOREPLICATION CREATEROLE"
                    )
                )
                admin_grant_options = (
                    "WITH ADMIN TRUE, INHERIT TRUE, SET TRUE"
                    if int(server_version) >= 160000
                    else "WITH ADMIN OPTION"
                )
                database_name = cast(str, connection.scalar(text("SELECT current_database()")))
                quoted_database = connection.dialect.identifier_preparer.quote_identifier(
                    database_name
                )
                connection.execute(
                    text(f'GRANT CREATE ON DATABASE {quoted_database} TO "{migration_role}"')
                )
                connection.execute(
                    text(f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles
                                       WHERE rolname = 'aequoros_audit_owner') THEN
                            CREATE ROLE aequoros_audit_owner NOLOGIN;
                        END IF;
                    END $$;
                    GRANT aqs_owner TO "{migration_role}" {admin_grant_options};
                    GRANT aequoros_audit_owner TO "{migration_role}"
                        {admin_grant_options};
                """)
                )
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(4130901)"))
    return migration_role


def _drop_worker_view_schema(connection: Connection, schema_name: str) -> None:
    # The worker's dedicated views live outside the tenant schema;
    # remove their disposable namespace before dropping its function.
    function = cast(
        str | None,
        connection.scalar(
            text("SELECT to_regprocedure(:signature)"),
            {"signature": f'"{schema_name}".aqs_worker_desk_schema()'},
        ),
    )
    if function is not None:
        worker_schema = cast(
            str | None,
            connection.scalar(text(f'SELECT "{schema_name}".aqs_worker_desk_schema()')),
        )
        if isinstance(worker_schema, str):
            quoted_worker_schema = connection.dialect.identifier_preparer.quote_identifier(
                worker_schema
            )
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_worker_schema} CASCADE"))


def alembic_config_for_app() -> Config:
    app_root = Path(__file__).parents[2]
    alembic_config = Config(str(app_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(app_root / "alembic"))
    return alembic_config


def clear_database_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()


def postgres_administrator_url() -> str:
    for database_url in (os.getenv("POSTGRES_ADMIN_URL"), os.getenv("TEST_DATABASE_URL")):
        if not database_url:
            continue
        with database_connections(database_url, administrative=True) as connections:
            engine = connections.engine()
            try:
                with engine.connect() as connection:
                    if (
                        connection.scalar(
                            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                        )
                        is True
                    ):
                        return database_url
            finally:
                engine.dispose()
    reason = "Superuser credentials are required for the administrator migration regression."
    if os.getenv("POSTGRES_PRIVILEGE_TESTS_REQUIRED"):
        pytest.fail(f"{reason} POSTGRES_PRIVILEGE_TESTS_REQUIRED is set.")
    pytest.skip(reason)


@pytest.fixture
def superuser_database_url() -> str:
    return postgres_administrator_url()
