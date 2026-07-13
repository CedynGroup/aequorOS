from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from app.schemas.common import JsonObject, JsonValue

type FinancialValidationSeverity = Literal["error", "warning", "info"]
type FinancialString120 = Annotated[str, Field(max_length=120)]
type FinancialCurrency = Annotated[str, Field(min_length=3, max_length=3)]
type FinancialAmount = Annotated[Decimal, Field(max_digits=20, decimal_places=4)]
type FinancialRate = Annotated[Decimal, Field(max_digits=10, decimal_places=6)]
type FinancialCovenantAmount = Annotated[Decimal, Field(max_digits=20, decimal_places=6)]
type FinancialMutationReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
type FinancialValidationEntityType = Literal[
    "institution",
    "account",
    "reporting_period",
    "balance",
    "cash_flow",
    "obligation",
    "covenant",
]

ENTITY_TYPE_BY_TABLE: dict[str, FinancialValidationEntityType] = {
    "financial_institutions": "institution",
    "financial_accounts": "account",
    "financial_reporting_periods": "reporting_period",
    "financial_balances": "balance",
    "financial_cash_flows": "cash_flow",
    "financial_obligations": "obligation",
    "financial_covenants": "covenant",
}


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


class FinancialCashFlowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    account_id: UUID | None
    reporting_period_id: UUID | None
    cash_flow_date: date | None
    amount: Decimal
    currency: str | None
    direction: str
    category: str
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialCashFlowCreate(BaseModel):
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    cash_flow_date: date | None = None
    amount: str = Field(min_length=1)
    currency: str | None = None
    direction: str
    category: str
    metadata: JsonObject = Field(default_factory=dict)


class FinancialCashFlowUpdate(BaseModel):
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    cash_flow_date: date | None = None
    amount: str | None = Field(default=None, min_length=1)
    currency: str | None = None
    direction: str | None = None
    category: str | None = None
    metadata: JsonObject | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def require_update_field(self) -> FinancialCashFlowUpdate:
        update_fields = set(self.model_fields_set) - {"reason"}
        if not update_fields:
            raise ValueError("At least one cash-flow field is required.")
        return self


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


class ManualMutationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: FinancialMutationReason


class ManualUpdatePayload(ManualMutationPayload):
    @model_validator(mode="after")
    def require_update_field(self) -> ManualUpdatePayload:
        if not (set(self.model_fields_set) - {"reason"}):
            raise ValueError("At least one editable field is required.")
        return self


class FinancialInstitutionCreate(ManualMutationPayload):
    name: str = Field(min_length=1)
    institution_type: FinancialString120 | None = None
    reference_code: FinancialString120 | None = None
    metadata: JsonObject = Field(default_factory=dict)


class FinancialInstitutionUpdate(ManualUpdatePayload):
    name: str = Field(default="", min_length=1)
    institution_type: FinancialString120 | None = None
    reference_code: FinancialString120 | None = None
    metadata: JsonObject | None = None


class FinancialAccountCreate(ManualMutationPayload):
    institution_id: UUID | None = None
    account_number: str | None = None
    account_name: str = Field(min_length=1)
    account_type: FinancialString120 | None = None
    currency: FinancialCurrency | None = None
    status: Literal["active", "inactive", "closed", "unknown"] | None = None
    metadata: JsonObject = Field(default_factory=dict)


class FinancialAccountUpdate(ManualUpdatePayload):
    institution_id: UUID | None = None
    account_number: str | None = None
    account_name: str = Field(default="", min_length=1)
    account_type: FinancialString120 | None = None
    currency: FinancialCurrency | None = None
    status: Literal["active", "inactive", "closed", "unknown"] | None = None
    metadata: JsonObject | None = None


class FinancialReportingPeriodCreate(ManualMutationPayload):
    period_type: Literal["as_of", "day", "month", "quarter", "year", "custom"]
    start_date: date | None = None
    end_date: date | None = None
    as_of_date: date | None = None
    label: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class FinancialReportingPeriodUpdate(ManualUpdatePayload):
    period_type: Literal["as_of", "day", "month", "quarter", "year", "custom"] = "custom"
    start_date: date | None = None
    end_date: date | None = None
    as_of_date: date | None = None
    label: str | None = None
    metadata: JsonObject | None = None


class FinancialBalanceCreate(ManualMutationPayload):
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    balance_type: FinancialString120 = Field(min_length=1)
    amount: FinancialAmount
    currency: FinancialCurrency | None = None
    as_of_date: date | None = None
    metadata: JsonObject = Field(default_factory=dict)


class FinancialBalanceUpdate(ManualUpdatePayload):
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    balance_type: FinancialString120 = Field(default="", min_length=1)
    amount: FinancialAmount = Decimal(0)
    currency: FinancialCurrency | None = None
    as_of_date: date | None = None
    metadata: JsonObject | None = None


class FinancialObligationCreate(ManualMutationPayload):
    institution_id: UUID | None = None
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    obligation_type: FinancialString120 = Field(min_length=1)
    facility_type: FinancialString120 | None = None
    principal_amount: FinancialAmount | None = None
    outstanding_amount: FinancialAmount | None = None
    currency: FinancialCurrency | None = None
    start_date: date | None = None
    maturity_date: date | None = None
    interest_rate: FinancialRate | None = None
    status: Literal["active", "inactive", "closed", "matured", "defaulted", "unknown"] | None = None
    details: JsonObject = Field(default_factory=dict)


