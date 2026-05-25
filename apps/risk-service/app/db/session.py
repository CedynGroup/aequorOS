from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

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
