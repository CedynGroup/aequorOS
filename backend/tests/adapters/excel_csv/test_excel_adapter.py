from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.adapters.excel_csv.adapter import ExcelCsvAdapter
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.contracts import (
    AdapterConfig,
    EntityMapping,
    MappingConfig,
    ReferenceMapping,
)
from tests.adapters.contract import AdapterContractSuite, as_of  # noqa: F401
from tests.adapters.excel_csv import fixtures

POSITION_FIELDS: dict[str, str | list[str]] = {
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


class TestTableResolutionAndAliases:
    def test_aliases_resolve_case_insensitively_and_normalized(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_bank_realistic(tmp_path / "bank.xlsx")),
            # "03_gl_accounts" misses; "general ledger" normalizes onto the
            # "General_Ledger" sheet.
            options={"entity_tables": {"gl_account": ["03_gl_accounts", "general ledger"]}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["gl_account"])
        assert len(extraction.records) == 2
        assert all(
            r.source_locator.split("#")[1].startswith("General_Ledger") for r in extraction.records
        )
        assert extraction.unmatched_mappings == []

    def test_unmatched_mapping_reports_tables_found_and_expected(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_bank_realistic(tmp_path / "bank.xlsx")),
            options={"entity_tables": {"position": ["06_loans", "Loans"]}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        assert extraction.records == []
        assert {(t.name, t.row_count) for t in extraction.source_tables} == {
            ("General_Ledger", 2),
            ("Yield_Curves", 2),
        }
        (unmatched,) = extraction.unmatched_mappings
        assert unmatched.mapping == "position"
        assert unmatched.expected == ("06_loans", "Loans")

    def test_close_miss_suggests_the_present_sheet(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_bank_realistic(tmp_path / "bank.xlsx")),
            options={"entity_tables": {"gl_account": ["General Ledger Accounts"]}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["gl_account"])
        (unmatched,) = extraction.unmatched_mappings
        assert unmatched.suggestion == "General_Ledger"

    def test_multiple_position_sheets_extract_with_fallback_and_attribute_columns(
        self, tmp_path: Path
    ) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_position_variants(tmp_path / "positions.xlsx")),
            options={"entity_tables": {"position": ["Loans", "LC_and_Guarantees"]}},
        )
        mapping = MappingConfig(
            field_mappings={
                "position": EntityMapping(
                    source_table="Loans",
                    source_table_aliases=["LC_and_Guarantees"],
                    fields={
                        "source_reference": "AccountRef",
                        "position_type": "Type",
                        "currency": "Ccy",
                        "balance": ["Outstanding", "NotionalCcy"],
                        "interest_rate": "Rate",
                        "rate_type": "RateKind",
                    },
                    attribute_columns=["CCF"],
                )
            },
            enum_mappings={
                "position_type": {"LC": "LC_GUARANTEE"},
                "rate_type": {"F": "FIXED"},
            },
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        records = adapter.translate(extraction, mapping)
        assert not records.failures
        by_reference = {p.source_reference: p for p in records.positions}
        assert by_reference["LN-0001"].balance == Decimal("1000")
        assert by_reference["LN-0001"].attributes == {}
        obs = by_reference["OBS-0001"]
        assert obs.position_type == "LC_GUARANTEE"
        assert obs.balance == Decimal("500")  # fallback column NotionalCcy
        assert obs.attributes == {"CCF": "0.2"}

    def test_fx_hedge_and_swap_books_ingest_with_types_and_attributes(self, tmp_path: Path) -> None:
        """The starter template's hedge/swap carriage: canonical position types
        flow from the position_type column, dates through the trade/maturity
        fallbacks, and instrument specifics ride in attributes."""
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_hedge_and_swap_book(tmp_path / "hedges.xlsx")),
            options={"entity_tables": {"position": ["FX_Hedges", "Interest_Rate_Swaps"]}},
        )
        mapping = MappingConfig(
            field_mappings={
                "position": EntityMapping(
                    source_table="FX_Hedges",
                    source_table_aliases=["Interest_Rate_Swaps"],
                    fields={
                        "source_reference": "position_id",
                        "position_type": "position_type",
                        "currency": "currency",
                        "balance": ["balance_ccy", "notional_ccy"],
                        "notional": "notional_ccy",
                        "origination_date": ["origination_date", "issue_date", "trade_date"],
                        "contractual_maturity": [
                            "contractual_maturity",
                            "expiry_date",
                            "maturity_date",
                        ],
                        "interest_rate": "interest_rate",
                    },
                    attribute_columns=[
                        "hedge_id",
                        "instrument",
                        "currency_pair",
                        "buy_currency",
                        "sell_currency",
                        "contract_rate",
                        "mtm_ghs",
                        "prospective_r2",
                        "dollar_offset_ratio",
                        "swap_id",
                        "direction",
                        "notional_ghs",
                        "pay_rate_pct",
                        "receive_index",
                        "tenor_years",
                    ],
                )
            },
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        records = adapter.translate(extraction, mapping)
        assert not records.failures
        by_reference = {p.source_reference: p for p in records.positions}
        assert set(by_reference) == {"SBL-FXH-000001", "SBL-IRS-000001"}

        hedge = by_reference["SBL-FXH-000001"]
        assert hedge.position_type == "FX_HEDGE"
        assert hedge.currency == "USD"
        assert hedge.balance == Decimal("3000000")  # notional_ccy via fallback
        assert hedge.origination_date == date(2026, 1, 30)  # trade_date fallback
        assert hedge.contractual_maturity == date(2026, 7, 29)  # maturity_date fallback
        assert hedge.attributes["hedge_id"] == "FXH-USD-001"
        assert hedge.attributes["instrument"] == "FORWARD"
        assert hedge.attributes["sell_currency"] == "USD"
        assert hedge.attributes["contract_rate"] == "13.05"
        assert hedge.attributes["prospective_r2"] == "0.94"
        assert hedge.attributes["dollar_offset_ratio"] == "1.02"
        assert "swap_id" not in hedge.attributes

        swap = by_reference["SBL-IRS-000001"]
        assert swap.position_type == "INTEREST_RATE_SWAP"
        assert swap.currency == "GHS"
        assert swap.balance == Decimal("60000000")
        assert swap.interest_rate == Decimal("0.2475")
        assert swap.contractual_maturity == date(2028, 4, 28)
        assert swap.attributes["swap_id"] == "IRS-2026-001"
        assert swap.attributes["direction"] == "PAY_FIXED"
        assert swap.attributes["pay_rate_pct"] == "24.75"
        assert swap.attributes["receive_index"] == "91D_TBILL"
        assert swap.attributes["tenor_years"] == "2"  # Excel normalizes 2.0 -> 2
        assert "hedge_id" not in swap.attributes

    def test_legacy_single_table_option_still_works(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_well_formed(tmp_path / "wb.xlsx")),
            options={"entity_tables": {"position": "Loans"}},
        )
        extraction = adapter.extract(config, fixtures.AS_OF, ["position"])
        assert len(extraction.records) == 2


