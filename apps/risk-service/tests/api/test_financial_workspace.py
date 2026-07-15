from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
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
    assert body["cash_flows"] == []
    assert body["obligations"] == []
    assert body["covenants"] == []
    assert body["source_rows"] == []
    assert body["record_source_links"] == []
    assert body["manual_edits"] == []
    assert body["validation_issues"] == []
    assert body["validation_summary"] == {"total": 0, "error": 0, "warning": 0, "info": 0}


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
    assert Decimal(str(body["cash_flows"][0]["amount"])) == Decimal("15000.0000")
    assert body["cash_flows"][0]["direction"] == "inflow"
    assert body["cash_flows"][0]["category"] == "customer deposit"
    assert body["obligations"][0]["facility_type"] == "revolving_credit"
    assert body["source_rows"][0]["raw_payload"] == {"balance": "250000.00"}
    assert body["record_source_links"][0]["record_table"] == "financial_balances"
    assert body["record_source_links"][0]["record_id"] == body["balances"][0]["id"]
    assert body["manual_edits"][0]["field_name"] == "account_name"
    assert body["manual_edits"][0]["record_id"] == body["accounts"][0]["id"]
    assert body["manual_edits"][0]["edited_by_display_name"] == "Demo User One"
    assert body["validation_issues"][0]["rule_id"] == "missing_currency"
    assert body["validation_issues"][0]["code"] == "missing_currency"
    assert body["validation_issues"][0]["issue_key"] == "missing_currency:test-seed"
    assert body["validation_issues"][0]["entity_type"] == "balance"
    assert body["validation_issues"][0]["field"] == "currency"
    assert body["validation_issues"][0]["record_id"] == body["balances"][0]["id"]
    assert body["validation_summary"] == {"total": 1, "error": 0, "warning": 1, "info": 0}


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
                    dedupe_key="test:institution:second",
                    name="Second Bank",
                    created_at=second_created,
                    updated_at=second_created,
                ),
                FinancialInstitution(
                    organization_id=ORG_1,
                    case_id=case.id,
                    dedupe_key="test:institution:first",
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
            dedupe_key="test:balance:precision",
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
            dedupe_key="test:obligation:precision",
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


def test_financial_validation_run_list_filters_and_workspace_summary(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    seed_financial_validation_records(case_id=case.id)

    run_response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-data/validate",
        headers=headers(),
    )
    error_response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-data/validation-issues?severity=error",
        headers=headers(),
    )
    account_response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-data/validation-issues?entity_type=account",
        headers=headers(),
    )
    workspace_response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-workspace",
        headers=headers(),
    )

    assert run_response.status_code == 200, run_response.text
    body = run_response.json()
    assert body["case_id"] == str(case.id)
    assert body["issue_count"] == body["summary"]["total"]
    assert body["summary"]["error"] >= 2
    assert body["summary"]["warning"] >= 1
    first_issue = body["issues"][0]
    assert {
        "severity",
        "code",
        "issue_key",
        "field_name",
        "entity_type",
        "entity_id",
        "field",
        "message",
    } <= set(first_issue)
    assert first_issue["field_name"] == first_issue["field"]

    assert error_response.status_code == 200, error_response.text
    assert {issue["severity"] for issue in error_response.json()} == {"error"}
    assert account_response.status_code == 200, account_response.text
    assert {issue["entity_type"] for issue in account_response.json()} == {"account"}

    assert workspace_response.status_code == 200, workspace_response.text
    workspace_body = workspace_response.json()
    assert workspace_body["validation_summary"] == body["summary"]
    assert len(workspace_body["validation_issues"]) == body["issue_count"]


