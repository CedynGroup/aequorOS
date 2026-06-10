from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import (
    DocumentExtraction,
    FinancialBalance,
    FinancialCashFlow,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialSourceRow,
)
from tests.api.factories import ApiFactories
from tests.api.helpers import ORG_1, ORG_2, headers


def test_financial_workspace_map_creates_records_traceability_and_is_idempotent(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={
            "rows": [
                {
                    "Bank": "Aequor Bank",
                    "Account Name": "Operating Account",
                    "As Of Date": "2026-03-31",
                    "Balance": "250,000.00",
                    "CCY": "GHS",
                    "Reviewer Note": "keep this unknown field",
                },
                {
                    "Lender": "Growth Lender",
                    "Account": "Revolver",
                    "Period Start": "2026-01-01",
                    "Period End": "2026-03-31",
                    "Committed": "1000000",
                    "Drawn": "125000",
                    "Currency": "USD",
                },
                {"Comment": "header row only"},
            ]
        },
    )

    first = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )
    second = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["summary"]["source_row_count"] == 3
    assert first_body["summary"]["mapped_source_row_count"] == 2
    assert first_body["summary"]["unmapped_source_row_count"] == 1
    assert first_body["created"]["source_rows"] == 3
    assert first_body["created"]["balances"] == 1
    assert first_body["created"]["obligations"] == 1
    assert first_body["unmapped_rows"][0]["row_index"] == 2
    assert second_body["created"]["source_rows"] == 0
    assert second_body["created"]["balances"] == 0
    assert second_body["created"]["obligations"] == 0
    assert second_body["created"]["record_source_links"] == 0
    assert second_body["reused"]["source_rows"] == 3

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        source_rows = list(
            session.scalars(
                select(FinancialSourceRow)
                .where(FinancialSourceRow.document_extraction_id == extraction_id)
                .order_by(FinancialSourceRow.row_index.asc())
            )
        )
        balances = list(session.scalars(select(FinancialBalance)))
        obligations = list(session.scalars(select(FinancialObligation)))
        links = list(session.scalars(select(FinancialRecordSourceLink)))

    assert len(source_rows) == 3
    assert source_rows[0].document_id == document.document_id
    assert source_rows[0].document_extraction_id == extraction_id
    assert source_rows[0].raw_payload["Reviewer Note"] == "keep this unknown field"
    assert len(balances) == 1
    assert balances[0].dedupe_key.startswith("balance:")
    assert balances[0].amount == Decimal("250000.0000")
    assert balances[0].currency == "GHS"
    assert len(obligations) == 1
    assert obligations[0].dedupe_key.startswith("obligation:")
    assert obligations[0].principal_amount == Decimal("1000000.0000")
    assert obligations[0].outstanding_amount == Decimal("125000.0000")
    assert {
        (link.record_table, link.field_name, link.source_field)
        for link in links
        if link.metadata_["document_extraction_id"] == str(extraction_id)
    } >= {
        ("financial_balances", "amount", "Balance"),
        ("financial_balances", "currency", "CCY"),
        ("financial_obligations", "principal_amount", "Committed"),
        ("financial_obligations", "outstanding_amount", "Drawn"),
    }
    unique_link_keys = {
        (
            link.record_table,
            link.record_id,
            link.source_row_id,
            link.field_name,
            link.source_field,
        )
        for link in links
    }
    assert len(links) == len(unique_link_keys)

    workspace = db_client.get(
        f"/api/v1/cases/{case.id}/financial-workspace",
        headers=headers(),
    )
    assert workspace.status_code == 200, workspace.text
    workspace_body = workspace.json()
    assert workspace_body["source_rows"][0]["document_extraction_id"] == str(extraction_id)
    assert {
        (link["field_name"], link["source_field"]) for link in workspace_body["record_source_links"]
    } >= {("amount", "Balance"), ("currency", "CCY")}


