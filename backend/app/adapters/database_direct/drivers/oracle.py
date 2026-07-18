"""Native Oracle driver over ``python-oracledb`` thin mode (lazy-imported).

Thin mode needs no Oracle Instant Client, so this driver runs anywhere the
AequorOS worker runs. It builds a TLS (``TCPS``) connect descriptor to a bank's
Oracle core, connects the read-only service account, and runs the parameterized
``SELECT`` statements the query builder produces (Oracle uses named binds, which
the builder emits for this backend).

Schema introspection reads the standard Oracle data-dictionary view
``ALL_TAB_COLUMNS`` (which covers tables and views the account can see) — no
proprietary syntax is invented. Incremental extraction uses a configured
timestamp/SCN cursor column; native change capture (GoldenGate / LogMiner) is an
out-of-band replication facility, not a query surface, so it is intentionally
NOT hardcoded here (see :meth:`capabilities`).

``oracledb`` is imported inside :meth:`connect`; its absence is surfaced as a
classified ``DRIVER_UNAVAILABLE`` rather than breaking application import.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import io
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
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

_DEFAULT_ORACLE_PORT = 1521
# Bound the TLS/session negotiation so an mTLS-required endpoint that accepts the
# socket but rejects the wallet-less session fails fast (seconds) instead of hanging
# for the full query timeout — the classifier then surfaces an actionable message.
_CONNECT_TIMEOUT_SECONDS = 20

# Credential ``extra`` keys carrying Oracle mutual-TLS wallet material. Autonomous
# Database and any mTLS-required Oracle needs a client wallet; python-oracledb THIN
# mode reads it as an ``ewallet.pem`` under ``wallet_location``. The value may be the
# base64 of the vendor "Client Credentials" wallet ZIP (ewallet.pem is extracted) or
# a raw PEM string; the password is the one set when the wallet was downloaded.
# Both ``oracle_wallet`` and the shorter ``wallet`` alias are accepted.
_WALLET_MATERIAL_KEYS = ("oracle_wallet", "wallet")
_WALLET_PASSWORD_KEY = "wallet_password"
_THIN_WALLET_FILENAME = "ewallet.pem"


def _wallet_material(extra: dict[str, str]) -> str:
    for key in _WALLET_MATERIAL_KEYS:
        value = (extra.get(key) or "").strip()
        if value:
            return value
    return ""

# ALL_TAB_COLUMNS spans tables and views the connected account may read; column
# order is preserved via COLUMN_ID. Owners are bound, never interpolated.
_INTROSPECT_BASE = "SELECT owner, table_name, column_name, data_type, nullable FROM all_tab_columns"


def _import_oracledb(database_label: str) -> Any:
    try:
        import oracledb  # noqa: PLC0415 - deliberate lazy optional-driver import
    except ImportError as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.DRIVER_UNAVAILABLE, database=database_label),
            internal_detail=f"oracledb not installed on the AequorOS worker: {exc}",
        ) from exc
    return oracledb


def _materialize_wallet(extra: dict[str, str], database_label: str) -> str | None:
    """Write an Oracle mTLS wallet from credential ``extra`` to a private temp dir.

    Returns the wallet directory (holding ``ewallet.pem``) for ``wallet_location``, or
    ``None`` when no wallet was supplied. The caller MUST remove the directory once the
    session is closed — it holds the client private key. Accepts a base64-encoded vendor
    wallet ZIP (``ewallet.pem`` is extracted) or a raw PEM string.
    """
    material = _wallet_material(extra)
    if not material:
        return None
    wallet_dir = tempfile.mkdtemp(prefix="aeq-ora-wallet-")
    Path(wallet_dir).chmod(stat.S_IRWXU)  # 0700 — owner only
    pem_path = Path(wallet_dir) / _THIN_WALLET_FILENAME
    try:
        if material.startswith("-----BEGIN"):
            pem_path.write_text(material + "\n", encoding="utf-8")
        else:
            _extract_pem_from_zip(material, wallet_dir, database_label)
        pem_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception:
        shutil.rmtree(wallet_dir, ignore_errors=True)
        raise
    return wallet_dir


def _extract_pem_from_zip(material_b64: str, wallet_dir: str, database_label: str) -> None:
    """Extract ``ewallet.pem`` (+ tnsnames/sqlnet) from a base64 vendor wallet ZIP."""
    try:
        blob = base64.b64decode(material_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CONFIGURATION_ERROR, database=database_label),
            internal_detail="oracle wallet material is neither a PEM nor valid base64",
        ) from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise DatabaseDirectError(
            render_bank_facing(DbDirectErrorCode.CONFIGURATION_ERROR, database=database_label),
            internal_detail="oracle wallet is not a valid ZIP archive",
        ) from exc
    with archive as zf:
        by_name = {name.rsplit("/", 1)[-1]: name for name in zf.namelist()}
        if _THIN_WALLET_FILENAME not in by_name:
            raise DatabaseDirectError(
                render_bank_facing(DbDirectErrorCode.MUTUAL_TLS_REQUIRED, database=database_label),
                internal_detail=(
                    "wallet ZIP has no ewallet.pem; thin mode needs the PEM wallet "
                    "(re-download the Client Credentials with the PEM option)"
                ),
            )
        # Only the files thin mode reads; flat filenames prevent zip-slip path escapes.
        for wanted in (_THIN_WALLET_FILENAME, "tnsnames.ora", "sqlnet.ora"):
            if wanted in by_name:
                (Path(wallet_dir) / wanted).write_bytes(zf.read(by_name[wanted]))


class OracleDriver(DatabaseDriver):
    """Opens read-only Oracle thin-mode sessions."""

    def backend(self) -> Backend:
        return "oracle"

    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            supports_change_data_capture=False,
            supports_incremental_timestamp=True,
            supports_schema_introspection=True,
        )

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> _OracleSession:
        oracledb = _import_oracledb(connection.display_label)
        service = connection.service_name or connection.database
        endpoints = connection.endpoints_in_preference_order() or ("",)
        wallet_dir = _materialize_wallet(credentials.extra, connection.display_label)
        wallet_password = credentials.extra.get(_WALLET_PASSWORD_KEY) or None
        last_error: BaseException | None = None
        try:
            for index, endpoint in enumerate(endpoints):
                host, port = _split_endpoint(endpoint, connection)
                dsn = _connect_descriptor(host, port, service, connection)
                try:
                    conn = oracledb.connect(
                        user=credentials.username,
                        password=credentials.password,
                        dsn=dsn,
                        tcp_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                        **_tls_kwargs(connection, wallet_dir, wallet_password),
                    )
                    conn.autocommit = False  # read-only: never commit a transaction
                    conn.call_timeout = max(0, connection.query_timeout_seconds) * 1000
                    session = _OracleSession(
                        conn, database_label=connection.display_label, wallet_dir=wallet_dir
                    )
                    wallet_dir = None  # ownership passes to the session (removed on close)
                    return session
                except oracledb.Error as exc:  # noqa: PERF203 - endpoint failover is intentional
                    last_error = exc
                    if index < len(endpoints) - 1:
                        continue
        finally:
            if wallet_dir is not None:  # connect failed — never leak the client private key
                shutil.rmtree(wallet_dir, ignore_errors=True)
        raise (
            classify_dbapi_error(last_error, database=connection.display_label)
            if last_error is not None
            else DatabaseDirectError(
                render_bank_facing(
                    DbDirectErrorCode.CONFIGURATION_ERROR, database=connection.display_label
                ),
                internal_detail="no Oracle endpoint resolved",
            )
        )


def _connect_descriptor(
    host: str, port: int | None, service: str, connection: ConnectionConfig
) -> str:
    protocol = "TCPS" if connection.tls.enabled else "TCP"
    resolved_port = port or _DEFAULT_ORACLE_PORT
    security = ""
    if connection.tls.enabled:
        match = "ON" if connection.tls.verify_server_certificate else "OFF"
        dn = (
            f'(SSL_SERVER_CERT_DN="{connection.tls.server_dn_match}")'
            if connection.tls.server_dn_match
            else ""
        )
        security = f"(SECURITY=(SSL_SERVER_DN_MATCH={match}){dn})"
    return (
        f"(DESCRIPTION=(ADDRESS=(PROTOCOL={protocol})(HOST={host})(PORT={resolved_port}))"
        f"(CONNECT_DATA=(SERVICE_NAME={service})){security})"
    )


def _tls_kwargs(
    connection: ConnectionConfig, wallet_dir: str | None, wallet_password: str | None
) -> dict[str, Any]:
    """Thin-mode TLS kwargs: the mTLS wallet (client cert + CA) when one was supplied.

    A supplied wallet (``wallet_dir`` holding ``ewallet.pem``) is exactly what Oracle
    Autonomous Database and any mTLS-required endpoint need; ``config_dir`` is set too so
    a bundled ``tnsnames.ora`` resolves. Falls back to a configured CA/wallet path.
    """
    if not connection.tls.enabled:
        return {}
    if wallet_dir is not None:
        kwargs: dict[str, Any] = {"wallet_location": wallet_dir, "config_dir": wallet_dir}
        if wallet_password:
            kwargs["wallet_password"] = wallet_password
        return kwargs
    if connection.tls.ca_cert_path:
        return {"wallet_location": connection.tls.ca_cert_path}
    return {}


class _OracleSession:
    """A live, read-only oracledb session."""

    def __init__(self, conn: Any, *, database_label: str, wallet_dir: str | None = None) -> None:
        self._conn = conn
        self._database_label = database_label
        self._wallet_dir = wallet_dir

    def __enter__(self) -> _OracleSession:
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
        if self._wallet_dir is not None:  # remove the client private-key material
            shutil.rmtree(self._wallet_dir, ignore_errors=True)
            self._wallet_dir = None

    def introspect(self, schemas: tuple[str, ...]) -> list[TableSchema]:
        sql = _INTROSPECT_BASE
        params: dict[str, Any] = {}
        if schemas:
            names = ", ".join(f":o{i}" for i in range(len(schemas)))
            sql += f" WHERE owner IN ({names})"
            params = {f"o{i}": owner.upper() for i, owner in enumerate(schemas)}
        sql += " ORDER BY owner, table_name, column_id"
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - classified, ORA- text stays internal
            raise classify_dbapi_error(exc, database=self._database_label) from exc
        return _assemble_tables(rows)

    def fetch(self, query: BuiltQuery) -> QueryResult:
        try:
            cursor = self._conn.cursor()
            cursor.execute(query.sql, query.parameters or {})
            columns = tuple(column[0] for column in cursor.description or ())
            rows = tuple(tuple(row) for row in cursor.fetchall())
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


def _assemble_tables(rows: list[Any]) -> list[TableSchema]:
    grouped: dict[tuple[str, str], list[ColumnSchema]] = {}
    order: list[tuple[str, str]] = []
    for owner, table, column, data_type, nullable in rows:
        key = (str(owner), str(table))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            ColumnSchema(
                name=str(column),
                data_type=str(data_type),
                nullable=str(nullable).upper() == "Y",
            )
        )
    return [
        TableSchema(name=table, schema=owner, columns=tuple(grouped[(owner, table)]))
        for owner, table in order
    ]
