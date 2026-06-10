from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DocumentExtraction,
    FinancialBalance,
    FinancialRecordSourceLink,
    FinancialSourceRow,
)
from app.schemas.financial_workspace_mapping import FinancialWorkspaceMapRequest
from app.services.financial_cash_flows import (
    cash_flow_review_rules,
    normalize_category,
    normalize_direction,
)
from app.services.financial_mapping.extraction import parse_extracted_rows
from app.services.financial_mapping.normalization import parse_decimal
from app.services.financial_mapping.service import map_financial_workspace
from app.services.financial_mapping.upserts import canonical_dedupe_key
from tests.services.factories import ServiceFactories


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


def test_map_financial_workspace_service_maps_aliases_and_is_idempotent(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    document = factories.documents.create_uploaded(case.id)
    extraction = DocumentExtraction(
        organization_id=factories.ctx.organization_id,
        document_id=document.id,
        extraction_type="financial_workspace",
        schema_version="1",
        status="completed",
        extracted_json={
            "rows": [
                {
                    "Counterparty": "Aequor Bank",
                    "Account": "Operating",
                    "Period": "2026-03-31",
                    "Amount": "42.25",
                    "Currency": "GHS",
                    "Ignored Column": "preserved",
                }
            ]
        },
        created_at=datetime.now(UTC),
    )
    db_session.add(extraction)
    db_session.commit()

    first = map_financial_workspace(
        db_session,
        factories.ctx,
        case.id,
        FinancialWorkspaceMapRequest(document_extraction_id=extraction.id),
    )
    second = map_financial_workspace(
        db_session,
        factories.ctx,
        case.id,
        FinancialWorkspaceMapRequest(document_extraction_id=extraction.id),
    )

    source_rows = list(db_session.scalars(select(FinancialSourceRow)))
    balances = list(db_session.scalars(select(FinancialBalance)))
    links = list(db_session.scalars(select(FinancialRecordSourceLink)))

    assert first.summary.source_row_count == 1
    assert first.created["source_rows"] == 1
    assert first.created["balances"] == 1
    assert second.created["source_rows"] == 0
    assert second.created["balances"] == 0
    assert len(source_rows) == 1
    assert source_rows[0].raw_payload["Ignored Column"] == "preserved"
    assert len(balances) == 1
    assert balances[0].amount == Decimal("42.2500")
    assert balances[0].currency == "GHS"
    assert {(link.record_table, link.field_name, link.source_field) for link in links} >= {
        ("financial_balances", "amount", "Amount"),
        ("financial_balances", "currency", "Currency"),
    }
