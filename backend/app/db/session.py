from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


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

    Bound to ``WORKER_DATABASE_URL`` when set (a BYPASSRLS role on an
    RLS-forced Postgres), else falling back to ``DATABASE_URL``. Handlers still
    filter every query by organization_id/bank_id explicitly, so bypassing RLS
    here is defense-reduced but not correctness-reducing.
    """
    settings = get_settings()
    url = settings.worker.worker_database_url or settings.database.database_url
    if url is None:
        msg = "DATABASE_URL (or WORKER_DATABASE_URL) is required for the worker."
        raise RuntimeError(msg)
    return sessionmaker(
        bind=get_engine(url),
        autoflush=False,
        expire_on_commit=False,
    )


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
