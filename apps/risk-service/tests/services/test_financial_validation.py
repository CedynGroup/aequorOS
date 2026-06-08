from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FinancialValidationIssue
from app.services.financial_validation import validate_financial_data
from app.services.financial_validation_rules import (
    FACILITY_AVAILABLE_AMOUNT_MISMATCH,
    FinancialValidationDataset,
    evaluate_financial_validation,
)
from tests.services.factories import ServiceFactories


def test_validate_financial_data_stores_required_consistency_and_traceability_issues(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    financial = factories.financial

    financial.institution(case.id, name=" ", dedupe_key="test:institution:blank")
    financial.account(
        case.id,
        account_name=" ",
        account_type=None,
        currency=None,
        dedupe_key="test:account:missing",
    )
    mismatch_account = financial.account(
        case.id,
        account_name="Operating",
        account_type="deposit",
        currency="GHS",
        dedupe_key="test:account:mismatch",
    )
    multi_currency_account = financial.account(
        case.id,
        account_name="Multi",
        account_type="deposit",
        currency="GHS",
        metadata={"multi_currency": True},
        dedupe_key="test:account:multi",
    )
    financial.reporting_period(
        case.id,
        period_type="custom",
        start_date=date(2026, 3, 31),
        end_date=date(2026, 1, 1),
        dedupe_key="test:period:bad-order",
    )
    financial.reporting_period(case.id, period_type="custom", dedupe_key="test:period:missing")

    balance = financial.balance(
        case.id,
        account=mismatch_account,
        balance_type="cash",
        amount=Decimal("100.00"),
        currency="USD",
        dedupe_key="test:balance:mismatch",
    )
    multi_currency_balance = financial.balance(
        case.id,
        account=multi_currency_account,
        balance_type="cash",
        amount=Decimal("100.00"),
        currency="USD",
        dedupe_key="test:balance:multi",
    )
    financial.balance(
        case.id,
        balance_type="cash",
        amount=Decimal("100.00"),
        currency=None,
        dedupe_key="test:balance:missing-currency",
    )
    financial.obligation(
        case.id,
        obligation_type="facility",
        principal_amount=Decimal("100.00"),
        outstanding_amount=Decimal("150.00"),
        currency=None,
        details={"availableAmount": "25"},
        dedupe_key="test:obligation:bad",
    )
    financial.validation_issue(
        case.id,
        record=balance,
        record_table="financial_balances",
        field_name="currency",
        details={"entity_type": "balance"},
    )
    db_session.commit()

    response = validate_financial_data(db_session, factories.ctx, case.id)

    codes = {issue.rule_id for issue in response.issues}
    assert response.summary.total == response.issue_count
    assert response.summary.error > 0
    assert response.summary.warning > 0
    assert "stale_issue" not in codes
    assert {
        "institution_name_required",
        "account_name_required",
        "account_type_required",
        "account_currency_required",
        "balance_account_required",
        "balance_reporting_period_required",
        "reporting_period_date_required",
        "reporting_period_end_before_start",
        "balance_currency_required",
        "balance_currency_mismatch",
        "obligation_currency_required",
        "obligation_outstanding_exceeds_principal",
        "facility_available_amount_mismatch",
        "source_traceability_missing",
    } <= codes
    assert not any(
        issue.rule_id == "balance_currency_mismatch"
        and issue.record_id == multi_currency_balance.id
        for issue in response.issues
    )
    available_issue = next(
        issue for issue in response.issues if issue.rule_id == "facility_available_amount_mismatch"
    )
    assert available_issue.field == "details.availableAmount"
    assert available_issue.details["available_amount_field"] == "availableAmount"

    stored_codes = {
        issue.rule_id
        for issue in db_session.scalars(
            select(FinancialValidationIssue).where(FinancialValidationIssue.case_id == case.id)
        )
    }
    assert stored_codes == codes
    stored_issue_keys = [
        issue.issue_key
        for issue in db_session.scalars(
            select(FinancialValidationIssue).where(FinancialValidationIssue.case_id == case.id)
        )
    ]
    assert len(stored_issue_keys) == len(set(stored_issue_keys))


def test_validate_financial_data_requires_source_traceability_per_field(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    financial = factories.financial
    account = financial.account(
        case.id,
        account_name="Operating",
        account_type="deposit",
        currency="GHS",
        dedupe_key="test:trace:account",
    )
    balance = financial.balance(
        case.id,
        account=account,
        balance_type="cash",
        amount=Decimal("100.00"),
        currency="GHS",
        dedupe_key="test:trace:balance",
    )
    source_row = financial.source_row(case.id, row_index=0, raw_payload={"amount": "100.00"})
    financial.source_link(
        case.id,
        record=balance,
        record_table="financial_balances",
        source_row=source_row,
        field_name="amount",
        source_field="Amount",
    )
    db_session.commit()

    response = validate_financial_data(db_session, factories.ctx, case.id)

    balance_traceability_fields = {
        issue.field
        for issue in response.issues
        if issue.record_id == balance.id and issue.rule_id == "source_traceability_missing"
    }
    assert "amount" not in balance_traceability_fields
    assert "currency" in balance_traceability_fields


def test_validate_financial_data_accepts_manual_edit_as_field_traceability(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    financial = factories.financial
    account = financial.account(
        case.id,
        account_name="Operating",
        account_type="deposit",
        currency="GHS",
        dedupe_key="test:trace:manual-account",
    )
    financial.manual_edit(
        case.id,
        record=account,
        record_table="financial_accounts",
        field_name="currency",
        new_value="GHS",
    )
    db_session.commit()

    response = validate_financial_data(db_session, factories.ctx, case.id)

    account_traceability_fields = {
        issue.field
        for issue in response.issues
        if issue.record_id == account.id and issue.rule_id == "source_traceability_missing"
    }
    assert "currency" not in account_traceability_fields
    assert {"account_name", "account_type"} <= account_traceability_fields


def test_evaluate_financial_validation_handles_available_amount_aliases_directly(
    db_session: Session,
    service_factories: ServiceFactories,
) -> None:
    factories = service_factories
    case = factories.cases.create()
    obligation = factories.financial.obligation(
        case.id,
        principal_amount=Decimal("100.00"),
        outstanding_amount=Decimal("40.00"),
        details={"remainingAmount": "20"},
        dedupe_key="test:rule:available-alias",
    )

    issues = evaluate_financial_validation(
        FinancialValidationDataset(
            institutions=[],
            accounts=[],
            periods=[],
            balances=[],
            obligations=[obligation],
            links=[],
            manual_edits=[],
        )
    )

    available_issue = next(
        issue for issue in issues if issue.rule_id == FACILITY_AVAILABLE_AMOUNT_MISMATCH
    )
    assert available_issue.field == "details.remainingAmount"
    assert available_issue.details["available_amount_field"] == "remainingAmount"
