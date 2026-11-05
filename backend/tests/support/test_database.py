"""Behavioral contract for managed synthetic test connections.

This module's other-suite cases live in ``test_database_integration.py``.
"""

from __future__ import annotations

import pytest

from tests.support.database import (
    DatabaseContext,
    TestDatabaseConnections,
)


def test_context_validation_fails_before_connecting() -> None:
    with pytest.raises(ValueError, match="explicit disposable schema"):
        _ = TestDatabaseConnections("postgresql+psycopg://unused/test")
    with pytest.raises(ValueError, match="Invalid test database identifier"):
        _ = TestDatabaseConnections(
            "postgresql+psycopg://unused/test", context=DatabaseContext(schema="unsafe; SQL")
        )
    with TestDatabaseConnections("sqlite://") as db:
        with pytest.raises(ValueError, match="owning test schema"):
            _ = db.with_context(DatabaseContext(schema="another_test"))
        with pytest.raises(ValueError, match="psycopg driver"), db.raw_connection():
            pass
        with (
            TestDatabaseConnections("sqlite://") as other,
            pytest.raises(ValueError, match="must belong"),
        ):
            _ = db.session_factory(bind=other.engine())
