"""Live-data invariant suite: read-only checks against the ACTUAL database.

The hermetic suite proves the LOGIC is right on disposable fixtures; this suite
proves the REAL data is right — every row ingestion-traced, every period
covered, the live engine current. It is opt-in and physically cannot write:

    LIVE_DATA_DATABASE_URL=<postgresql+psycopg URL> uv run pytest tests/live_data -q

Use the BYPASSRLS worker URL (WORKER_DATABASE_URL in backend/.env) so FORCE-RLS
tables are visible; with a tenant-scoped app role, set LIVE_DATA_ORG_ID to the
organization UUID and the session pins the RLS GUC instead. A dedicated env var
(never DATABASE_URL) keeps the hermetic suite's leak guard intact.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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
    engine = create_engine(_URL, pool_pre_ping=True)
    session = Session(engine)
    session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    org_id = os.environ.get("LIVE_DATA_ORG_ID", "").strip()
    if org_id:
        session.execute(
            text("SELECT set_config('app.organization_id', :org, false)"), {"org": org_id}
        )
    # COMMIT the settings transaction: session characteristics take effect from
    # the NEXT transaction, and a session-level set_config would be undone by a
    # rollback. Everything after this point runs read-only.
    session.commit()
    visible = session.execute(text("SELECT count(*) FROM organizations")).scalar()
    if not visible:
        pytest.fail(
            "The connected role sees no organizations — use the BYPASSRLS worker URL "
            "or set LIVE_DATA_ORG_ID so FORCE-RLS tables are visible."
        )
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
