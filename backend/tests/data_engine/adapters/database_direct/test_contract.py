"""SourceAdapter contract conformance for the database-direct adapter.

The generic :class:`AdapterContractSuite` is run twice — once over a staged
bundle pulled from the synthetic SQL Server dump, once over the Oracle dump —
proving one config-driven adapter serves both backends. Adapter-specific
guarantees (registry wiring, canonical field fidelity, reference capture) are
pinned alongside.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.data_engine.adapters.database_direct import DatabaseDirectAdapter
from app.data_engine.adapters.database_direct.adapter import SUPPORTED_BACKENDS
from app.data_engine.domain.ingestion.adapter import get_adapter_class
from app.data_engine.domain.ingestion.contracts import (
    ENTITY_TYPES,
    AdapterConfig,
    MappingConfig,
    RawRecord,
)
from tests.data_engine.adapters.contract import AdapterContractSuite

from .conftest import (
    stage_oracle_config,
    stage_oracle_entity_config,
    stage_sqlserver_config,
    stage_sqlserver_entity_config,
)


class TestSqlServerContract(AdapterContractSuite):
    """The generic SourceAdapter conformance suite, over SQL Server fixtures."""

    @pytest.fixture
    def valid_config(self, tmp_path: Path) -> AdapterConfig:
        return stage_sqlserver_entity_config(tmp_path)


class TestOracleContract(AdapterContractSuite):
    """The generic SourceAdapter conformance suite, over Oracle fixtures."""

    @pytest.fixture
    def valid_config(self, tmp_path: Path) -> AdapterConfig:
        return stage_oracle_entity_config(tmp_path)


class TestRegistration:
    def test_registered_under_db_direct(self) -> None:
        assert get_adapter_class("DB_DIRECT") is DatabaseDirectAdapter
        identity = DatabaseDirectAdapter().identify()
        assert identity.source_system == "DB_DIRECT"
        assert identity.name == "database_direct"

    def test_four_backends_advertised(self) -> None:
        assert set(SUPPORTED_BACKENDS) == {"oracle", "sqlserver", "jdbc", "odbc"}


class TestCanonicalFidelity:
    """Both backends land identical canonical values from divergent source forms."""

    @pytest.fixture(params=["sqlserver", "oracle"])
    def config(self, request: pytest.FixtureRequest, tmp_path: Path) -> AdapterConfig:
        builder = stage_sqlserver_config if request.param == "sqlserver" else stage_oracle_config
        return builder(tmp_path)

    def test_enum_and_locale_produce_canonical_positions(
        self, config: AdapterConfig, mapping_config: MappingConfig, as_of: date
    ) -> None:
        adapter = DatabaseDirectAdapter()
        canonical = adapter.translate(
            adapter.extract(config, as_of, list(ENTITY_TYPES)), mapping_config
        )
        by_ref = {p.source_reference: p for p in canonical.positions}
        assert set(by_ref) == {"P900001", "P900002", "P900003"}
        loan = by_ref["P900001"]
        # enum-mapped source codes -> canonical enums; European/plain numbers ->
        # the same Decimal; packed/ISO dates -> the same date.
        assert loan.position_type == "LOAN"
        assert loan.rate_type == "FLOATING"
        assert str(loan.balance) == "1200000.00"
        assert str(loan.interest_rate) == "0.285"
        assert loan.contractual_maturity is not None
        assert loan.contractual_maturity.isoformat() == "2030-01-15"
        # attribute_columns carried through to the attributes payload.
        assert loan.attributes.get("ECL_PROVISION") == "18000.00"

    def test_product_regulatory_category_derived(
        self, config: AdapterConfig, mapping_config: MappingConfig, as_of: date
    ) -> None:
        adapter = DatabaseDirectAdapter()
        canonical = adapter.translate(
            adapter.extract(config, as_of, list(ENTITY_TYPES)), mapping_config
        )
        by_code = {p.product_code: p for p in canonical.products}
        assert by_code["LN.CORP.5Y"].regulatory_category == "CORPORATE_LOAN_UNRATED_100RW"

    def test_unmapped_reference_columns_fail_without_discarding_the_raw_record(
        self, config: AdapterConfig, mapping_config: MappingConfig, as_of: date
    ) -> None:
        adapter = DatabaseDirectAdapter()
        canonical = adapter.translate(
            adapter.extract(config, as_of, list(ENTITY_TYPES)), mapping_config
        )
        failures = [item for item in canonical.failures if item.entity_type == "reference"]
        assert len(failures) == 3
        assert all(item.error_code == "invalid_reference_row" for item in failures)
        assert {item.raw_record["CCY_PAIR"] for item in failures} == {"USDGHS", "EURGHS", "GBPGHS"}
        assert not canonical.reference_rows

    def test_a_reference_view_with_registered_column_names_is_accepted(
        self, config: AdapterConfig, mapping_config: MappingConfig, as_of: date
    ) -> None:
        adapter = DatabaseDirectAdapter()
        extraction = adapter.extract(config, as_of, ["gl_account"])
        extraction.records = [
            RawRecord(
                entity_type="reference",
                source_table="FX_RATES",
                source_locator="FX_RATES#R1",
                dataset_kind="fx_rates_current",
                data={"pair": "USDGHS", "rate": "12.45", "quote_date": "2026-06-30"},
            )
        ]
        canonical = adapter.translate(extraction, mapping_config)
        assert not canonical.failures
        assert canonical.reference_row_counts == {"fx_rates_current": 1}
        assert canonical.reference_rows[0].payload["pair"] == "USDGHS"


class TestEntityTypeFiltering:
    def test_extract_honors_requested_entity_types(self, tmp_path: Path, as_of: date) -> None:
        adapter = DatabaseDirectAdapter()
        config = stage_sqlserver_config(tmp_path)
        result = adapter.extract(config, as_of, ["gl_account"])
        kinds = {record.entity_type for record in result.records}
        # gl_account entities plus reference rows (always kept); no positions.
        assert "gl_account" in kinds
        assert "position" not in kinds
        assert "reference" in kinds
        # every configured table still appears in the source breakdown.
        assert len(result.source_tables) == 5  # noqa: PLR2004
