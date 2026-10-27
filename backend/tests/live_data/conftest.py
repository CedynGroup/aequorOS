"""Live-data invariant suite: read-only checks against the ACTUAL database.

The hermetic suite proves the LOGIC is right on disposable fixtures; this suite
proves the REAL data is right — every row ingestion-traced, every period
covered, the live engine current. It is opt-in and physically cannot write:

    LIVE_DATA_DATABASE_URL=<postgresql+psycopg URL> uv run pytest tests/live_data -q

Credential selection and tenant visibility follow
``backend/README.md#test-databases-and-the-primary-database``.
A dedicated env var (never DATABASE_URL) keeps the hermetic suite's leak guard intact.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.support.database import database_connections, managed_session_factory

_URL = os.environ.get("LIVE_DATA_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason="live-data suite is opt-in: set LIVE_DATA_DATABASE_URL to the real database",
)


@pytest.fixture(scope="session")
def live_db() -> Iterator[Session]:
    """One READ-ONLY session for the whole suite. Postgres enforces the
    read-only characteristic server-side — any write raises, so this suite can
    never mutate the data it certifies."""
    if not _URL:  # pragma: no cover - guarded by the module skip
        pytest.skip("LIVE_DATA_DATABASE_URL not set")
    org_id = os.environ.get("LIVE_DATA_ORG_ID", "").strip()
    with database_connections(_URL, default_schema="public") as owner:
        engine = owner.engine(pool_pre_ping=True)
        with managed_session_factory(
            None, engine, info={"organization_id": org_id} if org_id else {}
        )() as session:
            session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
            # COMMIT the settings transaction: session characteristics take effect from
            # the NEXT transaction. Everything after this point runs read-only.
            session.commit()
            visible = session.execute(text("SELECT count(*) FROM organizations")).scalar()
            if not visible:
                pytest.fail(
                    "The connected role sees no organizations. Configure LIVE_DATA_DATABASE_URL "
                    "and LIVE_DATA_ORG_ID following "
                    "backend/README.md#test-databases-and-the-primary-database."
                )
            yield session


@pytest.fixture(autouse=True)
def _end_each_read_transaction(live_db: Session) -> Iterator[None]:
    """One session serves the whole suite, so one failed statement would abort
    its transaction and turn every later test into ``InFailedSqlTransaction`` —
    a cascade that reads as thirty findings and is one. Each test ends its own
    read transaction; there is never anything to commit."""
    yield
    live_db.rollback()
