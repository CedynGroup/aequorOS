from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import (
    FinancialAccount,
    FinancialBalance,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
    FinancialSourceRow,
    FinancialValidationIssue,
)
from tests.api.factories import CaseFactory
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers


def test_financial_workspace_returns_empty_groups_for_valid_case(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    response = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case_id"] == str(case.id)
    assert body["organization_id"] == str(ORG_1)
    assert body["institutions"] == []
    assert body["accounts"] == []
    assert body["reporting_periods"] == []
    assert body["balances"] == []
    assert body["obligations"] == []
    assert body["source_rows"] == []
    assert body["record_source_links"] == []
    assert body["manual_edits"] == []
    assert body["validation_issues"] == []


def test_financial_workspace_returns_grouped_records(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    seed_financial_workspace(case_id=case.id)

    response = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institutions"][0]["name"] == "Aequor Bank"
    assert body["institutions"][0]["metadata"] == {"country": "GH"}
    assert body["accounts"][0]["account_name"] == "Operating Account"
    assert body["reporting_periods"][0]["label"] == "Q1 2026"
    assert Decimal(str(body["balances"][0]["amount"])) == Decimal("250000.0000")
    assert body["obligations"][0]["facility_type"] == "revolving_credit"
    assert body["source_rows"][0]["raw_payload"] == {"balance": "250000.00"}
    assert body["record_source_links"][0]["record_table"] == "financial_balances"
    assert body["record_source_links"][0]["record_id"] == body["balances"][0]["id"]
    assert body["manual_edits"][0]["field_name"] == "account_name"
    assert body["manual_edits"][0]["record_id"] == body["accounts"][0]["id"]
    assert body["validation_issues"][0]["rule_id"] == "missing_currency"
    assert body["validation_issues"][0]["record_id"] == body["balances"][0]["id"]


def test_financial_workspace_orders_groups_deterministically(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    first_created = datetime(2026, 1, 1, tzinfo=UTC)
    second_created = datetime(2026, 1, 2, tzinfo=UTC)

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        session.add_all(
            [
                FinancialInstitution(
                    organization_id=ORG_1,
                    case_id=case.id,
                    name="Second Bank",
                    created_at=second_created,
                    updated_at=second_created,
                ),
                FinancialInstitution(
                    organization_id=ORG_1,
                    case_id=case.id,
                    name="First Bank",
                    created_at=first_created,
                    updated_at=first_created,
                ),
            ]
        )
        session.commit()

    response = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())

    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["institutions"]] == [
        "First Bank",
        "Second Bank",
    ]


def test_financial_workspace_serializes_precision_dates_and_nulls(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=case.id,
            account_id=None,
            reporting_period_id=None,
            balance_type="cash",
            amount=Decimal("123.4567"),
            currency=None,
            as_of_date=date(2026, 6, 1),
        )
        obligation = FinancialObligation(
            organization_id=ORG_1,
            case_id=case.id,
            obligation_type="lease",
            interest_rate=Decimal("0.123456"),
            maturity_date=None,
        )
        session.add_all([balance, obligation])
        session.commit()

    response = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(str(body["balances"][0]["amount"])) == Decimal("123.4567")
    assert body["balances"][0]["as_of_date"] == "2026-06-01"
    assert body["balances"][0]["account_id"] is None
    assert body["balances"][0]["reporting_period_id"] is None
    assert Decimal(str(body["obligations"][0]["interest_rate"])) == Decimal("0.123456")
    assert body["obligations"][0]["maturity_date"] is None


def test_financial_workspace_uses_case_tenant_isolation(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-workspace",
        headers=headers(ORG_2),
    )

    assert response.status_code == 404, response.text


def test_financial_workspace_rejects_invalid_tenant_header(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-workspace",
        headers={"X-Org-Id": "not-a-uuid", "X-User-Id": str(USER_1)},
    )

    assert response.status_code == 401, response.text


def seed_financial_workspace(*, case_id: UUID) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        institution = FinancialInstitution(
            organization_id=ORG_1,
            case_id=case_id,
            name="Aequor Bank",
            institution_type="bank",
            reference_code="BANK-GH-001",
            metadata_={"country": "GH"},
        )
        session.add(institution)
        session.flush()

        account = FinancialAccount(
            organization_id=ORG_1,
            case_id=case_id,
            institution_id=institution.id,
            account_number="123456789",
            account_name="Operating Account",
            account_type="deposit",
            currency="GHS",
            status="active",
        )
        period = FinancialReportingPeriod(
            organization_id=ORG_1,
            case_id=case_id,
            period_type="quarter",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            as_of_date=date(2026, 3, 31),
            label="Q1 2026",
        )
        session.add_all([account, period])
        session.flush()

        balance = FinancialBalance(
            organization_id=ORG_1,
            case_id=case_id,
            account_id=account.id,
            reporting_period_id=period.id,
            balance_type="cash",
            amount=Decimal("250000.00"),
            currency="GHS",
            as_of_date=date(2026, 3, 31),
        )
        obligation = FinancialObligation(
            organization_id=ORG_1,
            case_id=case_id,
            institution_id=institution.id,
            account_id=account.id,
            reporting_period_id=period.id,
            obligation_type="credit_facility",
            facility_type="revolving_credit",
            principal_amount=Decimal("1000000.00"),
            outstanding_amount=Decimal("250000.00"),
            currency="GHS",
            start_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            interest_rate=Decimal("0.125000"),
            status="active",
            details={"secured": False},
        )
        source_row = FinancialSourceRow(
            organization_id=ORG_1,
            case_id=case_id,
            row_index=1,
            locator={"sheet": "Balances", "row": 2},
            raw_payload={"balance": "250000.00"},
        )
        session.add_all([balance, obligation, source_row])
        session.flush()

        session.add_all(
            [
                FinancialRecordSourceLink(
                    organization_id=ORG_1,
                    case_id=case_id,
                    record_table="financial_balances",
                    record_id=balance.id,
                    source_row_id=source_row.id,
                    confidence=Decimal("0.9500"),
                    metadata_={"method": "seed"},
                ),
                FinancialManualEditHistory(
                    organization_id=ORG_1,
                    case_id=case_id,
                    record_table="financial_accounts",
                    record_id=account.id,
                    field_name="account_name",
                    previous_value="Ops",
                    new_value="Operating Account",
                    edited_by=USER_1,
                    reason="Normalize account label",
                ),
                FinancialValidationIssue(
                    organization_id=ORG_1,
                    case_id=case_id,
                    record_table="financial_balances",
                    record_id=balance.id,
                    severity="medium",
                    status="open",
                    rule_id="missing_currency",
                    message="Currency should be confirmed from source file.",
                    details={"field": "currency"},
                ),
            ]
        )
        session.commit()
