"""Generic JDBC-bridge driver over ``jaydebeapi`` + ``JPype1`` (lazy-imported).

For a bank whose core is only reachable through a vendor JDBC driver (common for
Finacle/FLEXCUBE estates and mainframe-fronted cores), this driver loads the
onboarding-supplied JDBC JAR, connects with the fully-qualified driver class and
a URL template, and runs the same parameterized ``SELECT`` statements every other
backend does. Nothing vendor-specific is invented: the driver class, the URL
template, the JAR path(s), and any TLS properties all come from
:class:`~app.adapters.database_direct.config.JdbcConfig`, supplied at onboarding.

Schema introspection uses the standard JDBC ``DatabaseMetaData`` API
(``getColumns``), which every compliant driver implements, so introspection is
portable across whatever core sits behind the JAR. Read-only intent is enforced
by executing only ``SELECT`` and never committing; TLS is enforced fail-closed
(see :meth:`connect`).

``jaydebeapi``/``JPype1`` are optional and JVM-backed: they are imported inside
:meth:`connect`, and their absence (or a missing JVM) is surfaced as a classified
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

# Tokens that indicate the onboarding-supplied JDBC url/props already request an
# encrypted transport. TLS is enforced fail-closed against this set.
_TLS_HINTS = ("ssl=true", "encrypt=true", "sslmode=require", "ssl=1", "sslconnection=true")


def _import_jaydebeapi(database_label: str) -> Any:
    try:
        import jaydebeapi  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"jaydebeapi/JPype1 not installed on the AequorOS worker: {exc}",
        ) from exc
    return jaydebeapi


class JdbcDriver(DatabaseDriver):
    """Opens read-only JDBC sessions via a vendor JAR."""

    def backend(self) -> Backend:
        return "jdbc"

    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            supports_change_data_capture=False,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> _JdbcSession:
        jdbc_cfg = connection.jdbc
        if jdbc_cfg is None:
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="jdbc backend selected without a jdbc config block",
            )
        jaydebeapi = _import_jaydebeapi(connection.display_label)

        endpoints = connection.endpoints_in_preference_order() or ("",)
        last_error: BaseException | None = None
        for index, endpoint in enumerate(endpoints):
            url = self._resolve_url(connection, endpoint)
            self._enforce_tls(connection, url, jdbc_cfg.properties)
            try:
                conn = jaydebeapi.connect(
                    jdbc_cfg.driver_class,
                    url,
                    self._driver_args(credentials, jdbc_cfg.properties),
                    list(jdbc_cfg.jar_paths) or None,
                )
                return _JdbcSession(conn, database_label=connection.display_label)
            except DatabaseDirectError:
                raise
            except Exception as exc:  # noqa: BLE001, PERF203 - failover + classify
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
                internal_detail="no JDBC endpoint resolved",
            )
        )

    def _resolve_url(self, connection: ConnectionConfig, endpoint: str) -> str:
        host, port = _split_endpoint(endpoint, connection)
        return (
            (connection.jdbc.url_template if connection.jdbc else "")
            .replace("{host}", host)
            .replace("{port}", str(port) if port is not None else "")
            .replace("{database}", connection.database)
        )

    def _driver_args(
        self, credentials: DbCredentials, properties: dict[str, str]
    ) -> dict[str, str]:
        # jaydebeapi accepts a {name: value} info map; user/password plus any
        # onboarding-supplied JDBC properties (e.g. encryption flags).
        args = {"user": credentials.username, "password": credentials.password}
        args.update(properties)
        return args

    def _enforce_tls(
        self, connection: ConnectionConfig, url: str, properties: dict[str, str]
    ) -> None:
        if not connection.tls.enabled:
            return
        haystack = (url + " " + " ".join(f"{k}={v}" for k, v in properties.items())).lower()
        if not any(hint in haystack for hint in _TLS_HINTS):
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.TLS_REQUIRED, database=connection.display_label
                ),
                internal_detail=(
                    "TLS required but JDBC url/properties declare no encryption hint; "
                    "refusing to open a possibly-cleartext JDBC connection"
                ),
            )


class _JdbcSession:
    """A live, read-only JDBC session over a jaydebeapi connection."""

    def __init__(self, conn: Any, *, database_label: str) -> None:
        self._conn = conn
        self._database_label = database_label

    def __enter__(self) -> _JdbcSession:
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
        """Introspect via the standard JDBC ``DatabaseMetaData.getColumns`` API."""
        try:
            metadata = self._conn.jconn.getMetaData()
            patterns = schemas or (None,)
            tables: dict[tuple[str, str], list[ColumnSchema]] = {}
            order: list[tuple[str, str]] = []
            for schema_pattern in patterns:
                result = metadata.getColumns(None, schema_pattern, "%", "%")
                self._drain_columns(result, tables, order)
        except Exception as exc:  # noqa: BLE001 - classified, raw JVM text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return [
            TableSchema(name=table, schema=schema or None, columns=tuple(tables[(schema, table)]))
            for schema, table in order
        ]

    def _drain_columns(
        self,
        result: Any,
        tables: dict[tuple[str, str], list[ColumnSchema]],
        order: list[tuple[str, str]],
    ) -> None:
        while result.next():
            schema = _jstr(result.getString("TABLE_SCHEM"))
            table = _jstr(result.getString("TABLE_NAME"))
            key = (schema, table)
            if key not in tables:
                tables[key] = []
                order.append(key)
            tables[key].append(
                ColumnSchema(
                    name=_jstr(result.getString("COLUMN_NAME")),
                    data_type=_jstr(result.getString("TYPE_NAME")),
                    nullable=_jstr(result.getString("IS_NULLABLE")).upper() != "NO",
                )
            )
        result.close()

    def fetch(self, query: BuiltQuery) -> QueryResult:
        try:
            cursor = self._conn.cursor()
            if query.parameters:
                cursor.execute(query.sql, list(query.parameters))
            else:
                cursor.execute(query.sql)
            columns = tuple(column[0] for column in cursor.description or ())
            rows = tuple(tuple(row) for row in cursor.fetchall())
            cursor.close()
        except Exception as exc:  # noqa: BLE001 - classified, raw text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return QueryResult(columns=columns, rows=rows)


def _split_endpoint(endpoint: str, connection: ConnectionConfig) -> tuple[str, int | None]:
    if not endpoint:
        return connection.host, connection.port
    if ":" in endpoint:
        host, _, port = endpoint.partition(":")
        return host, int(port) if port.isdigit() else connection.port
    return endpoint, connection.port


def _jstr(value: Any) -> str:
    """Coerce a possibly-``None`` Java string to a Python ``str``."""
    return "" if value is None else str(value)