def test_financial_workspace_map_uses_latest_completed_extraction_for_document_id(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    old_extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Bank": "Old Bank", "Balance": "1"}]},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    new_extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={
            "tables": [
                {
                    "name": "Balances",
                    "rows": [{"Institution": "New Bank", "Account": "Cash", "Amount": "2"}],
                }
            ]
        },
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_id": str(document.document_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_extraction_id"] == str(new_extraction_id)
    assert body["document_extraction_id"] != str(old_extraction_id)
    assert body["summary"]["source_row_count"] == 1
    assert body["summary"]["mapped_source_row_count"] == 1


def test_financial_workspace_map_creates_cash_flows_with_traceability(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={
            "rows": [
                {
                    "Account": "Operating Account",
                    "Cash Flow Date": "2026-04-15",
                    "Amount": "15,000",
                    "Direction": "Inflow",
                    "Category": "Customer Deposit",
                    "Currency": "GHS",
                }
            ]
        },
    )

    first = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )
    second = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["created"]["cash_flows"] == 1
    assert first_body["created"]["balances"] == 0
    assert second_body["created"]["cash_flows"] == 0
    assert second_body["reused"]["cash_flows"] == 1

    with get_sessionmaker()() as session:
        cash_flow = session.scalar(select(FinancialCashFlow))
        links = list(session.scalars(select(FinancialRecordSourceLink)))

    assert cash_flow is not None
    assert cash_flow.amount == Decimal("15000.0000")
    assert cash_flow.currency == "GHS"
    assert cash_flow.direction == "inflow"
    assert cash_flow.category == "customer deposit"
    assert cash_flow.cash_flow_date.isoformat() == "2026-04-15"
    assert {
        (link.record_table, link.field_name, link.source_field)
        for link in links
        if link.record_table == "financial_cash_flows"
    } >= {
        ("financial_cash_flows", "amount", "Amount"),
        ("financial_cash_flows", "currency", "Currency"),
        ("financial_cash_flows", "direction", "Direction"),
        ("financial_cash_flows", "category", "Category"),
        ("financial_cash_flows", "cash_flow_date", "Cash Flow Date"),
    }

    workspace = db_client.get(
        f"/api/v1/cases/{case.id}/financial-workspace",
        headers=headers(),
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["cash_flows"][0]["id"] == str(cash_flow.id)


def test_financial_workspace_map_rejects_invalid_and_non_completed_extractions(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    pending_extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Bank": "Aequor Bank"}]},
        status="pending",
    )

    invalid = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(uuid4())},
    )
    non_completed = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(pending_extraction_id)},
    )
    invalid_payload = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={
            "document_id": str(document.document_id),
            "document_extraction_id": str(pending_extraction_id),
        },
    )
    missing_payload = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={},
    )

    assert invalid.status_code == 404, invalid.text
    assert non_completed.status_code == 409, non_completed.text
    assert invalid_payload.status_code == 422, invalid_payload.text
    assert missing_payload.status_code == 422, missing_payload.text


def test_financial_workspace_map_rejects_document_without_completed_extraction(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Bank": "Aequor Bank", "Balance": "10"}]},
        status="pending",
    )

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_id": str(document.document_id)},
    )

    assert response.status_code == 404, response.text