class FinancialObligationUpdate(ManualUpdatePayload):
    institution_id: UUID | None = None
    account_id: UUID | None = None
    reporting_period_id: UUID | None = None
    obligation_type: FinancialString120 = Field(default="", min_length=1)
    facility_type: FinancialString120 | None = None
    principal_amount: FinancialAmount | None = None
    outstanding_amount: FinancialAmount | None = None
    currency: FinancialCurrency | None = None
    start_date: date | None = None
    maturity_date: date | None = None
    interest_rate: FinancialRate | None = None
    status: Literal["active", "inactive", "closed", "matured", "defaulted", "unknown"] | None = None
    details: JsonObject | None = None


type FinancialCovenantOperator = Literal["lt", "lte", "eq", "gte", "gt"]
type FinancialCovenantComplianceStatus = Literal["compliant", "non_compliant", "unknown"]


class FinancialCovenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    obligation_id: UUID | None
    reporting_period_id: UUID | None
    name: str
    metric: str
    operator: FinancialCovenantOperator
    threshold: Decimal
    actual_value: Decimal | None
    compliance_status: FinancialCovenantComplianceStatus
    source_record: JsonObject
    reporting_context: JsonObject
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class FinancialCovenantCreate(ManualMutationPayload):
    obligation_id: UUID | None = None
    reporting_period_id: UUID | None = None
    name: str = Field(min_length=1)
    metric: FinancialString120 = Field(min_length=1)
    operator: FinancialCovenantOperator
    threshold: FinancialCovenantAmount
    actual_value: FinancialCovenantAmount | None = None
    compliance_status: FinancialCovenantComplianceStatus | None = None
    source_record: JsonObject = Field(default_factory=dict)
    reporting_context: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class FinancialCovenantUpdate(ManualUpdatePayload):
    obligation_id: UUID | None = None
    reporting_period_id: UUID | None = None
    name: str = Field(default="", min_length=1)
    metric: FinancialString120 = Field(default="", min_length=1)
    operator: FinancialCovenantOperator = "eq"
    threshold: FinancialCovenantAmount = Decimal(0)
    actual_value: FinancialCovenantAmount | None = None
    compliance_status: FinancialCovenantComplianceStatus = "unknown"
    source_record: JsonObject = Field(default_factory=dict)
    reporting_context: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class FinancialSourceRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    document_id: UUID | None
    document_extraction_id: UUID | None
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
    field_name: str | None
    source_field: str | None
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
    issue_key: str
    field_name: str
    severity: str
    status: str
    rule_id: str
    message: str
    details: JsonObject
    created_at: datetime
    resolved_at: datetime | None

    @computed_field
    @property
    def code(self) -> str | None:
        return self.rule_id

    @computed_field
    @property
    def entity_type(self) -> FinancialValidationEntityType | None:
        if self.record_table is None:
            value = self.details.get("entity_type")
            if value in set(ENTITY_TYPE_BY_TABLE.values()):
                return value
            return None
        return ENTITY_TYPE_BY_TABLE.get(self.record_table)

    @computed_field
    @property
    def entity_id(self) -> UUID | None:
        return self.record_id

    @computed_field
    @property
    def field(self) -> str | None:
        if self.field_name:
            return self.field_name
        value = self.details.get("field")
        return value if isinstance(value, str) else None


class FinancialValidationSummaryRead(BaseModel):
    total: int = 0
    error: int = 0
    warning: int = 0
    info: int = 0


class FinancialValidationRunResponse(BaseModel):
    case_id: UUID
    organization_id: UUID
    issue_count: int
    summary: FinancialValidationSummaryRead
    issues: list[FinancialValidationIssueRead]


class FinancialInstitutionMutationResponse(BaseModel):
    record: FinancialInstitutionRead
    validation: FinancialValidationRunResponse


class FinancialAccountMutationResponse(BaseModel):
    record: FinancialAccountRead
    validation: FinancialValidationRunResponse


class FinancialReportingPeriodMutationResponse(BaseModel):
    record: FinancialReportingPeriodRead
    validation: FinancialValidationRunResponse


class FinancialBalanceMutationResponse(BaseModel):
    record: FinancialBalanceRead
    validation: FinancialValidationRunResponse


class FinancialObligationMutationResponse(BaseModel):
    record: FinancialObligationRead
    validation: FinancialValidationRunResponse


class FinancialCovenantMutationResponse(BaseModel):
    record: FinancialCovenantRead
    validation: FinancialValidationRunResponse


class FinancialDataWorkspaceRead(BaseModel):
    case_id: UUID
    organization_id: UUID
    institutions: list[FinancialInstitutionRead]
    accounts: list[FinancialAccountRead]
    reporting_periods: list[FinancialReportingPeriodRead]
    balances: list[FinancialBalanceRead]
    cash_flows: list[FinancialCashFlowRead]
    obligations: list[FinancialObligationRead]
    covenants: list[FinancialCovenantRead]
    source_rows: list[FinancialSourceRowRead]
    record_source_links: list[FinancialRecordSourceLinkRead]
    manual_edits: list[FinancialManualEditRead]
    validation_issues: list[FinancialValidationIssueRead]
    validation_summary: FinancialValidationSummaryRead
