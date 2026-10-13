from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from sqlalchemy import Engine, bindparam, create_engine, event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.core.config import get_settings
from app.core.tls import database_connect_args

_worker_tenant: ContextVar[tuple[str, str | None] | None] = ContextVar(
    "worker_tenant", default=None
)


@contextmanager
def worker_tenant_context(organization_id: str, bank_id: str | None) -> Generator[None]:
    """Carry a handler's tenant into separately opened storage sessions."""
    token = _worker_tenant.set((organization_id, bank_id))
    try:
        yield
    finally:
        _worker_tenant.reset(token)


def get_storage_session() -> Session:
    tenant = _worker_tenant.get()
    factory = get_worker_sessionmaker() if tenant is not None else get_sessionmaker()
    session = factory()
    if tenant is not None:
        session.info["organization_id"], session.info["bank_id"] = tenant
        session.info["worker_context"] = True
    return session


def apply_worker_tenant_context(session: Session) -> None:
    """Apply the active handler context before this session starts a transaction."""
    tenant = _worker_tenant.get()
    if tenant is not None:
        session.info["organization_id"], session.info["bank_id"] = tenant


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


def database_timeout_connect_args(database_url: str, application_name: str) -> dict[str, str]:
    """Scope legacy-role budgets to each connection; new roles use role defaults.

    Preserve URL options such as the disposable test schema's search_path.
    Migration connections explicitly opt out even when sharing a runtime login.
    """
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        return {}
    if application_name == "aequoros-migrator":
        idle_ms, statement_ms = 0, 0
    elif url.username in {
        "aqs_api",
        "aqs_worker",
        "aqs_bi",
        "aqs_operator",
        "aqs_audit_verifier",
        "aqs_migrator",
        "aqs_owner",
    }:
        return {}
    elif application_name == "aequoros-worker":
        idle_ms, statement_ms = 7_200_000, 1_800_000
    elif application_name == "aequoros-bi":
        idle_ms, statement_ms = 300_000, 10_000
    else:
        # Ingestion storage calls and database-direct introspection still span
        # API transactions. Ten minutes leaves retry/backoff margin until
        # these remaining external waits have explicit commit boundaries.
        idle_ms, statement_ms = 600_000, 300_000
    existing = " ".join(url.normalized_query.get("options", ()))
    return {
        "options": (
            f"{existing} -c idle_in_transaction_session_timeout={idle_ms} "
            f"-c statement_timeout={statement_ms}"
        ).strip()
    }


