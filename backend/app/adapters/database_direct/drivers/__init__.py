"""Backend-driver registry: one live driver per supported backend.

``driver_for`` resolves a :class:`~app.adapters.database_direct.config.Backend`
to its live :class:`~app.adapters.database_direct.drivers.base.DatabaseDriver`.
The offline fixture driver (used by the whole contract test suite) lives in
``app.adapters.database_direct.fixtures`` and implements the same abstraction, so
it can stand in for any of these without a live database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.database_direct.drivers.base import (
    ColumnSchema,
    DatabaseDriver,
    DbCredentials,
    DriverCapabilities,
    DriverSession,
    QueryResult,
    TableSchema,
)
from app.adapters.database_direct.drivers.jdbc import JdbcDriver
from app.adapters.database_direct.drivers.odbc import OdbcDriver
from app.adapters.database_direct.drivers.oracle import OracleDriver
from app.adapters.database_direct.drivers.sqlserver import SqlServerDriver

if TYPE_CHECKING:
    from app.adapters.database_direct.config import Backend

__all__ = [
    "ColumnSchema",
    "DatabaseDriver",
    "DbCredentials",
    "DriverCapabilities",
    "DriverSession",
    "JdbcDriver",
    "OdbcDriver",
    "OracleDriver",
    "QueryResult",
    "SqlServerDriver",
    "TableSchema",
    "driver_for",
]

_DRIVERS: dict[str, type[DatabaseDriver]] = {
    "oracle": OracleDriver,
    "sqlserver": SqlServerDriver,
    "jdbc": JdbcDriver,
    "odbc": OdbcDriver,
}


def driver_for(backend: Backend) -> DatabaseDriver:
    """Instantiate the live driver for a backend."""
    try:
        cls = _DRIVERS[backend]
    except KeyError:
        known = ", ".join(sorted(_DRIVERS))
        msg = f"No database-direct driver for backend {backend!r}. Known backends: {known}."
        raise ValueError(msg) from None
    return cls()
