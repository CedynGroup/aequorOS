"""Backend drivers: registry, lazy-import classification, TLS, and descriptors."""

from __future__ import annotations

import pytest

from app.adapters.database_direct.config import (
    ConnectionConfig,
    JdbcConfig,
    TlsConfig,
)
from app.adapters.database_direct.drivers import (
    JdbcDriver,
    OdbcDriver,
    OracleDriver,
    SqlServerDriver,
    driver_for,
)
from app.adapters.database_direct.drivers.base import DbCredentials
from app.adapters.database_direct.drivers.oracle import _connect_descriptor
from app.adapters.database_direct.errors import DatabaseDirectError, DbDirectErrorCode

from .conftest import odbc_connection, oracle_connection, sqlserver_connection

_CREDS = DbCredentials(username="SVC", password="pw")


def _jdbc_connection(
    *, tls: bool = True, url: str = "jdbc:sqlserver://{host}:{port}"
) -> ConnectionConfig:
    return ConnectionConfig(
        backend="jdbc",
        host="corebank.internal",
        port=1433,
        database="COREBANK",
        display_label="Sample Bank JDBC core",
        tls=TlsConfig(enabled=tls),
        jdbc=JdbcConfig(
            driver_class="com.microsoft.sqlserver.jdbc.SQLServerDriver",
            url_template=url,
            jar_paths=("/opt/jdbc/mssql-jdbc.jar",),
        ),
    )


class TestRegistry:
    def test_driver_for_maps_each_backend(self) -> None:
        assert isinstance(driver_for("oracle"), OracleDriver)
        assert isinstance(driver_for("sqlserver"), SqlServerDriver)
        assert isinstance(driver_for("jdbc"), JdbcDriver)
        assert isinstance(driver_for("odbc"), OdbcDriver)

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError, match="No database-direct driver"):
            driver_for("db2")  # type: ignore[arg-type]

    def test_backends_and_capabilities(self) -> None:
        for backend in ("oracle", "sqlserver", "jdbc", "odbc"):
            driver = driver_for(backend)  # type: ignore[arg-type]
            assert driver.backend() == backend
            caps = driver.capabilities()
            assert caps.supports_incremental_timestamp is True
            assert caps.supports_change_data_capture is False
            assert caps.supports_schema_introspection is True


class TestLazyDriverImportClassification:
    """The optional native/JVM drivers are not installed in CI: their absence
    must classify as DRIVER_UNAVAILABLE, not break import or leak a traceback."""

    def test_sqlserver_without_pyodbc(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            SqlServerDriver().connect(sqlserver_connection(), _CREDS)
        assert excinfo.value.code is DbDirectErrorCode.DRIVER_UNAVAILABLE
        assert "pyodbc" in excinfo.value.internal_detail

    def test_odbc_without_pyodbc(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            OdbcDriver().connect(odbc_connection(), _CREDS)
        assert excinfo.value.code is DbDirectErrorCode.DRIVER_UNAVAILABLE

    def test_jdbc_without_jaydebeapi(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            JdbcDriver().connect(_jdbc_connection(), _CREDS)
        assert excinfo.value.code is DbDirectErrorCode.DRIVER_UNAVAILABLE


class TestTlsFailClosed:
    def test_jdbc_refuses_when_no_encryption_hint(self) -> None:
        driver = JdbcDriver()
        connection = _jdbc_connection(tls=True, url="jdbc:sqlserver://{host}:{port}")
        with pytest.raises(DatabaseDirectError) as excinfo:
            driver._enforce_tls(connection, "jdbc:sqlserver://h:1433", {})
        assert excinfo.value.code is DbDirectErrorCode.TLS_REQUIRED

    def test_jdbc_accepts_when_encryption_declared(self) -> None:
        driver = JdbcDriver()
        connection = _jdbc_connection(tls=True)
        # An encryption hint in the URL satisfies the guard (no raise).
        driver._enforce_tls(connection, "jdbc:sqlserver://h:1433;encrypt=true", {})

    def test_jdbc_tls_disabled_skips_guard(self) -> None:
        driver = JdbcDriver()
        connection = _jdbc_connection(tls=False)
        driver._enforce_tls(connection, "jdbc:sqlserver://h:1433", {})

    def test_odbc_refuses_when_no_encryption_hint(self) -> None:
        driver = OdbcDriver()
        connection = odbc_connection()
        with pytest.raises(DatabaseDirectError) as excinfo:
            driver._enforce_tls(connection, "DRIVER={x};SERVER=h;UID=u;PWD=p;")
        assert excinfo.value.code is DbDirectErrorCode.TLS_REQUIRED

    def test_odbc_accepts_when_encryption_declared(self) -> None:
        driver = OdbcDriver()
        driver._enforce_tls(odbc_connection(), "DRIVER={x};SERVER=h;Encrypt=yes;")


class TestOracleDescriptor:
    def test_tcps_used_when_tls_enabled(self) -> None:
        descriptor = _connect_descriptor(
            "corebank-adg.internal", 1521, "COREBANK", oracle_connection()
        )
        assert "PROTOCOL=TCPS" in descriptor
        assert "SSL_SERVER_DN_MATCH=ON" in descriptor
        assert "SERVICE_NAME=COREBANK" in descriptor

    def test_tcp_used_when_tls_disabled(self) -> None:
        connection = ConnectionConfig(
            backend="oracle",
            host="h",
            port=1521,
            service_name="COREBANK",
            tls=TlsConfig(enabled=False),
        )
        descriptor = _connect_descriptor("h", 1521, "COREBANK", connection)
        assert "PROTOCOL=TCP)" in descriptor
        assert "SECURITY" not in descriptor
