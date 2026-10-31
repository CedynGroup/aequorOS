"""Opt-in canonical book reuse with fresh, managed per-test transactions."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    CanonicalTestBookSummary,
    materialize_canonical_test_book,
)
from tests.support.database import TestDatabaseConnections, isolated_database

_STATE_KEY = "canonical_book_reuse"
type BookFingerprint = tuple[tuple[str, int, str], ...]


@dataclass(frozen=True)
class CanonicalBookDatabase:
    """Scalar book identity and an owned database; never a shared ORM graph."""

    connections: TestDatabaseConnections
    summary: CanonicalTestBookSummary
    fingerprint: BookFingerprint


@dataclass
class _BookRequest:
    summary: CanonicalTestBookSummary
    reused: bool = False


def canonical_test_book(session: Session) -> CanonicalTestBookSummary:
    """Reuse the first explicitly opted-in request; keep later rebuilds real."""
    state = session.info.get(_STATE_KEY)
    if not isinstance(state, _BookRequest) or state.reused:
        return materialize_canonical_test_book(session)
    state.reused = True
    if session.get_bind().dialect.name == "postgresql":
        _ = session.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": DEMO_ORG_ID},
        )
    return state.summary


def book_fingerprint(connections: TestDatabaseConnections) -> BookFingerprint:
    """Compare every stored value, including IDs/timestamps, after rollback."""
    tables = sorted(Base.metadata.tables)
    with connections.connection() as connection:
        if connection.dialect.name == "postgresql":
            query = " UNION ALL ".join(
                f"SELECT '{name}', count(*), "
                "coalesce(md5(string_agg(v, ',' ORDER BY v)), '') "
                f'FROM (SELECT row_to_json(t)::text AS v FROM "{name}" AS t) AS rows'
                for name in tables
            )
            rows = cast(Sequence[tuple[str, int, str]], connection.execute(text(query)).all())
            return tuple(sorted(rows))
        result: list[tuple[str, int, str]] = []
        for name in tables:
            rows = cast(
                Sequence[Sequence[object]],
                connection.exec_driver_sql(f'SELECT * FROM "{name}"').all(),
            )
            values = sorted(repr(tuple(row)) for row in rows)
            digest = hashlib.sha256("\n".join(values).encode()).hexdigest()
            result.append((name, len(rows), digest))
        return tuple(result)


@pytest.fixture(scope="session")
def canonical_book_database(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[CanonicalBookDatabase]:
    from tests.conftest import seed_demo_tenants  # noqa: PLC0415

    database_url = os.environ.get("TEST_DATABASE_URL") or (
        f"sqlite+pysqlite:///{tmp_path_factory.mktemp('canonical-book') / 'book.db'}"
    )
    with isolated_database(database_url) as connections:
        engine = connections.engine()
        Base.metadata.create_all(engine)
        seed_demo_tenants(engine)
        with connections.session(bind=engine) as session:
            summary = materialize_canonical_test_book(session)
            session.commit()
        baseline = CanonicalBookDatabase(connections, summary, book_fingerprint(connections))
        try:
            yield baseline
        finally:
            assert book_fingerprint(connections) == baseline.fingerprint


@contextmanager
def canonical_book_transaction(
    baseline: CanonicalBookDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[sessionmaker[Session]]:
    """Bind application sessions to one outer transaction, then prove rollback."""
    import app.api.deps as deps_mod  # noqa: PLC0415
    import app.db.session as session_mod  # noqa: PLC0415
    import app.identity.api.auth as auth_mod  # noqa: PLC0415

    try:
        with baseline.connections.with_context(baseline.connections.context) as connections:
            monkeypatch.setenv("DATABASE_URL", connections.database_url)
            get_settings.cache_clear()
            get_engine.cache_clear()
            with connections.connection() as connection:
                outer = connection.begin()
                if connection.dialect.name == "sqlite":
                    _ = connection.exec_driver_sql("BEGIN")
                maker = connections.session_factory(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                    info={_STATE_KEY: _BookRequest(baseline.summary)},
                )
                engine = connection.engine
                original_get_engine = session_mod.get_engine

                def application_engine(
                    database_url: str, application_name: str = "aequoros-api"
                ) -> Engine:
                    if database_url == connections.database_url:
                        return engine
                    return original_get_engine(database_url, application_name)

                def application_sessionmaker(
                    *, bind: Engine | Connection, **_options: object
                ) -> sessionmaker[Session]:
                    assert bind is engine or bind is connection, (
                        "Session escaped the test transaction"
                    )
                    return maker

                monkeypatch.setattr(deps_mod, "get_sessionmaker", lambda: maker)
                monkeypatch.setattr(auth_mod, "get_sessionmaker", lambda: maker)
                monkeypatch.setattr(session_mod, "get_engine", application_engine)
                monkeypatch.setattr(session_mod, "sessionmaker", application_sessionmaker)
                try:
                    yield maker
                finally:
                    active = outer.is_active
                    if active:
                        outer.rollback()
                    get_settings.cache_clear()
                    get_engine.cache_clear()
                    assert active, "Canonical-book tests must not commit their outer transaction"
    finally:
        assert book_fingerprint(baseline.connections) == baseline.fingerprint


@pytest.fixture
def canonical_book_sessionmaker(
    canonical_book_database: CanonicalBookDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sessionmaker[Session]]:
    with canonical_book_transaction(canonical_book_database, monkeypatch) as maker:
        yield maker