def test_financial_workspace_map_enforces_tenant_and_case_isolation(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    other_case = api_factories.cases.create()
    other_tenant_case = api_factories.cases.create(org_id=ORG_2)
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Bank": "Aequor Bank", "Balance": "10"}]},
    )

    same_tenant_wrong_case = db_client.post(
        f"/api/v1/cases/{other_case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )
    response = db_client.post(
        f"/api/v1/cases/{other_tenant_case.id}/financial-workspace/map",
        headers=headers(ORG_2),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert same_tenant_wrong_case.status_code == 404, same_tenant_wrong_case.text
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "extracted_json",
    [
        {"not_rows": []},
        {"rows": {"Bank": "Aequor Bank"}},
        {"tables": {"rows": []}},
    ],
)
def test_financial_workspace_map_rejects_malformed_extraction_payloads(
    db_client: TestClient,
    api_factories: ApiFactories,
    extracted_json: dict,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(document_id=document.document_id, extracted_json=extracted_json)

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert response.status_code == 400, response.text


def test_financial_workspace_map_preserves_invalid_amount_rows_as_unmapped(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Balance": "not an amount"}]},
    )

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["source_row_count"] == 1
    assert body["summary"]["mapped_source_row_count"] == 0
    assert body["summary"]["unmapped_source_row_count"] == 1
    assert body["created"]["balances"] == 0
    with get_sessionmaker()() as session:
        assert (
            session.scalar(
                select(FinancialSourceRow).where(
                    FinancialSourceRow.document_extraction_id == extraction_id
                )
            )
            is not None
        )
        assert session.scalar(select(FinancialBalance)) is None


def test_financial_workspace_map_preserves_invalid_cash_flow_rows_as_unmapped(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={
            "rows": [
                {
                    "Cash Flow Amount": "not an amount",
                    "Direction": "inflow",
                    "Category": "deposit",
                }
            ]
        },
    )

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["mapped_source_row_count"] == 0
    assert body["created"]["cash_flows"] == 0
    with get_sessionmaker()() as session:
        assert session.scalar(select(FinancialCashFlow)) is None


def test_financial_workspace_map_does_not_reuse_stale_cross_case_source_link(
    db_client: TestClient,
    api_factories: ApiFactories,
) -> None:
    case = api_factories.cases.create()
    other_case = api_factories.cases.create()
    document = api_factories.documents.create_uploaded(case_id=case.id)
    extraction_id = seed_extraction(
        document_id=document.document_id,
        extracted_json={"rows": [{"Bank": "Aequor Bank", "Balance": "100", "Currency": "GHS"}]},
    )

    with get_sessionmaker()() as session:
        source_row = FinancialSourceRow(
            organization_id=ORG_1,
            case_id=case.id,
            document_id=document.document_id,
            document_extraction_id=extraction_id,
            row_index=0,
            locator={"shape": "rows", "row_index": 0},
            raw_payload={"Bank": "Aequor Bank", "Balance": "100", "Currency": "GHS"},
        )
        cross_case_balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=other_case.id,
            dedupe_key="test:balance:cross-case-stale-link",
            balance_type="balance",
            amount=Decimal("100.0000"),
            currency="GHS",
        )
        session.add_all([source_row, cross_case_balance])
        session.flush()
        session.add(
            FinancialRecordSourceLink(
                organization_id=ORG_1,
                case_id=case.id,
                record_table="financial_balances",
                record_id=cross_case_balance.id,
                source_row_id=source_row.id,
                field_name="amount",
                source_field="Balance",
                metadata_={"document_extraction_id": str(extraction_id), "kind": "stale"},
            )
        )
        session.commit()

    response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/map",
        headers=headers(),
        json={"document_extraction_id": str(extraction_id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"]["balances"] == 1
    with get_sessionmaker()() as session:
        balances = list(
            session.scalars(
                select(FinancialBalance).where(
                    FinancialBalance.organization_id == ORG_1,
                    FinancialBalance.amount == Decimal("100.0000"),
                )
            )
        )
    assert {balance.case_id for balance in balances} == {case.id, other_case.id}


def seed_extraction(
    *,
    document_id: UUID,
    extracted_json: dict,
    status: str = "completed",
    created_at: datetime | None = None,
) -> UUID:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        extraction = DocumentExtraction(
            organization_id=ORG_1,
            document_id=document_id,
            extraction_type="financial_workspace",
            schema_version="1",
            status=status,
            extracted_json=extracted_json,
            created_at=created_at or datetime.now(UTC) + timedelta(microseconds=1),
        )
        session.add(extraction)
        session.commit()
        return extraction.id
