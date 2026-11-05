"""The execution wrapper on SQLite: caps, truncation, paging, session choice.

The Postgres-only guards (``statement_timeout``, read-only transaction, the
savepoint that resets them) are proven in ``tests/db/test_bi_query_execution_integration.py``
against a real server (D-011); here the SQLite path is shown to skip them
rather than pretend, and the error mapping is exercised on a synthetic
driver error.

This module's other-suite cases live in ``test_execution_integration.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.bi.service import execution
from app.bi.service.errors import BiQueryError, BiQueryTimeout


class _Cancelled(Exception):
    sqlstate = "57014"


class _Other(Exception):
    sqlstate = "42P01"


class _Legacy(Exception):
    pgcode = "57014"


@pytest.mark.parametrize(
    ("origin", "cancelled"),
    [(_Cancelled(), True), (_Other(), False), (_Legacy(), True), (Exception("plain"), False)],
)
def test_only_query_canceled_maps_to_the_timeout(origin: Exception, cancelled: bool) -> None:
    error = DBAPIError("SELECT 1", {}, origin)
    assert execution._is_cancelled(error) is cancelled


def test_the_timeout_error_is_504_shaped_and_names_no_sql() -> None:
    error = BiQueryTimeout(10_000)
    assert isinstance(error, BiQueryError)
    assert error.status_code == 504
    assert error.code == "bi_query_timeout"
    assert "10000" in str(error)
    assert "SELECT" not in str(error)
    assert error.timeout_ms == 10_000
