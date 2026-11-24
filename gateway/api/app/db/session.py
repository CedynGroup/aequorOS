"""Engine and session construction."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def make_engine(url: str | URL, *, schema: str | None = None) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database or ""
        in_memory = database in ("", ":memory:") or "mode=memory" in str(parsed)
        engine = create_engine(
            parsed,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if in_memory else None,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:  # pyright: ignore[reportUnusedFunction]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    connect_args: dict[str, str] = {}
    if schema:
        connect_args["options"] = f"-csearch_path={schema}"
    return create_engine(parsed, pool_pre_ping=True, connect_args=connect_args)


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine: Engine = engine
        self.sessions: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)

    @property
    def postgres(self) -> bool:
        return is_postgres(self.engine)

    @contextmanager
    def session(self) -> Generator[Session]:
        session = self.sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
