from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from inspect import unwrap
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.key_management import registry
from app.data_engine.api.ingest_data import get_ingestion_storage
from app.db.session import get_sessionmaker
from app.integrations.storage.s3 import get_object_storage
from app.models import Bank, Organization, RiskCase
from tests import conftest as root_fixtures
from tests.conftest import (
    FakeStorage,
    _db_client_lifecycle,
    _LazyTestApp,
    _reset_test_database,
    _rollback_sessionmaker_lifecycle,
    _TestDatabase,
)
from tests.support.api_factories import CaseFactory
from tests.support.database import DatabaseContext, TestDatabaseConnections
from tests.support.helpers import ORG_1
from tests.support.inmemory_storage import InMemoryStorageClient
from tests.support.key_concurrency import assert_blocked, backend_pid

_ROLLBACK_ORGANIZATION_ID = "OR-TXROLL01"
_COMMITTED_ORGANIZATION_ID = "OR-TXCOMM01"

# This contract test must expose the real engine and independent connections.
requires_committing_db = pytest.mark.committing_db


def test_ordinary_application_baseline_is_bank_free(db_session: Session) -> None:
    assert not db_session.scalars(select(Bank)).all()


def test_committed_write_is_rolled_back_between_fixture_lifecycles(
    _shared_test_database: _TestDatabase,
) -> None:
    with (
        pytest.MonkeyPatch.context() as first_patch,
        _rollback_sessionmaker_lifecycle(_shared_test_database, first_patch) as maker,
        maker() as session,
    ):
        session.add(
            Organization(
                id=_ROLLBACK_ORGANIZATION_ID,
                name="Rollback Isolation Contract",
            )
        )
        session.commit()
        assert session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is not None

    with (
        pytest.MonkeyPatch.context() as second_patch,
        _rollback_sessionmaker_lifecycle(_shared_test_database, second_patch) as maker,
        maker() as session,
    ):
        assert session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is None


def test_api_write_is_visible_to_direct_bound_session(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    with get_sessionmaker()() as session:
        assert session.get(RiskCase, case.id) is not None


def test_reused_app_receives_fresh_client_and_storage_state(
    _shared_app: _LazyTestApp,
    _shared_test_database: _TestDatabase,
) -> None:
    app = _shared_app.get()
    first_storage = FakeStorage()
    first_ingestion = InMemoryStorageClient()
    with (
        pytest.MonkeyPatch.context() as first_patch,
        _rollback_sessionmaker_lifecycle(_shared_test_database, first_patch),
        _db_client_lifecycle(app, first_storage, first_ingestion) as first_client,
    ):
        first_client.cookies.set("fixture-state", "must-not-leak")
        first_client.headers["x-fixture-state"] = "must-not-leak"

    second_storage = FakeStorage()
    second_ingestion = InMemoryStorageClient()
    with (
        pytest.MonkeyPatch.context() as second_patch,
        _rollback_sessionmaker_lifecycle(_shared_test_database, second_patch),
        _db_client_lifecycle(app, second_storage, second_ingestion) as second_client,
    ):
        assert second_client.cookies.get("fixture-state") is None
        assert "x-fixture-state" not in second_client.headers
        assert app.dependency_overrides[get_object_storage]() is second_storage
        assert app.dependency_overrides[get_ingestion_storage]() is second_ingestion


@requires_committing_db
def test_committing_schema_reset_removes_committed_rows_and_restores_the_seed(
    _shared_committing_database: _TestDatabase,
) -> None:
    """Every committing test starts from the freshly built schema, not from the
    rows its predecessors really committed."""
    with Session(_shared_committing_database.engine) as session:
        session.add(Organization(id=_COMMITTED_ORGANIZATION_ID, name="Committed Contract"))
        session.commit()

    _reset_test_database(_shared_committing_database)

    with Session(_shared_committing_database.engine) as session:
        assert session.get(Organization, _COMMITTED_ORGANIZATION_ID) is None
        assert session.get(Organization, ORG_1) is not None


@requires_committing_db
def test_isolated_session_exposes_a_real_engine(
    db_session: Session,
) -> None:
    assert isinstance(db_session.get_bind(), Engine)


def test_real_data_fixture_keeps_commits_inside_outer_rollback_on_synthetic_database(
    _shared_test_database: _TestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(root_fixtures, "REAL_DATA_DATABASE_URL", _shared_test_database.database_url)
    fixture = cast(
        Callable[[pytest.MonkeyPatch], Generator[sessionmaker[Session]]],
        unwrap(root_fixtures._real_bound_sessionmaker),  # pyright: ignore[reportPrivateUsage]
    )
    lifecycle = fixture(monkeypatch)
    maker = next(lifecycle)
    try:
        with maker() as session:
            session.add(Organization(id="OR-RAWROLL1", name="Synthetic rollback fixture"))
            session.commit()
            assert (
                session.scalar(text("SELECT count(*) FROM organizations WHERE id='OR-RAWROLL1'"))
                == 1
            )
    finally:
        lifecycle.close()
    with _shared_test_database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM organizations WHERE id='OR-RAWROLL1'"))
            == 0
        )


def test_bank_key_locks_are_isolated_between_test_schemas(
    _shared_test_database: _TestDatabase,
    _shared_committing_database: _TestDatabase,
) -> None:
    engine = _shared_test_database.engine
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory locks are required.")
    waiter: Future[int] = Future()
    with engine.connect() as connection:
        schema = cast(object, connection.scalar(text("SELECT current_schema()")))
    assert isinstance(schema, str)
    connections = TestDatabaseConnections(
        engine.url.difference_update_query(["options"]), context=DatabaseContext(schema=schema)
    )
    replacement = connections.engine()

    def same_schema_writer() -> None:
        with Session(replacement) as writer, writer.begin():
            _ = writer.execute(text("SET LOCAL lock_timeout='1s'"))
            waiter.set_result(backend_pid(writer))
            registry.lock_bank_version(writer, ORG_1, "BK-SAMP0001")

    try:
        with Session(engine) as reader:
            registry.lock_bank_version(reader, ORG_1, "BK-SAMP0001", read=True)
            blocker = backend_pid(reader)
            with Session(_shared_committing_database.engine) as other, other.begin():
                _ = other.execute(text("SET LOCAL lock_timeout='1s'"))
                registry.lock_bank_version(other, ORG_1, "BK-SAMP0001")
            with ThreadPoolExecutor(max_workers=1) as pool:
                writing = pool.submit(same_schema_writer)
                try:
                    assert_blocked(engine, blocker, waiter.result(timeout=5), writing)
                finally:
                    reader.rollback()
                writing.result(timeout=5)
    finally:
        connections.close()
