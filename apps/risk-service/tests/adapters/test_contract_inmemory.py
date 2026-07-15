from __future__ import annotations

import pytest

from app.domain.ingestion.contracts import AdapterConfig, EntityMapping, MappingConfig
from tests.adapters.contract import AdapterContractSuite, as_of  # noqa: F401
from tests.adapters.inmemory import VALID_LOCATION, InMemoryAdapter

TABLES = {
    "GL": [
        {"Code": "1000", "Label": "Cash and balances", "Class": "ASSET"},
        {"Code": "2000", "Label": "Customer deposits", "Class": "LIABILITY"},
    ],
    "Customers": [
        {"CustomerId": "C-001", "CustomerName": "Kojo Mensah", "Segment": "RETAIL"},
        {"CustomerId": "C-002", "CustomerName": "Volta Agro Ltd", "Segment": "CORP"},
    ],
    "Products": [
        {"ProductCode": "LN.CORP.5Y", "ProductName": "5y corporate loan"},
    ],
    "Loans": [
        {
            "AccountRef": "LN-0001",
            "Type": "LOAN",
            "Ccy": "GHS",
            "Outstanding": "1500000.50",
            "Customer": "C-002",
            "Product": "LN.CORP.5Y",
            "Rate": "0.245",
            "RateKind": "F",
        },
    ],
}

ENTITY_TABLES = {
    "gl_account": "GL",
    "counterparty": "Customers",
    "product": "Products",
    "position": "Loans",
}

MAPPING = MappingConfig(
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
                "source_reference": "AccountRef",
                "position_type": "Type",
                "currency": "Ccy",
                "balance": "Outstanding",
                "counterparty_reference": "Customer",
                "product_code": "Product",
                "interest_rate": "Rate",
                "rate_type": "RateKind",
            },
        ),
    },
    enum_mappings={
        "counterparty_type": {"RETAIL": "RETAIL_INDIVIDUAL", "CORP": "CORPORATE"},
        "rate_type": {"F": "FIXED", "V": "FLOATING"},
    },
)


class TestInMemoryAdapterContract(AdapterContractSuite):
    @pytest.fixture
    def adapter(self) -> InMemoryAdapter:
        return InMemoryAdapter(TABLES)

    @pytest.fixture
    def valid_config(self) -> AdapterConfig:
        return AdapterConfig(location=VALID_LOCATION, options={"entity_tables": ENTITY_TABLES})

    @pytest.fixture
    def broken_config(self) -> AdapterConfig:
        return AdapterConfig(location="memory://missing")

    @pytest.fixture
    def mapping_config(self) -> MappingConfig:
        return MAPPING
