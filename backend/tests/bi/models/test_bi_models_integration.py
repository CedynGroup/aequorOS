"""Hermetic shape checks for the BI models (``app/bi/models/marts.py``).

What the ORM declares is what ``Base.metadata.create_all`` builds for the
hermetic suite and the Playwright stack, and what the RLS static guard and the
current-generation guard read. These tests pin that declaration against the
binding column contract (``.ai/bi_contracts.md``) — keys, unique indexes, the
CHECK vocabularies, the tenant columns, ``sa.JSON`` (never JSONB) — and the
D-011 rule that the models are PLAIN tables: no ``postgresql_partition_by``, so
``create_all`` works on SQLite and on Postgres alike, and partitioning stays
the migration's business.

The BI session factory lives here too: with no ``BI_DATABASE_URL`` it yields
``None`` (the request session is used), and any session made from the BI pool
is an ordinary :class:`Session`, so the global ``after_begin`` tenant-GUC
listener applies to it unchanged.

This module's other-suite cases live in ``test_bi_models.py``.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app.bi.models.marts import (
    BI_TABLES,
)
from app.db.base import Base
from app.db.session import get_bi_engine, set_tenant_rls_context
from tests.bi.models.test_bi_models import MODELS, _table
from tests.support.database import (
    adopt_application_engine,
    managed_engine,
    managed_session_factory,
)


def test_create_all_on_sqlite_builds_every_bi_table_as_a_plain_table(
    request: pytest.FixtureRequest,
) -> None:
    engine = managed_engine(request, "sqlite://")
    Base.metadata.create_all(engine)
    present = set(inspect(engine).get_table_names())
    assert set(BI_TABLES) <= present
    # D-011: partitioning is migration-owned. A ``postgresql_partition_by`` here
    # would make a Postgres ``create_all`` produce a childless parent that
    # rejects every INSERT on the TEST_DATABASE_URL conftest path.
    for model in MODELS:
        assert _table(model).dialect_options["postgresql"]["partition_by"] is None, _table(
            model
        ).name


def test_bi_engine_is_a_small_named_pool_on_postgres(request: pytest.FixtureRequest) -> None:
    get_bi_engine.cache_clear()
    engine = adopt_application_engine(
        request,
        get_bi_engine("postgresql+psycopg://bi:bi@localhost:1/never_connected"),
        administrative=True,
    )
    seen: dict[str, object] = {}

    class _Abort(Exception):
        pass

    # ``do_connect`` sees the merged connect arguments just before the DBAPI
    # call; raising there inspects them without ever reaching a server.
    @event.listens_for(engine, "do_connect")
    def _capture(_dialect, _record, _cargs, cparams) -> None:
        seen.update(cparams)
        raise _Abort

    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(_Abort):
            engine.connect()
        assert seen["application_name"] == "aequoros-bi"
        assert seen["keepalives"] == 1
        assert get_bi_engine("postgresql+psycopg://bi:bi@localhost:1/never_connected") is engine
    finally:
        engine.dispose()
        get_bi_engine.cache_clear()


def test_bi_sessions_get_the_tenant_guc_listener_for_free(
    tmp_path, request: pytest.FixtureRequest
) -> None:
    """The ``after_begin`` listener is registered on :class:`Session` itself,
    so a session from the BI pool is scoped exactly like a request session."""
    assert event.contains(Session, "after_begin", set_tenant_rls_context)
    engine = adopt_application_engine(
        request, get_bi_engine(f"sqlite+pysqlite:///{tmp_path / 'bi.db'}")
    )
    try:
        with managed_session_factory(request, engine)() as session:
            assert isinstance(session, Session)
            session.info["organization_id"] = "OR-DEM00001"
            session.execute(sa.text("SELECT 1"))  # begins a transaction; the hook runs
    finally:
        engine.dispose()
        get_bi_engine.cache_clear()
