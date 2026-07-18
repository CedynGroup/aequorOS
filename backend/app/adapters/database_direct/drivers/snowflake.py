"""Snowflake warehouse driver over ``snowflake-connector-python`` (lazy-imported).

Snowflake is a cloud data warehouse, not an operational core: banks with mature
analytics land historical position data, market feeds, and pre-aggregated
datasets there, and AequorOS reads point-in-time or historical series from it
(see data_engine.md §11.3). This driver:

- authenticates the read-only AequorOS service user with **key-pair auth** — the
  RSA private key (PEM) and optional passphrase arrive in the transient
  ``DbCredentials.extra`` from the vault, are loaded into DER for the connector,
  and are never persisted or logged. Username/password is not sanctioned for
  Snowflake (key-pair is Snowflake's own stronger, recommended pattern);
- runs every query on a bank-owned **warehouse** (sized compute, per-second
  billed) named in :class:`SnowflakeConfig`, tolerating the auto-suspend warm-up;
- introspects via the standard ``INFORMATION_SCHEMA.COLUMNS`` view and runs the
  parameterized ``SELECT`` the query builder emits (``qmark`` paramstyle).

Both ``snowflake.connector`` and ``cryptography`` are imported inside
:meth:`connect`; their absence is a classified ``DRIVER_UNAVAILABLE`` rather than
an import-time break. Native change capture uses Snowflake **Streams**, an
out-of-band object the bank creates and grants; it is not a query surface this
driver invents, so incremental extraction falls back to a timestamp cursor when
streams are not configured (see :meth:`capabilities` and data_engine.md §11.3).
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

# Bound the auth/login negotiation so a wrong account or unreachable region fails
# fast (seconds) rather than hanging for the whole query timeout.
_LOGIN_TIMEOUT_SECONDS = 30

# Credential ``extra`` keys carrying the key-pair material. The private key is the
# PKCS#8 PEM the bank registered the public half of against the AequorOS service
# user in Snowflake; the passphrase is optional (set only if the key is encrypted).
_PRIVATE_KEY_KEYS = ("snowflake_private_key", "private_key")
_PRIVATE_KEY_PASSPHRASE_KEY = "private_key_passphrase"

# INFORMATION_SCHEMA.COLUMNS spans tables and views the role can see in the
# connection's current database; ORDINAL_POSITION preserves column order. Schema
# names are bound, never interpolated.
_INTROSPECT_BASE = (
    "SELECT table_schema, table_name, column_name, data_type, is_nullable "
    "FROM information_schema.columns"
)


def _private_key_material(extra: dict[str, str]) -> str:
    for key in _PRIVATE_KEY_KEYS:
        value = (extra.get(key) or "").strip()
        if value:
            return value
    return ""


def _import_connector(database_label: str) -> Any:
    try:
        import snowflake.connector  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"snowflake-connector-python not installed on the worker: {exc}",
        ) from exc
    return snowflake.connector


def _load_private_key_der(extra: dict[str, str], database_label: str) -> bytes:
    """Load the PKCS#8 PEM private key from ``extra`` and return DER for the connector.

    The connector expects the key as DER-encoded PKCS#8 bytes. A missing or
    unparseable key is a configuration error (never a bank-facing leak of the key
    material or the underlying crypto exception text).
    """
    pem = _private_key_material(extra)
    if not pem:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CONFIGURATION_ERROR, database=database_label),
            internal_detail="snowflake connection has no key-pair private key in credentials",
        )
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: PLC0415 - lazy optional
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"cryptography not installed on the worker: {exc}",
        ) from exc
    passphrase = (extra.get(_PRIVATE_KEY_PASSPHRASE_KEY) or "").strip() or None
    try:
        key = serialization.load_pem_private_key(
            pem.encode("utf-8"),
            password=passphrase.encode("utf-8") if passphrase else None,
        )
    except TypeError as exc:
        # cryptography raises TypeError when the passphrase presence does not match
        # the key: an encrypted key with no passphrase, or a passphrase for a key
        # that is not encrypted. Actionable + a configuration issue, not a bad key.
        detail = (
            "snowflake private key is encrypted but no passphrase was supplied"
            if passphrase is None
            else "a passphrase was supplied but the snowflake private key is not encrypted"
        )
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CONFIGURATION_ERROR, database=database_label),
            internal_detail=detail,
        ) from exc
    except ValueError as exc:
        # Wrong passphrase for an encrypted key, or a PEM that is not a valid
        # PKCS#8 private key at all.
        detail = (
            "the passphrase for the encrypted snowflake private key is incorrect"
            if passphrase is not None
            else "the snowflake private key is not a valid PKCS#8 PEM"
        )
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CREDENTIAL_INVALID, database=database_label),
            internal_detail=detail,
        ) from exc
    try:
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    except Exception as exc:  # noqa: BLE001 - crypto detail stays internal
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CREDENTIAL_INVALID, database=database_label),
            internal_detail="snowflake private key could not be serialized to DER",
        ) from exc


class SnowflakeDriver(DatabaseDriver):
    """Opens read-only Snowflake sessions with key-pair auth on a named warehouse."""

    def backend(self) -> Backend:
        return "snowflake"

    def capabilities(self) -> DriverCapabilities:
        # Streams are Snowflake's native CDC primitive (opt-in per table where the
        # bank grants stream access); a timestamp cursor is the fallback.
        return DriverCapabilities(
            supports_change_data_capture=True,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(
        self, connection: ConnectionConfig, credentials: DbCredentials
    ) -> _SnowflakeSession:
        connector = _import_connector(connection.display_label)
        sf = connection.snowflake
        if sf is None:  # guarded by ConnectionConfig validator; defensive here
            raise DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="snowflake backend selected without a snowflake config block",
            )
        private_key_der = _load_private_key_der(credentials.extra, connection.display_label)
        params: dict[str, Any] = {
            "account": sf.account,
            "user": credentials.username,
            "private_key": private_key_der,
            "warehouse": sf.warehouse,
            "login_timeout": _LOGIN_TIMEOUT_SECONDS,
            "network_timeout": max(0, connection.query_timeout_seconds) or None,
            "paramstyle": "qmark",
            "client_session_keep_alive": False,
            "application": "AequorOS",
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": max(0, connection.query_timeout_seconds),
                # Read-only guardrail: this role must never autocommit a write; the
                # adapter only ever issues SELECT, but the parameter is belt-and-braces.
                "AUTOCOMMIT": False,
            },
        }
        if sf.role:
            params["role"] = sf.role
        if connection.database:
            params["database"] = connection.database
        if sf.default_schema:
            params["schema"] = sf.default_schema
        try:
            conn = connector.connect(**params)
        except Exception as exc:  # noqa: BLE001 - classified; raw Snowflake text stays internal
            raise classify_dbapi_error(exc, database=connection.display_label) from exc
        return _SnowflakeSession(conn, database_label=connection.display_label)


class _SnowflakeSession:
    """A live, read-only Snowflake session bound to a warehouse."""

    def __init__(self, conn: Any, *, database_label: str) -> None:
        self._conn = conn
        self._database_label = database_label
        # Best-effort credit/observability hook: the last query's Snowflake query id.
        # Full per-institution credit attribution reads ACCOUNT_USAGE (latent up to a
        # few hours) and is a downstream follow-up, not a synchronous per-pull figure.
        self.last_query_id: str | None = None

    def __enter__(self) -> _SnowflakeSession:
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
        sql = _INTROSPECT_BASE
        params: list[Any] = []
        if schemas:
            placeholders = ", ".join("?" for _ in schemas)
            sql += f" WHERE table_schema IN ({placeholders})"
            # Unquoted Snowflake identifiers fold to upper-case in the catalog.
            params = [schema.upper() for schema in schemas]
        sql += " ORDER BY table_schema, table_name, ordinal_position"
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - classified; raw text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return _assemble_tables(rows)

    def fetch(self, query: BuiltQuery) -> QueryResult:
        try:
            cursor = self._conn.cursor()
            cursor.execute(query.sql, list(query.parameters) if query.parameters else None)
            self.last_query_id = getattr(cursor, "sfqid", None)
            columns = tuple(column[0] for column in cursor.description or ())
            rows = tuple(tuple(row) for row in cursor.fetchall())
        except Exception as exc:  # noqa: BLE001 - classified; raw text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return QueryResult(columns=columns, rows=rows)


def _assemble_tables(rows: list[Any]) -> list[TableSchema]:
    grouped: dict[tuple[str, str], list[ColumnSchema]] = {}
    order: list[tuple[str, str]] = []
    for table_schema, table, column, data_type, is_nullable in rows:
        key = (str(table_schema), str(table))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            ColumnSchema(
                name=str(column),
                data_type=str(data_type),
                nullable=str(is_nullable).upper() in ("YES", "Y", "TRUE"),
            )
        )
    return [
        TableSchema(name=table, schema=table_schema, columns=tuple(grouped[(table_schema, table)]))
        for table_schema, table in order
    ]
