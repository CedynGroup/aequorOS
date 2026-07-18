"""Native SQL Server driver over ``pyodbc`` (lazy-imported).

Builds a read-only, TLS-enforced ODBC connection to a bank's SQL Server core and
runs the parameterized ``SELECT`` statements the query builder produces. Read
intent is declared to the server (``ApplicationIntent=ReadOnly``) so an
Availability-Group listener routes AequorOS to a readable secondary, reinforcing
the replica-preference contract; the connection is never committed and only
``SELECT`` is ever issued.

Schema introspection uses ANSI ``INFORMATION_SCHEMA`` views, which SQL Server
implements — no proprietary catalog syntax is invented here. Incremental
extraction uses a configured timestamp/``rowversion`` cursor column; native SQL
Server CDC (``cdc.fn_cdc_get_all_changes_*``) is a documented follow-on whose
capture-instance names are installation-specific, so it is intentionally NOT
hardcoded (see :meth:`capabilities`).

``pyodbc`` is an optional native dependency: it is imported inside
:meth:`connect`, and its absence is surfaced as a classified
``DRIVER_UNAVAILABLE`` rather than breaking application import.
"""

from __future__ import annotations

import contextlib
from types import TracebackType
from typing import TYPE_CHECKING, Any

from app.adapters.database_direct.drivers.base import (
    ColumnSchema,
    DatabaseDriver,
    DbCredentials,
    DriverCapabilities,
    QueryResult,
    TableSchema,
)
from app.adapters.database_direct.errors import (
    DatabaseDirectError,
    DbDirectErrorCode,
    classify_dbapi_error,
    render_bank_facing,
)

if TYPE_CHECKING:
    from app.adapters.database_direct.config import Backend, ConnectionConfig
    from app.adapters.database_direct.query_builder import BuiltQuery

# ANSI INFORMATION_SCHEMA introspection (portable across SQL Server versions).
_INTROSPECT_SQL = (
    "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE "
    "FROM INFORMATION_SCHEMA.COLUMNS c "
    "JOIN INFORMATION_SCHEMA.TABLES t "
    "  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
    "WHERE t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')"
)


def _import_pyodbc(database_label: str) -> Any:
    try:
        import pyodbc  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"pyodbc not installed on the AequorOS worker: {exc}",
        ) from exc
    return pyodbc


class SqlServerDriver(DatabaseDriver):
    """Opens read-only ODBC sessions to a SQL Server core."""

    def __init__(self, *, odbc_driver_name: str = "ODBC Driver 18 for SQL Server") -> None:
        # The registered ODBC driver name is environment-specific; the default
        # is Microsoft's current cross-platform driver. Onboarding may override.
        self._odbc_driver_name = odbc_driver_name

    def backend(self) -> Backend:
        return "sqlserver"

    def capabilities(self) -> DriverCapabilities:
        # Native CDC exists but its capture-instance names are per-install, so we
        # advertise timestamp-cursor incremental and leave CDC to explicit config.
        return DriverCapabilities(
            supports_change_data_capture=False,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> _PyodbcSession:
        pyodbc = _import_pyodbc(connection.display_label)
        endpoints = connection.endpoints_in_preference_order() or ("",)
        last_error: BaseException | None = None
        for index, endpoint in enumerate(endpoints):
            try:
                conn = pyodbc.connect(
                    self._connection_string(connection, credentials, endpoint),
                    timeout=connection.query_timeout_seconds,
                    autocommit=True,  # read-only: never opens a writable txn
                    readonly=True,
                )
                return _PyodbcSession(
                    conn,
                    timeout_seconds=connection.query_timeout_seconds,
                    database_label=connection.display_label,
                )
            except pyodbc.Error as exc:  # noqa: PERF203 - endpoint failover is intentional
                last_error = exc
                # A failed non-last endpoint is a replica we skip past; keep going.
                if index < len(endpoints) - 1:
                    continue
        raise self._connect_failure(connection, last_error)

    def _connect_failure(
        self, connection: ConnectionConfig, exc: BaseException | None
    ) -> DatabaseDirectError:
        if exc is None:
            return DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="no SQL Server endpoint configured to connect to",
            )
        return classify_dbapi_error(exc, database=connection.display_label)

    def _connection_string(
        self, connection: ConnectionConfig, credentials: DbCredentials, endpoint: str
    ) -> str:
        server = endpoint.replace(":", ",") if endpoint else connection.host  # SQL Server host,port
        parts = [
            f"DRIVER={{{self._odbc_driver_name}}}",
            f"SERVER={server}",
            f"DATABASE={connection.database}",
            f"UID={credentials.username}",
            f"PWD={credentials.password}",
            "ApplicationIntent=ReadOnly",
        ]
        tls = connection.tls
        if tls.enabled:
            parts.append("Encrypt=yes")
            parts.append(
                "TrustServerCertificate=no"
                if tls.verify_server_certificate
                else "TrustServerCertificate=yes"
            )
            if tls.ca_cert_path:
                # Driver 18 honors a CA bundle via the "SSLCA"/"CAFile" keyword
                # depending on platform build; onboarding sets the correct one.
                parts.append(f"CAFile={tls.ca_cert_path}")
        else:
            parts.append("Encrypt=no")
        return ";".join(parts) + ";"


class _PyodbcSession:
    """A live, read-only pyodbc session. Closes the connection on exit."""

    def __init__(self, conn: Any, *, timeout_seconds: int, database_label: str) -> None:
        self._conn = conn
        self._timeout = timeout_seconds
        self._database_label = database_label

    def __enter__(self) -> _PyodbcSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        # A close failure must never mask the real error being handled.
        with contextlib.suppress(Exception):
            self._conn.close()

    def introspect(self, schemas: tuple[str, ...]) -> list[TableSchema]:
        sql = _INTROSPECT_SQL
        params: list[Any] = []
        if schemas:
            placeholders = ", ".join("?" for _ in schemas)
            sql += f" AND c.TABLE_SCHEMA IN ({placeholders})"
            params.extend(schemas)
        sql += " ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION"
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params) if params else cursor.execute(sql)
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - classified below, never surfaced raw
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return _assemble_tables(rows)

    def fetch(self, query: BuiltQuery) -> QueryResult:
        try:
            cursor = self._conn.cursor()
            if query.parameters:
                cursor.execute(query.sql, list(query.parameters))
            else:
                cursor.execute(query.sql)
            columns = tuple(column[0] for column in cursor.description or ())
            rows = tuple(tuple(row) for row in cursor.fetchall())
        except Exception as exc:  # noqa: BLE001 - classified, raw text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return QueryResult(columns=columns, rows=rows)


def _assemble_tables(rows: list[Any]) -> list[TableSchema]:
    """Group flat ``(schema, table, column, type, nullable)`` rows into tables."""
    grouped: dict[tuple[str, str], list[ColumnSchema]] = {}
    order: list[tuple[str, str]] = []
    for schema, table, column, data_type, is_nullable in rows:
        key = (str(schema), str(table))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            ColumnSchema(
                name=str(column),
                data_type=str(data_type),
                nullable=str(is_nullable).upper() != "NO",
            )
        )
    return [
        TableSchema(name=table, schema=schema, columns=tuple(grouped[(schema, table)]))
        for schema, table in order
    ]
