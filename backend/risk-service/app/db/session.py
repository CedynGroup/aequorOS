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
