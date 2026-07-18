"""Shared builders and fixtures for the database-direct adapter tests.

The synthetic Oracle and SQL Server dumps under ``fixtures_oracle`` /
``fixtures_sqlserver`` are pulled OFFLINE through the real
:class:`OfflineDumpDriver` + :func:`stage_pull_to_path` path, producing the
staged bundle each contract test then reads. One :class:`MappingConfig` serves
both backends because their dumps share column names; only the physical table
qualifiers (``DBO.*`` vs ``COREBANK.*``) and the number locale differ, which is
exactly what a config-driven, no-per-bank-code adapter should absorb.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.adapters.database_direct.adapter import DatabaseDirectAdapter
from app.adapters.database_direct.config import (
    ConnectionConfig,
    ExtractionSpec,
    OdbcConfig,
    TableExtraction,
)
from app.adapters.database_direct.fixtures import stage_bundle_from_dump
from app.adapters.database_direct.normalization import NormalizationPolicy
from app.domain.ingestion.adapter import SourceAdapter
from app.domain.ingestion.contracts import (
    AdapterConfig,
    EntityMapping,
    MappingConfig,
    ReferenceMapping,
)

FIXTURES_SQLSERVER = Path(__file__).parent / "fixtures_sqlserver"
FIXTURES_ORACLE = Path(__file__).parent / "fixtures_oracle"

AS_OF = date(2026, 6, 30)


def dbdirect_mapping_config() -> MappingConfig:
    """The onboarding mapping: physical columns -> canonical fields, shared by
    both backends (their dumps use identical column names)."""
    return MappingConfig(
        field_mappings={
            "gl_account": EntityMapping(
                source_table="GL_ACCOUNTS",
                fields={
                    "source_reference": "ACCT_CODE",
                    "account_code": "ACCT_CODE",
                    "name": "ACCT_NAME",
                    "account_class": "ACCT_CLASS",
                    "currency": "CCY",
                    "balance": "BAL",
                },
            ),
            "counterparty": EntityMapping(
                source_table="CUSTOMERS",
                fields={
                    "source_reference": "CUST_ID",
                    "name": "CUST_NAME",
                    "counterparty_type": "CUST_TYPE",
                    "country_code": "COUNTRY",
                },
                attribute_columns=["TIN"],
            ),
            "product": EntityMapping(
                source_table="PRODUCTS",
                fields={
                    "source_reference": "PROD_CODE",
                    "product_code": "PROD_CODE",
                    "name": "PROD_NAME",
                },
            ),
            "position": EntityMapping(
                source_table="POSITIONS",
                fields={
                    "source_reference": "POS_ID",
                    "position_type": "POS_TYPE",
                    "currency": "CCY",
                    "balance": "BAL",
                    "counterparty_reference": "CUST_ID",
                    "product_code": "PROD_CODE",
                    "contractual_maturity": "MATURITY",
                    "interest_rate": "RATE",
                    "rate_type": "RATE_TYPE",
                },
                attribute_columns=["ECL_PROVISION"],
            ),
        },
        reference_mappings={
            "fx": ReferenceMapping(source_table="FX_RATES", dataset_kind="fx_rates_current"),
        },
        enum_mappings={
            "account_class": {"A": "ASSET", "L": "LIABILITY", "E": "EQUITY"},
            "counterparty_type": {
                "COR": "CORPORATE",
                "RET": "RETAIL_INDIVIDUAL",
                "SME": "SME",
            },
            "position_type": {"LN": "LOAN", "DP": "DEPOSIT"},
            "rate_type": {"F": "FIXED", "V": "FLOATING"},
        },
        product_mappings={
            "LN.CORP.5Y": "CORPORATE_LOAN_UNRATED_100RW",
            "LN.RETAIL.MORT": "RESIDENTIAL_MORTGAGE_35RW",
        },
    )


def _spec(schema: str, *, include_reference: bool = True) -> ExtractionSpec:
    entity_tables = (
        TableExtraction(table=f"{schema}.GL_ACCOUNTS", record_kind="gl_account"),
        TableExtraction(table=f"{schema}.CUSTOMERS", record_kind="counterparty"),
        TableExtraction(table=f"{schema}.PRODUCTS", record_kind="product"),
        TableExtraction(
            table=f"{schema}.POSITIONS",
            record_kind="position",
            incremental_column="UPDATED_AT",
        ),
    )
    reference_tables = (
        (
            TableExtraction(
                table=f"{schema}.FX_RATES",
                record_kind="reference",
                dataset_kind="fx_rates_current",
            ),
        )
        if include_reference
        else ()
    )
    return ExtractionSpec(tables=(*entity_tables, *reference_tables))


def sqlserver_spec() -> ExtractionSpec:
    return _spec("DBO")


def oracle_spec() -> ExtractionSpec:
    return _spec("COREBANK")


def sqlserver_connection() -> ConnectionConfig:
    return ConnectionConfig(
        backend="sqlserver",
        host="corebank-ag.sample-bank.internal",
        port=1433,
        database="COREBANK",
        schemas=("DBO",),
        read_replicas=("corebank-ag-ro.sample-bank.internal:1433",),
        display_label="Sample Bank SQL Server core",
        credential_ref="vault://institutions/sample/db_direct/sqlserver/default",
    )


def oracle_connection() -> ConnectionConfig:
    return ConnectionConfig(
        backend="oracle",
        host="corebank-scan.sample-bank.internal",
        port=1521,
        database="COREBANK",
        service_name="COREBANK",
        schemas=("COREBANK",),
        read_replicas=("corebank-adg.sample-bank.internal:1521",),
        display_label="Sample Bank Oracle core",
        credential_ref="vault://institutions/sample/db_direct/oracle/default",
    )


def odbc_connection() -> ConnectionConfig:
    return ConnectionConfig(
        backend="odbc",
        host="corebank.sample-bank.internal",
        port=1433,
        database="COREBANK",
        schemas=("DBO",),
        display_label="Sample Bank ODBC core",
        odbc=OdbcConfig(
            driver_name="ODBC Driver 18 for SQL Server",
            extra_keywords={"Encrypt": "yes", "TrustServerCertificate": "no"},
        ),
    )


def oracle_normalization() -> NormalizationPolicy:
    """The Oracle dump stores European-locale numbers; normalize them on pull."""
    return NormalizationPolicy(
        decimal_separator=",",
        grouping_separator=".",
        locale_number_columns=("BAL", "RATE", "ECL_PROVISION", "MID_RATE"),
    )


def stage_sqlserver_config(tmp_path: Path) -> AdapterConfig:
    path = tmp_path / "dbdirect-sqlserver.json"
    stage_bundle_from_dump(
        FIXTURES_SQLSERVER, sqlserver_connection(), sqlserver_spec(), path, as_of=AS_OF
    )
    return AdapterConfig(location=str(path))


def stage_oracle_config(tmp_path: Path) -> AdapterConfig:
    path = tmp_path / "dbdirect-oracle.json"
    stage_bundle_from_dump(
        FIXTURES_ORACLE,
        oracle_connection(),
        oracle_spec(),
        path,
        as_of=AS_OF,
        normalization=oracle_normalization(),
    )
    return AdapterConfig(location=str(path))


def stage_sqlserver_entity_config(tmp_path: Path) -> AdapterConfig:
    """Entity-only staged bundle for the generic SourceAdapter contract, whose
    extract test asserts every record is one of the four canonical entity types
    (reference rows are exercised by the adapter-specific suites)."""
    path = tmp_path / "dbdirect-sqlserver-entities.json"
    stage_bundle_from_dump(
        FIXTURES_SQLSERVER,
        sqlserver_connection(),
        _spec("DBO", include_reference=False),
        path,
        as_of=AS_OF,
    )
    return AdapterConfig(location=str(path))


def stage_oracle_entity_config(tmp_path: Path) -> AdapterConfig:
    path = tmp_path / "dbdirect-oracle-entities.json"
    stage_bundle_from_dump(
        FIXTURES_ORACLE,
        oracle_connection(),
        _spec("COREBANK", include_reference=False),
        path,
        as_of=AS_OF,
        normalization=oracle_normalization(),
    )
    return AdapterConfig(location=str(path))


@pytest.fixture
def adapter() -> SourceAdapter:
    return DatabaseDirectAdapter()


@pytest.fixture
def mapping_config() -> MappingConfig:
    return dbdirect_mapping_config()


@pytest.fixture
def as_of() -> date:
    return AS_OF


@pytest.fixture
def broken_config(tmp_path: Path) -> AdapterConfig:
    return AdapterConfig(location=str(tmp_path / "does-not-exist.json"))
