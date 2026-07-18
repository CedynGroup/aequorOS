"""Generic ODBC driver over ``pyodbc`` (lazy-imported).

For a bank reachable through any registered system ODBC driver or DSN — a
long-tail of cores where no native Python driver exists — this backend connects
via ``pyodbc`` and runs the same parameterized ``SELECT`` statements. Unlike the
SQL Server backend it makes no vendor assumptions: introspection uses the
ODBC-level catalog function ``SQLColumns`` (exposed as ``cursor.columns``), which
every ODBC driver implements, so it works whatever database sits behind the DSN.

Read-only intent is enforced by executing only ``SELECT`` and never committing.
TLS is enforced fail-closed: with TLS required, the resolved connection keywords
must declare an encryption option, otherwise the connection is refused rather
than risking a cleartext pipe into a bank's core.

``pyodbc`` is imported inside :meth:`connect`; its absence is surfaced as a
classified ``DRIVER_UNAVAILABLE`` rather than breaking application import.
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

_TLS_HINTS = ("encrypt=yes", "ssl=1", "sslmode=require", "encryption=required", "usessl=1")


def _import_pyodbc(database_label: str) -> Any:
    try:
        import pyodbc  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"pyodbc not installed on the AequorOS worker: {exc}",
        ) from exc
    return pyodbc


class OdbcDriver(DatabaseDriver):
    """Opens read-only sessions to any ODBC-reachable core."""

    def backend(self) -> Backend:
        return "odbc"

    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            supports_change_data_capture=False,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> _OdbcSession:
        odbc_cfg = connection.odbc
        if odbc_cfg is None:
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="odbc backend selected without an odbc config block",
            )
        pyodbc = _import_pyodbc(connection.display_label)
        endpoints = connection.endpoints_in_preference_order() or ("",)
        last_error: BaseException | None = None
        for index, endpoint in enumerate(endpoints):
            conn_str = self._connection_string(connection, credentials, endpoint)
            self._enforce_tls(connection, conn_str)
            try:
                conn = pyodbc.connect(
                    conn_str,
                    timeout=connection.query_timeout_seconds,
                    autocommit=True,
                    readonly=True,
                )
                return _OdbcSession(conn, database_label=connection.display_label)
            except DatabaseDirectError:
                raise
            except pyodbc.Error as exc:  # noqa: PERF203 - endpoint failover is intentional
                last_error = exc
                if index < len(endpoints) - 1:
                    continue
        raise (
            classify_dbapi_error(last_error, database=connection.display_label)
            if last_error is not None
            else DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="no ODBC endpoint resolved",
            )
        )

    def _connection_string(
        self, connection: ConnectionConfig, credentials: DbCredentials, endpoint: str
    ) -> str:
        odbc_cfg = connection.odbc
        assert odbc_cfg is not None  # guarded in connect()
        parts: list[str] = []
        if odbc_cfg.dsn:
            parts.append(f"DSN={odbc_cfg.dsn}")
        else:
            parts.append(f"DRIVER={{{odbc_cfg.driver_name}}}")
            server = endpoint or connection.host
            if server:
                parts.append(f"SERVER={server}")
            if connection.port and not endpoint:
                parts.append(f"PORT={connection.port}")
            if connection.database:
                parts.append(f"DATABASE={connection.database}")
        parts.append(f"UID={credentials.username}")
        parts.append(f"PWD={credentials.password}")
        for key, value in odbc_cfg.extra_keywords.items():
            parts.append(f"{key}={value}")
        return ";".join(parts) + ";"

    def _enforce_tls(self, connection: ConnectionConfig, conn_str: str) -> None:
        if not connection.tls.enabled:
            return
        if not any(hint in conn_str.lower() for hint in _TLS_HINTS):
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.TLS_REQUIRED, database=connection.display_label
                ),
                internal_detail=(
                    "TLS required but ODBC keywords declare no encryption option; "
                    "refusing to open a possibly-cleartext ODBC connection"
                ),
            )


class _OdbcSession:
    """A live, read-only pyodbc session over a generic ODBC driver."""

    def __init__(self, conn: Any, *, database_label: str) -> None:
        self._conn = conn
        self._database_label = database_label

    def __enter__(self) -> _OdbcSession:
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
        """Introspect via the ODBC ``SQLColumns`` catalog function (portable)."""
        tables: dict[tuple[str, str], list[ColumnSchema]] = {}
        order: list[tuple[str, str]] = []
        try:
            for schema in schemas or (None,):
                cursor = self._conn.cursor()
                for row in cursor.columns(schema=schema):
                    schema_name = str(getattr(row, "table_schem", "") or "")
                    table_name = str(getattr(row, "table_name", "") or "")
                    key = (schema_name, table_name)
                    if key not in tables:
                        tables[key] = []
                        order.append(key)
                    is_nullable = str(getattr(row, "is_nullable", "YES") or "YES")
                    tables[key].append(
                        ColumnSchema(
                            name=str(getattr(row, "column_name", "") or ""),
                            data_type=str(getattr(row, "type_name", "") or ""),
                            nullable=is_nullable.upper() != "NO",
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - classified, raw text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return [
            TableSchema(name=table, schema=schema or None, columns=tuple(tables[(schema, table)]))
            for schema, table in order
        ]

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