class TestReferenceDatasets:
    def test_reference_rows_extract_and_stringify(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_bank_realistic(tmp_path / "bank.xlsx")),
            options={
                "entity_tables": {},
                "reference_tables": {
                    "yield_curves": {
                        "tables": ["13_yield_curves", "Yield_Curves"],
                        "dataset_kind": "yield_curve",
                    }
                },
            },
        )
        mapping = MappingConfig(
            reference_mappings={
                "yield_curves": ReferenceMapping(
                    source_table="13_yield_curves",
                    source_table_aliases=["Yield_Curves"],
                    dataset_kind="yield_curve",
                )
            }
        )
        extraction = adapter.extract(config, fixtures.AS_OF, [])
        assert len(extraction.records) == 2
        assert all(record.entity_type == "reference" for record in extraction.records)

        records = adapter.translate(extraction, mapping)
        assert not records.failures
        assert records.record_count == 2
        assert records.reference_row_counts == {"yield_curve": 2}
        first, second = records.reference_rows
        assert (first.row_index, second.row_index) == (1, 2)
        assert first.payload == {
            "curve_name": "GHS_SOVEREIGN",
            "tenor_months": "3",
            "rate": "0.158",
            "quote_date": "2026-06-01",  # dates land ISO-formatted
        }

    def test_reference_field_selection_restricts_payload_columns(self, tmp_path: Path) -> None:
        adapter = ExcelCsvAdapter()
        config = AdapterConfig(
            location=str(fixtures.build_bank_realistic(tmp_path / "bank.xlsx")),
            options={
                "reference_tables": {
                    "yield_curves": {"tables": ["Yield_Curves"], "dataset_kind": "yield_curve"}
                }
            },
        )
        mapping = MappingConfig(
            reference_mappings={
                "yield_curves": ReferenceMapping(
                    source_table="Yield_Curves",
                    dataset_kind="yield_curve",
                    fields=["curve_name", "rate"],
                )
            }
        )
        records = adapter.translate(adapter.extract(config, fixtures.AS_OF, []), mapping)
        assert records.reference_rows[0].payload == {
            "curve_name": "GHS_SOVEREIGN",
            "rate": "0.158",
        }
