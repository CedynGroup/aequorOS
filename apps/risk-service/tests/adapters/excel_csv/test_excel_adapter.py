from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.excel_csv.adapter import ExcelCsvAdapter
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.contracts import AdapterConfig, EntityMapping, MappingConfig
from tests.adapters.contract import AdapterContractSuite, as_of  # noqa: F401
from tests.adapters.excel_csv import fixtures

POSITION_FIELDS = {
    "source_reference": "AccountRef",
    "position_type": "Type",
    "currency": "Ccy",
    "balance": "Outstanding",
    "interest_rate": "Rate",
    "rate_type": "RateKind",
    "contractual_maturity": "Maturity",
}

FULL_MAPPING = MappingConfig(
    field_mappings={
        "gl_account": EntityMapping(
            source_table="GL",
            fields={
                "source_reference": "Code",
                "account_code": "Code",
                "name": "Label",
                "account_class": "Class",
            },
        ),
        "counterparty": EntityMapping(
            source_table="Customers",
            fields={
                "source_reference": "CustomerId",
                "name": "CustomerName",
                "counterparty_type": "Segment",
                "country_code": "Country",
            },
        ),
        "product": EntityMapping(
            source_table="Products",
            fields={
                "source_reference": "ProductCode",
                "product_code": "ProductCode",
                "name": "ProductName",
            },
        ),
        "position": EntityMapping(
            source_table="Loans",
            fields={
                **POSITION_FIELDS,
                "counterparty_reference": "Customer",
                "product_code": "Product",
            },
        ),
    },
    enum_mappings={
        "counterparty_type": {"RETAIL": "RETAIL_INDIVIDUAL", "CORP": "CORPORATE"},
        "rate_type": {"F": "FIXED", "V": "FLOATING", "FLOAT": "FLOATING"},
    },
    product_mappings={
        "LN.CORP.5Y": "CORPORATE_LOAN_UNRATED_100RW",
        "DP.CURRENT": "RETAIL_DEPOSIT_STABLE",
    },
)

ENTITY_TABLES = {
    "gl_account": "GL",
    "counterparty": "Customers",
    "product": "Products",
    "position": "Loans",
}

POSITION_ONLY_MAPPING = MappingConfig(
    field_mappings={"position": EntityMapping(source_table="Loans", fields=POSITION_FIELDS)},
    enum_mappings={"rate_type": {"F": "FIXED", "V": "FLOATING", "FLOAT": "FLOATING"}},
)


class TestExcelCsvAdapterContract(AdapterContractSuite):
    @pytest.fixture
    def adapter(self) -> ExcelCsvAdapter:
        return ExcelCsvAdapter()

    @pytest.fixture
    def valid_config(self, tmp_path: Path) -> AdapterConfig:
        workbook = fixtures.build_well_formed(tmp_path / "well_formed.xlsx")
        return AdapterConfig(location=str(workbook), options={"entity_tables": ENTITY_TABLES})

    @pytest.fixture
    def broken_config(self, tmp_path: Path) -> AdapterConfig:
        return AdapterConfig(location=str(tmp_path / "does_not_exist.xlsx"))

    @pytest.fixture
    def mapping_config(self) -> MappingConfig:
        return FULL_MAPPING


class TestRegistration:
    def test_excel_adapter_is_registered(self) -> None:
        assert get_adapter_class("EXCEL_CSV") is ExcelCsvAdapter


class TestWellFormedTranslation:
    def test_full_workbook_translates_completely(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_well_formed(tmp_path / "wb.xlsx")),
            options={"entity_tables": ENTITY_TABLES},
        )
        extraction = adapter.extract(
            config, fixtures.AS_OF, ["gl_account", "counterparty", "product", "position"]
        )
        records = adapter.translate(extraction, FULL_MAPPING)

        assert not records.failures
        assert len(records.gl_accounts) == 2
        assert len(records.counterparties) == 2
        assert len(records.products) == 2
        assert len(records.positions) == 2

        corporate = next(c for c in records.counterparties if c.source_reference == "C-002")
        assert corporate.counterparty_type == "CORPORATE"

        loan = next(p for p in records.positions if p.source_reference == "LN-0001")
        assert loan.balance == Decimal("1500000.5")
        assert loan.rate_type == "FIXED"
        assert loan.contractual_maturity == date(2031, 3, 15)
        assert loan.counterparty_reference == "C-002"

        product = next(p for p in records.products if p.product_code == "LN.CORP.5Y")
        assert product.regulatory_category == "CORPORATE_LOAN_UNRATED_100RW"


class TestDirtyPatterns:
    def test_merged_title_banner_does_not_hide_the_table(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_merged_headers(tmp_path / "merged.xlsx")),
            options={"entity_tables": {"position": "Loans"}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        assert len(extraction.records) == 2
        assert extraction.records[0].source_locator.endswith("#Loans!R4")

    def test_stacked_tables_are_separately_routable(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_multiple_tables_per_sheet(tmp_path / "stacked.xlsx")),
            options={"entity_tables": {"product": "Data#1", "position": "Data#2"}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["product", "position"])
        by_type = {record.entity_type: record for record in extraction.records}
        assert by_type["product"].data["ProductCode"] == "LN.CORP.5Y"
        assert by_type["position"].data["AccountRef"] == "LN-0009"

    def test_dirty_cells_parse_and_tbc_rows_fail_loudly(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_dirty_cells(tmp_path / "dirty.xlsx")),
            options={"entity_tables": {"position": "Loans"}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        records = adapter.translate(extraction, POSITION_ONLY_MAPPING)

        # Three notations of the same economics all normalize identically.
        assert len(records.positions) == 3
        assert {p.interest_rate for p in records.positions} == {Decimal("0.245")}
        assert {p.contractual_maturity for p in records.positions} == {date(2031, 3, 15)}
        assert next(
            p.balance for p in records.positions if p.source_reference == "LN-0001"
        ) == Decimal("1500000.50")
        assert next(
            p.balance for p in records.positions if p.source_reference == "LN-0002"
        ) == Decimal("-2500.00")

        # "TBC" is a recognized no-value placeholder, so the row fails on the
        # missing required balance — raw record preserved, never silently dropped.
        (failure,) = records.failures
        assert failure.error_code == "invalid_record"
        assert failure.raw_record["Outstanding"] == "TBC"
        assert "balance" in failure.error_message

    def test_csv_files_are_first_class(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_positions_csv(tmp_path / "positions.csv")),
            options={"entity_tables": {"position": "positions"}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        records = adapter.translate(extraction, POSITION_ONLY_MAPPING)
        assert not records.failures
        assert [p.balance for p in records.positions] == [
            Decimal("1500000.50"),
            Decimal("250000"),
        ]
        assert records.positions[0].interest_rate == Decimal("0.245")


class TestUnreadableSources:
    def test_missing_file_is_a_connection_failure_not_a_crash(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        status = adapter.validate_connection(AdapterConfig(location=str(tmp_path / "nope.xlsx")))
        assert not status.ok
        assert "does not exist" in status.detail

    def test_legacy_xls_gets_an_actionable_message(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        legacy = tmp_path / "old.xls"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0old binary format")
        status = adapter.validate_connection(AdapterConfig(location=str(legacy)))
        assert not status.ok
        assert "save the file as .xlsx" in status.detail