def test_financial_validation_issue_filters_reject_invalid_values(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()

    invalid_severity = db_client.get(
        f"/api/v1/cases/{case.id}/financial-data/validation-issues?severity=critical",
        headers=headers(),
    )
    invalid_entity_type = db_client.get(
        f"/api/v1/cases/{case.id}/financial-data/validation-issues?entity_type=facility",
        headers=headers(),
    )

    assert invalid_severity.status_code == 422, invalid_severity.text
    assert invalid_entity_type.status_code == 422, invalid_entity_type.text


def test_financial_validation_rerun_replaces_stale_issues(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    seed_financial_validation_records(case_id=case.id)

    first = db_client.post(
        f"/api/v1/cases/{case.id}/financial-data/validate",
        headers=headers(),
    )
    second = db_client.post(
        f"/api/v1/cases/{case.id}/financial-data/validate",
        headers=headers(),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["issue_count"] == first.json()["issue_count"]


def test_financial_validation_enforces_tenant_isolation(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()
    seed_financial_validation_records(case_id=case.id)

    run_response = db_client.post(
        f"/api/v1/cases/{case.id}/financial-data/validate",
        headers=headers(ORG_2),
    )
    list_response = db_client.get(
        f"/api/v1/cases/{case.id}/financial-data/validation-issues",
        headers=headers(ORG_2),
    )

    assert run_response.status_code == 404, run_response.text
    assert list_response.status_code == 404, list_response.text


def test_financial_cash_flows_can_be_manually_created_and_corrected(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()

    create = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(),
        json={
            "cash_flow_date": "2026-04-15",
            "amount": "1000.25",
            "currency": "usd",
            "direction": "inflow",
            "category": "Customer Deposit",
            "metadata": {"entry": "manual"},
            "reason": "Manual statement entry",
        },
    )

    assert create.status_code == 200, create.text
    create_body = create.json()
    created = create_body["record"]
    assert create_body["validation"]["case_id"] == str(case.id)
    assert created["metadata"]["provenance"] == "manual"
    assert Decimal(str(created["amount"])) == Decimal("1000.2500")
    assert created["currency"] == "USD"
    assert created["category"] == "customer deposit"

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows/{created['id']}",
        headers=headers(),
        json={
            "amount": "1250.50",
            "direction": "outflow",
            "reason": "Reviewer correction",
        },
    )

    assert correction.status_code == 200, correction.text
    correction_body = correction.json()
    corrected = correction_body["record"]
    assert Decimal(str(corrected["amount"])) == Decimal("1250.5000")
    assert corrected["direction"] == "outflow"
    assert corrected["metadata"]["provenance"] == "corrected"
    assert correction_body["validation"]["issue_count"] == len(
        correction_body["validation"]["issues"]
    )

    workspace = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    assert body["cash_flows"][0]["id"] == created["id"]
    assert {(edit["field_name"], edit["reason"]) for edit in body["manual_edits"]} >= {
        ("amount", "Reviewer correction"),
        ("direction", "Reviewer correction"),
    }
    assert body["validation_issues"] == []
    with get_sessionmaker()() as session:
        create_edits = list(
            session.scalars(
                select(FinancialManualEditHistory).where(
                    FinancialManualEditHistory.record_id == UUID(created["id"]),
                    FinancialManualEditHistory.reason == "Manual statement entry",
                )
            )
        )
        assert {edit.field_name for edit in create_edits} >= {
            "amount",
            "cash_flow_date",
            "category",
            "currency",
            "direction",
            "metadata",
        }
        assert all(edit.edited_by == USER_1 for edit in create_edits)
        events = set(
            session.scalars(
                select(AuditEvent.event_type).where(AuditEvent.entity_id == UUID(created["id"]))
            )
        )
        assert events >= {"financial_record.created", "financial_record.corrected"}


def test_financial_cash_flow_validation_issues_are_recorded_and_resolved(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()

    create = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(),
        json={
            "amount": "1000",
            "direction": "inflow",
            "category": "loan repayment",
            "reason": "Manual statement entry",
        },
    )

    assert create.status_code == 200, create.text
    assert {issue["rule_id"] for issue in create.json()["validation"]["issues"]} == {
        "cash_flow_missing_currency",
        "cash_flow_missing_period_or_date",
    }
    cash_flow_id = create.json()["record"]["id"]
    workspace = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())
    assert {issue["rule_id"] for issue in workspace.json()["validation_issues"]} == {
        "cash_flow_missing_currency",
        "cash_flow_missing_period_or_date",
    }

    correction = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows/{cash_flow_id}",
        headers=headers(),
        json={
            "currency": "GHS",
            "cash_flow_date": "2026-04-15",
            "reason": "Confirm from statement",
        },
    )

    assert correction.status_code == 200, correction.text
    assert correction.json()["validation"]["issues"] == []
    workspace = db_client.get(f"/api/v1/cases/{case.id}/financial-workspace", headers=headers())
    assert workspace.json()["validation_issues"] == []


def test_financial_cash_flow_correction_conflicts_return_409(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    first = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(),
        json={
            "cash_flow_date": "2026-04-15",
            "amount": "1000",
            "currency": "GHS",
            "direction": "inflow",
            "category": "deposit",
            "reason": "First manual entry",
        },
    )
    second = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(),
        json={
            "cash_flow_date": "2026-04-15",
            "amount": "2000",
            "currency": "GHS",
            "direction": "inflow",
            "category": "deposit",
            "reason": "Second manual entry",
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    conflict = db_client.patch(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows/{second.json()['record']['id']}",
        headers=headers(),
        json={"amount": "1000", "reason": "Duplicate correction"},
    )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "http_error"


def test_financial_cash_flow_manual_routes_enforce_tenant_isolation(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()

    create = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(ORG_2),
        json={
            "amount": "1000",
            "direction": "inflow",
            "category": "deposit",
            "reason": "Cross-tenant attempt",
        },
    )

    assert create.status_code == 404, create.text


def test_financial_cash_flow_mutations_require_actor_and_non_empty_reason(
    db_client: TestClient,
) -> None:
    case = CaseFactory(db_client).create()
    payload = {
        "amount": "1000",
        "direction": "inflow",
        "category": "deposit",
        "reason": "   ",
    }

    blank_reason = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers=headers(),
        json=payload,
    )
    missing_actor = db_client.post(
        f"/api/v1/cases/{case.id}/financial-workspace/cash-flows",
        headers={"X-Org-Id": str(ORG_1)},
        json={**payload, "reason": "Manual entry"},
    )

    assert blank_reason.status_code == 422
    assert missing_actor.status_code == 422


def seed_financial_workspace(*, case_id: UUID) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        institution = FinancialInstitution(
            organization_id=ORG_1,
            case_id=case_id,
            dedupe_key="test:institution:seed",
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
            dedupe_key="test:account:seed",
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
            dedupe_key="test:period:seed",
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
            dedupe_key="test:balance:seed",
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
            dedupe_key="test:obligation:seed",
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
        cash_flow = FinancialCashFlow(
            organization_id=ORG_1,
            case_id=case_id,
            dedupe_key="test:cash-flow:seed",
            account_id=account.id,
            reporting_period_id=period.id,
            cash_flow_date=date(2026, 3, 15),
            amount=Decimal("15000.00"),
            currency="GHS",
            direction="inflow",
            category="customer deposit",
        )
        session.add_all([balance, cash_flow, obligation, source_row])
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
                    issue_key="missing_currency:test-seed",
                    field_name="currency",
                    severity="warning",
                    status="open",
                    rule_id="missing_currency",
                    message="Currency should be confirmed from source file.",
                    details={"entity_type": "balance", "field": "currency"},
                ),
            ]
        )
        session.commit()


def seed_financial_validation_records(*, case_id: UUID) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        account = FinancialAccount(
            organization_id=ORG_1,
            case_id=case_id,
            dedupe_key="test:validation:account",
            account_name="Validation Account",
            account_type=None,
            currency="GHS",
        )
        session.add(account)
        session.flush()
        session.add_all(
            [
                FinancialBalance(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="test:validation:balance",
                    account_id=account.id,
                    balance_type="cash",
                    amount=Decimal("100.00"),
                    currency="USD",
                ),
                FinancialObligation(
                    organization_id=ORG_1,
                    case_id=case_id,
                    dedupe_key="test:validation:obligation",
                    obligation_type="facility",
                    principal_amount=Decimal("10.00"),
                    outstanding_amount=Decimal("15.00"),
                    currency="GHS",
                ),
            ]
        )
        session.commit()
