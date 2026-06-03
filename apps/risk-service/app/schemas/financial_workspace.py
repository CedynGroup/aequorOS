from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import JsonObject, JsonValue


class FinancialInstitutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    name: str
    institution_type: str | None
    reference_code: str | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    institution_id: UUID | None
    account_number: str | None
    account_name: str
    account_type: str | None
    currency: str | None
    status: str | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialReportingPeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    period_type: str
    start_date: date | None
    end_date: date | None
    as_of_date: date | None
    label: str | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialBalanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    account_id: UUID | None
    reporting_period_id: UUID | None
    balance_type: str
    amount: Decimal
    currency: str | None
    as_of_date: date | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialObligationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    institution_id: UUID | None
    account_id: UUID | None
    reporting_period_id: UUID | None
    obligation_type: str
    facility_type: str | None
    principal_amount: Decimal | None
    outstanding_amount: Decimal | None
    currency: str | None
    start_date: date | None
    maturity_date: date | None
    interest_rate: Decimal | None
    status: str | None
    details: JsonObject
    created_at: datetime
    updated_at: datetime


class FinancialSourceRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    document_id: UUID | None
    row_index: int | None
    locator: JsonObject
    raw_payload: JsonObject
    created_at: datetime


class FinancialRecordSourceLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    record_table: str
    record_id: UUID
    source_row_id: UUID
    confidence: Decimal | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime


class FinancialManualEditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    record_table: str
    record_id: UUID
    field_name: str
    previous_value: JsonValue
    new_value: JsonValue
    edited_by: UUID | None
    reason: str | None
    created_at: datetime


class FinancialValidationIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    record_table: str | None
    record_id: UUID | None
    severity: str
    status: str
    rule_id: str | None
    message: str
    details: JsonObject
    created_at: datetime
    resolved_at: datetime | None


class FinancialDataWorkspaceRead(BaseModel):
    case_id: UUID
    organization_id: UUID
    institutions: list[FinancialInstitutionRead]
    accounts: list[FinancialAccountRead]
    reporting_periods: list[FinancialReportingPeriodRead]
    balances: list[FinancialBalanceRead]
    obligations: list[FinancialObligationRead]
    source_rows: list[FinancialSourceRowRead]
    record_source_links: list[FinancialRecordSourceLinkRead]
    manual_edits: list[FinancialManualEditRead]
    validation_issues: list[FinancialValidationIssueRead]
