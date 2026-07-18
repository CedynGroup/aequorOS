"""Offline pull orchestration over the synthetic-dump driver."""

from __future__ import annotations

import pytest

from app.adapters.database_direct.config import ExtractionSpec, TableExtraction
from app.adapters.database_direct.drivers.base import DbCredentials
from app.adapters.database_direct.errors import DatabaseDirectError, DbDirectErrorCode
from app.adapters.database_direct.fixtures import OfflineDumpDriver, load_dump
from app.adapters.database_direct.pull import stage_pull

from .conftest import (
    AS_OF,
    FIXTURES_ORACLE,
    FIXTURES_SQLSERVER,
    oracle_connection,
    oracle_normalization,
    oracle_spec,
    sqlserver_connection,
    sqlserver_spec,
)

_CREDS = DbCredentials(username="SVC.AEQUOROS", password="unused-offline")


def _sqlserver_driver() -> OfflineDumpDriver:
    return OfflineDumpDriver(load_dump(FIXTURES_SQLSERVER), backend="sqlserver")


def _oracle_driver() -> OfflineDumpDriver:
    return OfflineDumpDriver(load_dump(FIXTURES_ORACLE), backend="oracle")


class TestFullPull:
    def test_full_pull_stages_every_table(self) -> None:
        bundle = stage_pull(
            _sqlserver_driver(), sqlserver_connection(), _CREDS, sqlserver_spec(), as_of=AS_OF
        )
        assert bundle.backend == "sqlserver"
        assert bundle.source_database == "COREBANK"
        assert bundle.extraction_mode == "full"
        counts = {t.name: t.row_count for t in bundle.tables}
        assert counts == {
            "DBO.GL_ACCOUNTS": 4,
            "DBO.CUSTOMERS": 3,
            "DBO.PRODUCTS": 3,
            "DBO.POSITIONS": 3,
            "DBO.FX_RATES": 3,
        }

    def test_pull_is_deterministic(self) -> None:
        driver = _sqlserver_driver()
        first = stage_pull(driver, sqlserver_connection(), _CREDS, sqlserver_spec(), as_of=AS_OF)
        second = stage_pull(driver, sqlserver_connection(), _CREDS, sqlserver_spec(), as_of=AS_OF)
        assert first.to_json() == second.to_json()

    def test_oracle_locale_and_packed_dates_normalized_in_bundle(self) -> None:
        bundle = stage_pull(
            _oracle_driver(),
            oracle_connection(),
            _CREDS,
            oracle_spec(),
            as_of=AS_OF,
            normalization=oracle_normalization(),
        )
        positions = next(t for t in bundle.tables if t.name == "COREBANK.POSITIONS")
        first = positions.rows[0]
        assert first["BAL"] == "1200000.00"  # "1.200.000,00" -> canonical
        assert first["RATE"] == "0.285"  # "0,285" -> canonical


class TestIncrementalPull:
    def test_incremental_filters_by_cursor_and_records_high_water(self) -> None:
        bundle = stage_pull(
            _sqlserver_driver(),
            sqlserver_connection(),
            _CREDS,
            sqlserver_spec(),
            as_of=AS_OF,
            mode="incremental",
            incremental_cursors={"DBO.POSITIONS": "2026-06-30T06:05:00"},
        )
        positions = next(t for t in bundle.tables if t.name == "DBO.POSITIONS")
        assert [r["POS_ID"] for r in positions.rows] == ["P900003"]
        assert positions.extraction_mode == "incremental"
        assert bundle.incremental_cursors["DBO.POSITIONS"] == "2026-06-30T07:40:00"

    def test_cursorless_table_degrades_to_full_with_warning(self) -> None:
        bundle = stage_pull(
            _sqlserver_driver(),
            sqlserver_connection(),
            _CREDS,
            sqlserver_spec(),
            as_of=AS_OF,
            mode="incremental",
        )
        gl = next(t for t in bundle.tables if t.name == "DBO.GL_ACCOUNTS")
        assert gl.extraction_mode == "full"
        assert gl.row_count == 4  # noqa: PLR2004
        assert any("DBO.GL_ACCOUNTS" in w for w in bundle.warnings)

    def test_first_incremental_run_without_cursor_pulls_all(self) -> None:
        bundle = stage_pull(
            _sqlserver_driver(),
            sqlserver_connection(),
            _CREDS,
            sqlserver_spec(),
            as_of=AS_OF,
            mode="incremental",
        )
        positions = next(t for t in bundle.tables if t.name == "DBO.POSITIONS")
        assert positions.row_count == 3  # noqa: PLR2004 - no since -> full history


class TestReplicaPreferenceAndSafety:
    def test_replica_endpoints_are_preferred_over_primary(self) -> None:
        order = sqlserver_connection().endpoints_in_preference_order()
        assert order[0] == "corebank-ag-ro.sample-bank.internal:1433"
        assert order[-1] == "corebank-ag.sample-bank.internal:1433"

    def test_empty_credentials_are_rejected_by_the_driver(self) -> None:
        with pytest.raises(DatabaseDirectError) as excinfo:
            stage_pull(
                _sqlserver_driver(),
                sqlserver_connection(),
                DbCredentials(username=""),
                sqlserver_spec(),
                as_of=AS_OF,
            )
        assert excinfo.value.code is DbDirectErrorCode.CREDENTIAL_INVALID

    def test_equality_filters_are_applied(self) -> None:
        spec = ExtractionSpec(
            tables=(
                TableExtraction(
                    table="DBO.POSITIONS", record_kind="position", filters={"POS_TYPE": "DP"}
                ),
            )
        )
        bundle = stage_pull(_sqlserver_driver(), sqlserver_connection(), _CREDS, spec, as_of=AS_OF)
        assert [r["POS_ID"] for r in bundle.tables[0].rows] == ["P900003"]