@lru_cache
def get_engine(database_url: str, application_name: str = "aequoros-api") -> Engine:
    # SQLite has no application_name; keep fallback users on the same cached
    # engine, particularly when its in-memory database is connection-local.
    if not database_url.startswith("postgresql") and application_name != "aequoros-api":
        return get_engine(database_url)
    # The primary database is remote (~40ms away), so connection handling — not
    # query cost — dominates latency. Without TCP keepalives a firewall/NAT silently
    # drops idle pooled connections, forcing a full TLS+auth reconnect (~200ms) on the
    # next request; a small default pool also starves under concurrent requests.
    # Keep each pool's connections warm and recycle before any server-side idle timeout.
    tls_args = database_connect_args(database_url)
    if database_url.startswith("postgresql"):
        return create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,
            connect_args={
                **tls_args,
                **database_timeout_connect_args(database_url, application_name),
                "application_name": application_name,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
    # SQLite (tests): keepalives/pool sizing don't apply.
    return create_engine(database_url, pool_pre_ping=True)


def get_sessionmaker() -> sessionmaker[Session]:
    settings = get_settings()
    if settings.database.database_url is None:
        msg = "DATABASE_URL is required to create database sessions."
        raise RuntimeError(msg)
    return sessionmaker(
        bind=get_engine(settings.database.database_url),
        autoflush=False,
        expire_on_commit=False,
    )


@lru_cache
def get_bi_engine(database_url: str) -> Engine:
    """The BI query pool: a small, separately named sibling of :func:`get_engine`.

    Interactive BI queries are long relative to an API request and are capped
    by ``statement_timeout``; letting them queue behind (or starve) the
    request pool would turn one slow dashboard into API latency. So when
    ``BI_DATABASE_URL`` is set they get their own pool, sized for a handful of
    concurrent dashboards, and announce themselves as ``aequoros-bi`` in
    ``pg_stat_activity`` so an operator can see — and cancel — them by name.
    Keepalives and recycle match the primary pool for the same remote-primary
    reasons documented there.
    """
    tls_args = database_connect_args(database_url)
    if database_url.startswith("postgresql"):
        return create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            pool_timeout=30,
            pool_recycle=1800,
            connect_args={
                **tls_args,
                **database_timeout_connect_args(database_url, "aequoros-bi"),
                "application_name": "aequoros-bi",
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
    return create_engine(database_url, pool_pre_ping=True)


def get_bi_sessionmaker() -> sessionmaker | None:
    """Sessionmaker for BI reads, or ``None`` when the request session is used.

    ``None`` is the hermetic and single-database default: the rollback-
    isolated test fixture knows exactly one URL, and a second engine would
    open a real connection outside its savepoint transaction. Callers set
    ``session.info["organization_id"]`` on any session they make here exactly
    as ``get_tenant_db_session`` does — the ``after_begin`` listener below is
    registered on :class:`Session` globally, so BI sessions get the tenant GUC
    (and therefore RLS) through the same single mechanism.
    """
    url = get_settings().bi.database_url
    if url is None:
        return None
    return sessionmaker(
        bind=get_bi_engine(url),
        autoflush=False,
        expire_on_commit=False,
    )


def get_worker_sessionmaker() -> sessionmaker[Session]:
    """Sessionmaker for background jobs and their narrow platform entry points.

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
        bind=get_engine(url, "aequoros-worker"),
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
    found = connection.execute(_FORCED_RLS_TABLES_SQL, {"table_names": list(table_names)}).scalars()
    return tuple(sorted(found))


@contextmanager
def force_rls_suspended(connection: Connection, *table_names: str) -> Iterator[tuple[str, ...]]:
    """Run a cross-tenant data statement so it can actually see every tenant.

    A data migration rewrites rows for ALL tenants at once, so it cannot set
    ``app.organization_id`` to any single value; on a FORCE-RLS table a
    tenant-scoped role's ``UPDATE`` therefore matches zero rows and reports
    success. Migration credentials and the ``aqs_owner`` handoff follow
    ``docs/ops/database-roles.md#migration-and-local-development-order``.

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
            "match zero rows. Configure MIGRATION_DATABASE_URL with the deployment "
            "migration credential and owner membership described in "
            "docs/ops/database-roles.md#migration-and-local-development-order."
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

    Prefer the dedicated claim function. Legacy deployments may still claim
    directly when their role bypasses RLS or the queue is not forced. A tenant
    role lacking this entry point must fail visibly rather than silently idle.
    """
    try:
        engine = cast(Engine, get_worker_sessionmaker().kw["bind"])
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
            role = cast(str, connection.scalar(text("SELECT current_user")))
            bypasses = role_bypasses_rls(connection)
            forced = forced_rls_tables(connection, _CROSS_TENANT_TABLES)
            narrow_claim = cast(
                bool | None,
                connection.scalar(
                    text(
                        "SELECT has_function_privilege(current_user, "
                        "to_regprocedure('aqs_worker_claim_job("
                        "timestamp with time zone,text[],text)'), "
                        "'EXECUTE')"
                    )
                ),
            )
    except SQLAlchemyError as exc:
        return WorkerVisibility(
            can_claim=False,
            role=None,
            detail=f"Worker database is unreachable: {exc.__class__.__name__}.",
        )

    if narrow_claim or bypasses:
        detail = (
            f"{role} can execute the worker claim function."
            if narrow_claim
            else f"{role} bypasses row-level security."
        )
        return WorkerVisibility(can_claim=True, role=role, detail=detail)
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
            "WORKER_DATABASE_URL at aqs_worker after applying the worker access migration."
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
    _transaction: SessionTransaction,
    connection: Connection,
) -> None:
    organization_id = cast(str | None, session.info.get("organization_id"))
    if connection.dialect.name != "postgresql":
        return

    connection.execute(
        text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": "" if organization_id is None else str(organization_id)},
    )

    bank_id = cast(str | None, session.info.get("bank_id"))
    connection.execute(
        text("SELECT set_config('app.bank_id', :bank_id, true)"),
        {"bank_id": "" if bank_id is None else str(bank_id)},
    )


def get_db_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
