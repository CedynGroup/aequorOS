"""Tests for test_financial_mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.case.schemas.financial_workspace_mapping import FinancialWorkspaceMapRequest
from app.case.service.financial_mapping.service import map_financial_workspace
from app.models import (
    DocumentExtraction,
    FinancialBalance,
    FinancialRecordSourceLink,
    FinancialSourceRow,
)
from tests.case.factories import ServiceFactories


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
