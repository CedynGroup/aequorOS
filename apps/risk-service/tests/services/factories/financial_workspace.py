from __future__ import annotations

# ruff: noqa: PLR0913
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
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
from app.schemas.common import JsonObject, JsonValue


@dataclass(frozen=True)
class FinancialWorkspaceFactory:
    db: Session
    ctx: TenantContext

    def institution(
        self,
        case_id: UUID,
        *,
        name: str = "Aequor Bank",
        dedupe_key: str = "test:institution",
        institution_type: str | None = None,
        reference_code: str | None = None,
        metadata: JsonObject | None = None,
    ) -> FinancialInstitution:
        record = FinancialInstitution(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            dedupe_key=dedupe_key,
            name=name,
            institution_type=institution_type,
            reference_code=reference_code,
            metadata_=metadata or {},
        )
        return self.add(record)

    def account(
        self,
        case_id: UUID,
        *,
        institution: FinancialInstitution | None = None,
        account_name: str = "Operating",
        account_type: str | None = "deposit",
        currency: str | None = "GHS",
        dedupe_key: str = "test:account",
        metadata: JsonObject | None = None,
    ) -> FinancialAccount:
        record = FinancialAccount(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            dedupe_key=dedupe_key,
            institution_id=institution.id if institution is not None else None,
            account_name=account_name,
            account_type=account_type,
            currency=currency,
            metadata_=metadata or {},
        )
        return self.add(record)

    def reporting_period(
        self,
        case_id: UUID,
        *,
        period_type: str = "custom",
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
        label: str | None = None,
        dedupe_key: str = "test:period",
    ) -> FinancialReportingPeriod:
        record = FinancialReportingPeriod(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            dedupe_key=dedupe_key,
            period_type=period_type,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            label=label,
        )
        return self.add(record)

    def balance(
        self,
        case_id: UUID,
        *,
        account: FinancialAccount | None = None,
        reporting_period: FinancialReportingPeriod | None = None,
        balance_type: str = "cash",
        amount: Decimal = Decimal("100.00"),
        currency: str | None = "GHS",
        as_of_date: date | None = None,
        dedupe_key: str = "test:balance",
    ) -> FinancialBalance:
        record = FinancialBalance(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            dedupe_key=dedupe_key,
            account_id=account.id if account is not None else None,
            reporting_period_id=reporting_period.id if reporting_period is not None else None,
            balance_type=balance_type,
            amount=amount,
            currency=currency,
            as_of_date=as_of_date,
        )
        return self.add(record)

    def obligation(
        self,
        case_id: UUID,
        *,
        institution: FinancialInstitution | None = None,
        account: FinancialAccount | None = None,
        reporting_period: FinancialReportingPeriod | None = None,
        obligation_type: str = "facility",
        facility_type: str | None = None,
        principal_amount: Decimal | None = Decimal("100.00"),
        outstanding_amount: Decimal | None = Decimal("50.00"),
        currency: str | None = "GHS",
        details: JsonObject | None = None,
        dedupe_key: str = "test:obligation",
    ) -> FinancialObligation:
        record = FinancialObligation(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            dedupe_key=dedupe_key,
            institution_id=institution.id if institution is not None else None,
            account_id=account.id if account is not None else None,
            reporting_period_id=reporting_period.id if reporting_period is not None else None,
            obligation_type=obligation_type,
            facility_type=facility_type,
            principal_amount=principal_amount,
            outstanding_amount=outstanding_amount,
            currency=currency,
            details=details or {},
        )
        return self.add(record)

    def source_row(
        self,
        case_id: UUID,
        *,
        row_index: int | None = 0,
        raw_payload: JsonObject | None = None,
        locator: JsonObject | None = None,
    ) -> FinancialSourceRow:
        record = FinancialSourceRow(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            row_index=row_index,
            raw_payload=raw_payload or {},
            locator=locator or {},
        )
        return self.add(record)

    def source_link(
        self,
        case_id: UUID,
        *,
        record: Any,
        record_table: str,
        field_name: str,
        source_row: FinancialSourceRow | None = None,
        source_field: str | None = None,
    ) -> FinancialRecordSourceLink:
        if source_row is None:
            source_row = self.source_row(case_id)
        link = FinancialRecordSourceLink(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            record_table=record_table,
            record_id=record.id,
            source_row_id=source_row.id,
            field_name=field_name,
            source_field=source_field or field_name,
        )
        return self.add(link)

    def manual_edit(
        self,
        case_id: UUID,
        *,
        record: Any,
        record_table: str,
        field_name: str,
        previous_value: JsonValue = None,
        new_value: JsonValue = None,
    ) -> FinancialManualEditHistory:
        edit = FinancialManualEditHistory(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            record_table=record_table,
            record_id=record.id,
            field_name=field_name,
            previous_value=previous_value,
            new_value=new_value,
        )
        return self.add(edit)

    def validation_issue(
        self,
        case_id: UUID,
        *,
        record: Any,
        record_table: str,
        rule_id: str = "stale_issue",
        issue_key: str = "stale_issue:test",
        field_name: str = "",
        severity: str = "warning",
        message: str = "Stale issue.",
        details: JsonObject | None = None,
    ) -> FinancialValidationIssue:
        issue = FinancialValidationIssue(
            organization_id=self.ctx.organization_id,
            case_id=case_id,
            record_table=record_table,
            record_id=record.id,
            issue_key=issue_key,
            field_name=field_name,
            severity=severity,
            status="open",
            rule_id=rule_id,
            message=message,
            details=details or {},
        )
        return self.add(issue)

    def add[T](self, record: T) -> T:
        self.db.add(record)
        self.db.flush()
        return record
