"""Pytest entry point for independently owned test databases."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.database import TestDatabaseConnections, isolated_database


@pytest.fixture
def isolated_db(tmp_path: Path) -> Iterator[TestDatabaseConnections]:
    """An empty per-test schema/file, without financial data or global seeds.

    Use existing ``db_session``/``db_client`` for the shared application baseline.
    Migration and concurrency tests can build their own schema here and open
    as many independently pooled connections as they need.
    """
    database_url = os.environ.get("TEST_DATABASE_URL") or (
        f"sqlite+pysqlite:///{tmp_path / 'test-connections.db'}"
    )
    with isolated_database(database_url) as database:
        yield database
