from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, bindparam, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


class RlsBlindError(RuntimeError):
    """Raised when a cross-tenant operation would silently see zero rows.

    On an RLS-forced Postgres a tenant-scoped role reading a tenant table with
    no ``app.organization_id`` set does not error — it matches nothing. Every
    caller in this module converts that into this exception instead, because a
    silent empty result is indistinguishable from "there is no work to do" and
    that ambiguity is what let the worker idle for weeks (audit P0-16) and a
    data migration no-op unnoticed (audit P0-18).
    """


_ROLE_CAPABILITY_SQL = text(
    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
)

_FORCED_RLS_TABLES_SQL = text(
    """
    SELECT c.relname
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relforcerowsecurity
      AND n.nspname = ANY (current_schemas(false))
      AND c.relname IN :table_names
    """
).bindparams(bindparam("table_names", expanding=True))

#: Tables the background worker must read across every tenant at once. ``jobs``
#: is the queue ``claim_next`` polls; it is FORCE-RLS (migration 202607180014).
_CROSS_TENANT_TABLES: tuple[str, ...] = ("jobs",)


@lru_cache
def get_engine(database_url: str) -> Engine:
    # The primary database is remote (~40ms away), so connection handling — not
    # query cost — dominates latency. Without TCP keepalives a firewall/NAT silently
    # drops idle pooled connections, forcing a full TLS+auth reconnect (~200ms) on the
    # next request; a small default pool also starves under the API + in-process worker
    # sharing it. Keep connections warm, size the pool for both, and recycle before any
    # server-side idle timeout.
    if database_url.startswith("postgresql"):
        return create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,
            connect_args={
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
    # SQLite (tests): keepalives/pool sizing don't apply.
    return create_engine(database_url, pool_pre_ping=True)


def get_sessionmaker() -> sessionmaker:
    settings = get_settings()
    if settings.database.database_url is None:
        msg = "DATABASE_URL is required to create database sessions."
        raise RuntimeError(msg)
    return sessionmaker(
        bind=get_engine(settings.database.database_url),
        autoflush=False,
        expire_on_commit=False,
    )


def get_worker_sessionmaker() -> sessionmaker:
    """Sessionmaker for the cross-tenant background worker.

    Production and staging workers require ``WORKER_DATABASE_URL``: accepting
    the tenant API role here makes a FORCE-RLS job queue look empty forever.
    Local SQLite remains intentionally simple for hermetic tests.
    """
    settings = get_settings()
    if (
        settings.app.app_env in {"production", "staging"}
        and settings.worker.worker_database_url is None
    ):
        msg = "WORKER_DATABASE_URL is required for production and staging workers."
        raise RuntimeError(msg)
    url = settings.worker.worker_database_url or settings.database.database_url
    if url is None:
        msg = "DATABASE_URL (or WORKER_DATABASE_URL) is required for the worker."
        raise RuntimeError(msg)
    return sessionmaker(
        bind=get_engine(url),
        autoflush=False,
        expire_on_commit=False,
    )


def role_bypasses_rls(connection: Connection) -> bool:
    """Whether this connection's role sees rows a FORCE-RLS policy would hide.

    Non-Postgres backends (SQLite in the hermetic suite) have no row-level
    security at all, so every role trivially sees everything.
    """
    if connection.dialect.name != "postgresql":
        return True
    return bool(connection.scalar(_ROLE_CAPABILITY_SQL))


def forced_rls_tables(connection: Connection, table_names: tuple[str, ...]) -> tuple[str, ...]:
    """Which of ``table_names`` currently have FORCE ROW LEVEL SECURITY on."""
    if connection.dialect.name != "postgresql" or not table_names:
        return ()
    found = connection.execute(
        _FORCED_RLS_TABLES_SQL, {"table_names": list(table_names)}
    ).scalars()
    return tuple(sorted(found))


@contextmanager
def force_rls_suspended(connection: Connection, *table_names: str) -> Iterator[tuple[str, ...]]:
    """Run a cross-tenant data statement so it can actually see every tenant.

    A data migration rewrites rows for ALL tenants at once, so it cannot set
    ``app.organization_id`` to any single value; on a FORCE-RLS table a
    tenant-scoped role's ``UPDATE`` therefore matches zero rows and reports
    success. Alembic runs as whichever role owns ``DATABASE_URL``, which in
    production is deliberately tenant-scoped — that is audit finding P0-18, and
    the remediation it shipped with was "remember to re-run it by hand under
    ``WORKER_DATABASE_URL``".

    This lifts FORCE for the duration instead. RLS stays ENABLED, so any other
    session keeps its policies; only the table OWNER — which is the role that
    created it through this same migration chain — is exempted, and only inside
    this transaction. When the role cannot lift it (it does not own the table),
    the failure is loud and names the fix rather than silently doing nothing.

    Postgres DDL is transactional, so an exception raised in the body rolls the
    suspension back with the rest of the migration.
    """
    if role_bypasses_rls(connection):
        yield ()
        return

    suspended = forced_rls_tables(connection, table_names)
    if not suspended:
        yield ()
        return

    try:
        for table_name in suspended:
            connection.exec_driver_sql(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    except SQLAlchemyError as exc:
        msg = (
            "Cross-tenant data migration cannot see tenant rows: "
            f"{', '.join(suspended)} enforce row-level security and the current role "
            "neither bypasses it nor owns the table, so every write would silently "
            "match zero rows. Re-run this migration with the BYPASSRLS worker role "
            "(WORKER_DATABASE_URL)."
        )
        raise RlsBlindError(msg) from exc

    yield suspended

    for table_name in suspended:
        connection.exec_driver_sql(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')


@dataclass(frozen=True)
class WorkerVisibility:
    """Whether the worker's connection can claim jobs across every tenant."""

    #: False only when the verdict is certain and negative.
    can_claim: bool
    #: The role name, when Postgres could be interrogated.
    role: str | None
    #: Human-readable reason, always populated.
    detail: str

    @property
    def blind(self) -> bool:
        return not self.can_claim


def worker_visibility() -> WorkerVisibility:
    """Report whether ``claim_next`` can match rows, without raising.

    ``jobs`` is FORCE-RLS (migration 202607180014) and the worker claims across
    tenants with no organization set, so a role without BYPASSRLS matches zero
    rows for every tenant, forever, with no error and no log. Readiness reads
    this so the condition is visible from outside the worker process.
    """
    try:
        engine = get_worker_sessionmaker().kw["bind"]
    except RuntimeError as exc:
        return WorkerVisibility(can_claim=False, role=None, detail=str(exc))

    if engine.dialect.name != "postgresql":
        return WorkerVisibility(
            can_claim=True,
            role=None,
            detail=f"{engine.dialect.name} has no row-level security.",
        )

    try:
        with engine.connect() as connection:
            role = connection.scalar(text("SELECT current_user"))
            bypasses = role_bypasses_rls(connection)
            forced = forced_rls_tables(connection, _CROSS_TENANT_TABLES)
    except SQLAlchemyError as exc:
        return WorkerVisibility(
            can_claim=False,
            role=None,
            detail=f"Worker database is unreachable: {exc.__class__.__name__}.",
        )

    if bypasses:
        return WorkerVisibility(
            can_claim=True, role=role, detail=f"{role} bypasses row-level security."
        )
    if not forced:
        return WorkerVisibility(
            can_claim=True,
            role=role,
            detail=f"{', '.join(_CROSS_TENANT_TABLES)} do not force row-level security.",
        )
    return WorkerVisibility(
        can_claim=False,
        role=role,
        detail=(
            f"{role} does not bypass row-level security, so cross-tenant claims on "
            f"{', '.join(forced)} match zero rows for every tenant. Point "
            "WORKER_DATABASE_URL at a BYPASSRLS role."
        ),
    )


def assert_worker_database_access() -> None:
    """Fail worker startup when its PostgreSQL role cannot claim cross-tenant jobs."""
    visibility = worker_visibility()
    if visibility.blind:
        raise RlsBlindError(visibility.detail)


@event.listens_for(Session, "after_begin")
def set_tenant_rls_context(
    session: Session,
    _transaction: Any,
    connection: Connection,
) -> None:
    organization_id = session.info.get("organization_id")
    if organization_id is None or connection.dialect.name != "postgresql":
        return

    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


def get_db_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
