"""Snowflake database-direct backend: config, key-pair auth, driver, dialect.

The live ``snowflake.connector`` is an optional extra not installed in the test
env, so ``connect`` is exercised only to the point of the classified
``DRIVER_UNAVAILABLE`` guard; the offline dump driver stands in for the pull path
(the same contract every backend passes). Key-pair loading is tested for real
against a freshly generated RSA key.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.database_direct.config import (
    ConnectionConfig,
    SnowflakeConfig,
    TableExtraction,
)
from app.adapters.database_direct.drivers.base import DbCredentials
from app.adapters.database_direct.drivers.snowflake import (
    SnowflakeDriver,
    _load_private_key_der,
)
from app.adapters.database_direct.errors import DatabaseDirectError, DbDirectErrorCode
from app.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.adapters.database_direct.query_builder import ParamStyle, build_select, paramstyle_for


def _rsa_pem(*, passphrase: bytes | None = None) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
    return pem.decode("utf-8")


def _snowflake_config() -> ConnectionConfig:
    return ConnectionConfig(
        backend="snowflake",
        database="ANALYTICS",
        schemas=("CORE",),
        snowflake=SnowflakeConfig(account="ab12345.eu-west-1", warehouse="ETL_WH", role="AEQ_RO"),
    )


class TestSnowflakeConfig:
    def test_snowflake_backend_requires_config_block(self) -> None:
        with pytest.raises(ValueError, match="requires a 'snowflake' configuration block"):
            ConnectionConfig(backend="snowflake")

    def test_snowflake_backend_with_block_is_valid(self) -> None:
        config = _snowflake_config()
        assert config.snowflake is not None
        assert config.snowflake.warehouse == "ETL_WH"
        assert config.snowflake.use_streams is False


class TestKeyPairLoading:
    def test_loads_unencrypted_pkcs8_key(self) -> None:
        der = _load_private_key_der({"snowflake_private_key": _rsa_pem()}, "your warehouse")
        assert isinstance(der, bytes) and len(der) > 0

    def test_loads_encrypted_key_with_passphrase(self) -> None:
        pem = _rsa_pem(passphrase=b"s3cret")
        der = _load_private_key_der(
            {"snowflake_private_key": pem, "private_key_passphrase": "s3cret"}, "your warehouse"
        )
        assert isinstance(der, bytes) and len(der) > 0

    def test_missing_key_is_configuration_error(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            _load_private_key_der({}, "your warehouse")
        assert excinfo.value.code is DbDirectErrorCode.CONFIGURATION_ERROR

    def test_unparseable_key_is_credential_invalid(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            _load_private_key_der({"snowflake_private_key": "not a real pem"}, "your warehouse")
        assert excinfo.value.code is DbDirectErrorCode.CREDENTIAL_INVALID

    def test_wrong_passphrase_is_credential_invalid(self) -> None:
        pem = _rsa_pem(passphrase=b"right")
        with pytest.raises(DatabaseDirectError) as excinfo:
            _load_private_key_der(
                {"snowflake_private_key": pem, "private_key_passphrase": "wrong"}, "your warehouse"
            )
        assert excinfo.value.code is DbDirectErrorCode.CREDENTIAL_INVALID

    def test_encrypted_key_without_passphrase_is_configuration_error(self) -> None:
        pem = _rsa_pem(passphrase=b"needs-a-passphrase")
        with pytest.raises(DatabaseDirectError) as excinfo:
            _load_private_key_der({"snowflake_private_key": pem}, "your warehouse")
        assert excinfo.value.code is DbDirectErrorCode.CONFIGURATION_ERROR


class TestSnowflakeDriver:
    def test_identity_and_capabilities(self) -> None:
        driver = SnowflakeDriver()
        assert driver.backend() == "snowflake"
        caps = driver.capabilities()
        assert caps.supports_change_data_capture is True  # Snowflake Streams
        assert caps.supports_incremental_timestamp is True

    def test_connect_classifies_before_network(self) -> None:
        # A missing private key must produce a CLASSIFIED error before any network
        # I/O, whether or not the optional connector is installed: with the
        # connector absent the driver-import guard fires (DRIVER_UNAVAILABLE);
        # with it present the key guard fires (CONFIGURATION_ERROR). Either way it
        # is a classified DatabaseDirectError, never a raw connector exception.
        creds = DbCredentials(username="AEQ_SVC", extra={})
        with pytest.raises(DatabaseDirectError) as excinfo:
            SnowflakeDriver().connect(_snowflake_config(), creds)
        assert excinfo.value.code in (
            DbDirectErrorCode.CONFIGURATION_ERROR,
            DbDirectErrorCode.DRIVER_UNAVAILABLE,
        )


class TestSnowflakeDialect:
    def test_paramstyle_is_qmark(self) -> None:
        assert paramstyle_for("snowflake") is ParamStyle.QMARK

    def test_select_uses_ansi_double_quotes_and_qmark_binds(self) -> None:
        ext = TableExtraction(
            table="CORE.POSITIONS",
            record_kind="position",
            filters={"CURRENCY": "GHS"},
        )
        query = build_select(ext, "snowflake")
        assert query.sql == 'SELECT * FROM "CORE"."POSITIONS" WHERE "CURRENCY" = ?'
        assert query.parameters == ["GHS"]

    def test_offline_pull_round_trips_snowflake_dialect(self) -> None:
        # The offline dump driver consumes the exact BuiltQuery the builder emits,
        # so it proves the Snowflake dialect (quoting + qmark) round-trips end to end.
        dump = Dump(
            database="ANALYTICS",
            tables=(),
            rows={"CORE.POSITIONS": [{"REF": "P1", "CURRENCY": "GHS"}]},
        )
        ext = TableExtraction(table="CORE.POSITIONS", record_kind="position")
        query = build_select(ext, "snowflake")
        creds = DbCredentials(username="AEQ_SVC", extra={"snowflake_private_key": _rsa_pem()})
        with OfflineDumpDriver(dump, backend="snowflake").connect(
            _snowflake_config(), creds
        ) as session:
            result = session.fetch(query)
        assert result.as_dicts() == [{"REF": "P1", "CURRENCY": "GHS"}]
