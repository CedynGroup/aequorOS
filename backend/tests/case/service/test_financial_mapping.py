"""Tests for test_financial_mapping."""

from __future__ import annotations

from decimal import Decimal

from app.case.service.financial_cash_flows import (
    cash_flow_review_rules,
    normalize_category,
    normalize_direction,
)
from app.case.service.financial_mapping.extraction import parse_extracted_rows
from app.case.service.financial_mapping.normalization import parse_decimal
from app.case.service.financial_mapping.upserts import canonical_dedupe_key


def test_parse_extracted_rows_flattens_supported_shapes() -> None:
    rows = parse_extracted_rows(
        {
            "tables": [
                {"name": "Balances", "page": 2, "rows": [{"Bank": "A"}, {"Bank": "B"}]},
                {"name": "Facilities", "rows": ["total row"]},
            ]
        }
    )

    assert [row.index for row in rows] == [0, 1, 2]
    assert rows[0].payload == {"Bank": "A"}
    assert rows[0].locator == {
        "shape": "tables",
        "table_index": 0,
        "row_index": 0,
        "name": "Balances",
        "page": 2,
    }
    assert rows[2].payload == {"value": "total row"}
    assert rows[2].locator == {
        "shape": "tables",
        "table_index": 1,
        "row_index": 0,
        "name": "Facilities",
    }


def test_parse_decimal_accepts_common_financial_formats() -> None:
    assert parse_decimal("1,234.50") == Decimal("1234.50")
    assert parse_decimal("$2,500") == Decimal("2500")
    assert parse_decimal("(75.25)") == Decimal("-75.25")
    assert parse_decimal(10) == Decimal("10")
    assert parse_decimal("not a number") is None


def test_canonical_dedupe_key_fits_database_column() -> None:
    key = canonical_dedupe_key(
        "reporting_period",
        ["as_of", None, None, "2026-03-31", "2026-03-31"],
    )

    assert key.startswith("reporting_period:")
    assert len(key) <= 96


def test_cash_flow_normalization_and_review_rules_are_declarative() -> None:
    assert normalize_direction("Inflow") == "inflow"
    assert normalize_direction("debit") == "outflow"
    assert normalize_direction("sideways") is None
    assert normalize_category(" Customer Deposit ") == "customer deposit"

    rules = cash_flow_review_rules(
        currency=None,
        cash_flow_date=None,
        reporting_period_id=None,
    )

    assert {rule.rule_id for rule in rules} == {
        "cash_flow_missing_currency",
        "cash_flow_missing_period_or_date",
    }
