from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol, cast
from uuid import UUID

from app.models import (
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialManualEditHistory,
    FinancialObligation,
    FinancialRecordSourceLink,
    FinancialReportingPeriod,
)
from app.schemas.common import JsonObject
from app.schemas.financial_workspace import FinancialValidationSeverity
from app.services.financial_mapping.normalization import parse_decimal

type RecordTable = Literal[
    "financial_institutions",
    "financial_accounts",
    "financial_reporting_periods",
    "financial_balances",
    "financial_cash_flows",
    "financial_obligations",
    "financial_covenants",
]

type FinancialValidationRecord = (
    FinancialInstitution
    | FinancialAccount
    | FinancialReportingPeriod
    | FinancialBalance
    | FinancialCashFlow
    | FinancialObligation
    | FinancialCovenant
)

INSTITUTION_NAME_REQUIRED = "institution_name_required"
ACCOUNT_NAME_REQUIRED = "account_name_required"
ACCOUNT_TYPE_REQUIRED = "account_type_required"
ACCOUNT_CURRENCY_REQUIRED = "account_currency_required"
REPORTING_PERIOD_DATE_REQUIRED = "reporting_period_date_required"
REPORTING_PERIOD_END_BEFORE_START = "reporting_period_end_before_start"
BALANCE_ACCOUNT_REQUIRED = "balance_account_required"
BALANCE_PERIOD_REQUIRED = "balance_reporting_period_required"
BALANCE_AMOUNT_REQUIRED = "balance_amount_required"
BALANCE_CURRENCY_REQUIRED = "balance_currency_required"
BALANCE_CURRENCY_MISMATCH = "balance_currency_mismatch"
CASH_FLOW_CURRENCY_REVIEW = "cash_flow_missing_currency"
CASH_FLOW_PERIOD_OR_DATE_REVIEW = "cash_flow_missing_period_or_date"
OBLIGATION_PRINCIPAL_REQUIRED = "obligation_principal_required"
OBLIGATION_OUTSTANDING_REQUIRED = "obligation_outstanding_required"
OBLIGATION_CURRENCY_REQUIRED = "obligation_currency_required"
OBLIGATION_OUTSTANDING_EXCEEDS_PRINCIPAL = "obligation_outstanding_exceeds_principal"
FACILITY_AVAILABLE_AMOUNT_MISMATCH = "facility_available_amount_mismatch"
SOURCE_TRACEABILITY_MISSING = "source_traceability_missing"
COVENANT_COMPLIANCE_STATUS_MISMATCH = "covenant_compliance_status_mismatch"
COVENANT_OBLIGATION_REQUIRED = "covenant_obligation_required"

AVAILABLE_AMOUNT_FIELDS = (
    "available_amount",
    "availableAmount",
    "available",
    "undrawn_amount",
    "undrawnAmount",
    "unused_amount",
    "unusedAmount",
    "remaining_amount",
    "remainingAmount",
)


@dataclass(frozen=True)
class IssueDraft:
    record_table: RecordTable
    record_id: UUID
    severity: FinancialValidationSeverity
    rule_id: str
    field: str | None
    message: str
    details: JsonObject


@dataclass(frozen=True)
class FinancialValidationDataset:
    institutions: list[FinancialInstitution]
    accounts: list[FinancialAccount]
    periods: list[FinancialReportingPeriod]
    balances: list[FinancialBalance]
    cash_flows: list[FinancialCashFlow]
    obligations: list[FinancialObligation]
    links: list[FinancialRecordSourceLink]
    manual_edits: list[FinancialManualEditHistory]
    covenants: list[FinancialCovenant] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationContext:
    traceability_keys: set[tuple[str, UUID, str]]
    accounts_by_id: dict[UUID, FinancialAccount]

    @classmethod
    def from_dataset(cls, dataset: FinancialValidationDataset) -> ValidationContext:
        return cls(
            traceability_keys={
                (link.record_table, link.record_id, link.field_name)
                for link in dataset.links
                if link.field_name is not None
            }
            | {
                (edit.record_table, edit.record_id, edit.field_name)
                for edit in dataset.manual_edits
            },
            accounts_by_id={account.id: account for account in dataset.accounts},
        )


@dataclass(frozen=True)
class FieldRule[TRecord]:
    field: str
    code: str
    message: str
    value: Callable[[TRecord], object]
    severity: FinancialValidationSeverity = "error"
    is_missing: Callable[[object], bool] = lambda value: value is None
    traceable: bool = True

    def evaluate(
        self,
        record: TRecord,
        record_table: RecordTable,
        context: ValidationContext,
    ) -> Iterable[IssueDraft]:
        value = self.value(record)
        if self.is_missing(value):
            yield issue(
                record,
                record_table,
                self.severity,
                self.code,
                self.field,
                self.message,
            )
        elif self.traceable:
            traceability_issue = maybe_traceability_issue(record, record_table, self.field, context)
            if traceability_issue is not None:
                yield traceability_issue


@dataclass(frozen=True)
class EntityValidationSpec[TRecord]:
    table: RecordTable
    records: Callable[[FinancialValidationDataset], Sequence[TRecord]]
    fields: tuple[FieldRule[TRecord], ...] = ()
    record_rules: tuple[Callable[[TRecord, ValidationContext], Iterable[IssueDraft]], ...] = ()

    def evaluate(
        self,
        dataset: FinancialValidationDataset,
        context: ValidationContext,
    ) -> Iterable[IssueDraft]:
        for record in self.records(dataset):
            for field_rule in self.fields:
                yield from field_rule.evaluate(record, self.table, context)
            for record_rule in self.record_rules:
                yield from record_rule(record, context)


class FinancialValidationSpec(Protocol):
    def evaluate(
        self,
        dataset: FinancialValidationDataset,
        context: ValidationContext,
    ) -> Iterable[IssueDraft]: ...


@dataclass(frozen=True)
class ParsedAvailableAmount:
    field_name: str
    value: Decimal


def evaluate_financial_validation(dataset: FinancialValidationDataset) -> list[IssueDraft]:
    context = ValidationContext.from_dataset(dataset)
    drafts: list[IssueDraft] = []

    for spec in ENTITY_VALIDATION_SPECS:
        drafts.extend(spec.evaluate(dataset, context))
    return drafts


def validate_reporting_period_dates(
    period: FinancialReportingPeriod,
    context: ValidationContext,
) -> Iterable[IssueDraft]:
    has_complete_period = period.start_date is not None and period.end_date is not None
    if not has_complete_period and period.as_of_date is None:
        yield issue(
            period,
            "financial_reporting_periods",
            "error",
            REPORTING_PERIOD_DATE_REQUIRED,
            "as_of_date",
            "Reporting period requires a start/end date pair or an as-of date.",
        )
    for field_name, value in (
        ("start_date", period.start_date),
        ("end_date", period.end_date),
        ("as_of_date", period.as_of_date),
    ):
        if value is not None:
            traceability_issue = maybe_traceability_issue(
                period,
                "financial_reporting_periods",
                field_name,
                context,
            )
            if traceability_issue is not None:
                yield traceability_issue


def validate_reporting_period_order(
    period: FinancialReportingPeriod,
    _context: ValidationContext,
) -> Iterable[IssueDraft]:
    if (
        period.start_date is not None
        and period.end_date is not None
        and period.end_date < period.start_date
    ):
        yield issue(
            period,
            "financial_reporting_periods",
            "error",
            REPORTING_PERIOD_END_BEFORE_START,
            "end_date",
            "Reporting period end date cannot precede start date.",
            {
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
            },
        )


def validate_balance_links(
    balance: FinancialBalance,
    _context: ValidationContext,
) -> Iterable[IssueDraft]:
    if balance.account_id is None:
        yield issue(
            balance,
            "financial_balances",
            "error",
            BALANCE_ACCOUNT_REQUIRED,
            "account_id",
            "Balance should be linked to an account.",
        )
    if balance.reporting_period_id is None and balance.as_of_date is None:
        yield issue(
            balance,
            "financial_balances",
            "error",
            BALANCE_PERIOD_REQUIRED,
            "reporting_period_id",
            "Balance should have a reporting period or as-of date.",
        )


def validate_balance_account_currency(
    balance: FinancialBalance,
    context: ValidationContext,
) -> Iterable[IssueDraft]:
    account = context.accounts_by_id.get(balance.account_id) if balance.account_id else None
    if (
        account is not None
        and balance.currency is not None
        and account.currency is not None
        and balance.currency != account.currency
        and account.metadata_.get("multi_currency") is not True
    ):
        yield issue(
            balance,
            "financial_balances",
            "warning",
            BALANCE_CURRENCY_MISMATCH,
            "currency",
            "Balance currency should match account currency unless the account is multi-currency.",
            {
                "account_id": str(account.id),
                "account_currency": account.currency,
                "balance_currency": balance.currency,
            },
        )


def validate_obligation_amounts(
    obligation: FinancialObligation,
    _context: ValidationContext,
) -> Iterable[IssueDraft]:
    principal = obligation.principal_amount
    outstanding = obligation.outstanding_amount
    if principal is not None and outstanding is not None and outstanding > principal:
        yield issue(
            obligation,
            "financial_obligations",
            "error",
            OBLIGATION_OUTSTANDING_EXCEEDS_PRINCIPAL,
            "outstanding_amount",
            "Obligation outstanding amount cannot exceed principal amount.",
            {
                "principal_amount": decimal_json(principal),
                "outstanding_amount": decimal_json(outstanding),
            },
        )

    available = parse_available_amount(obligation.details)
    if available is None or principal is None or outstanding is None:
        return

    expected = principal - outstanding
    if available.value != expected:
        yield issue(
            obligation,
            "financial_obligations",
            "warning",
            FACILITY_AVAILABLE_AMOUNT_MISMATCH,
            f"details.{available.field_name}",
            "Facility available amount should equal committed amount minus drawn amount.",
            {
                "available_amount": decimal_json(available.value),
                "available_amount_field": available.field_name,
                "expected_available_amount": decimal_json(expected),
                "principal_amount": decimal_json(principal),
                "outstanding_amount": decimal_json(outstanding),
            },
        )


def validate_cash_flow_review_fields(
    cash_flow: FinancialCashFlow,
    _context: ValidationContext,
) -> Iterable[IssueDraft]:
    if cash_flow.currency is None:
        yield issue(
            cash_flow,
            "financial_cash_flows",
            "warning",
            CASH_FLOW_CURRENCY_REVIEW,
            "currency",
            "Cash-flow currency should be confirmed from source data.",
            {"field": "currency"},
        )
    if cash_flow.cash_flow_date is None and cash_flow.reporting_period_id is None:
        yield issue(
            cash_flow,
            "financial_cash_flows",
            "warning",
            CASH_FLOW_PERIOD_OR_DATE_REVIEW,
            "cash_flow_date",
            "Cash flow should have either a cash-flow date or reporting period.",
            {"fields": ["cash_flow_date", "reporting_period_id"]},
        )


def validate_covenant(
    covenant: FinancialCovenant,
    _context: ValidationContext,
) -> Iterable[IssueDraft]:
    if covenant.obligation_id is None:
        yield issue(
            covenant,
            "financial_covenants",
            "warning",
            COVENANT_OBLIGATION_REQUIRED,
            "obligation_id",
            "Covenant should be linked to an obligation or facility when available.",
        )
    if covenant.actual_value is None:
        expected = "unknown"
    else:
        comparisons = {
            "lt": covenant.actual_value < covenant.threshold,
            "lte": covenant.actual_value <= covenant.threshold,
            "eq": covenant.actual_value == covenant.threshold,
            "gte": covenant.actual_value >= covenant.threshold,
            "gt": covenant.actual_value > covenant.threshold,
        }
        expected = "compliant" if comparisons[covenant.operator] else "non_compliant"
    if covenant.compliance_status != expected:
        yield issue(
            covenant,
            "financial_covenants",
            "error",
            COVENANT_COMPLIANCE_STATUS_MISMATCH,
            "compliance_status",
            "Covenant compliance status does not match its operator, threshold, and actual value.",
            {
                "actual_status": covenant.compliance_status,
                "expected_status": expected,
                "operator": covenant.operator,
                "threshold": decimal_json(covenant.threshold),
                "actual_value": decimal_json(covenant.actual_value)
                if covenant.actual_value is not None
                else None,
            },
        )


def issue(  # noqa: PLR0913
    record: object,
    record_table: RecordTable,
    severity: FinancialValidationSeverity,
    rule_id: str,
    field: str | None,
    message: str,
    details: JsonObject | None = None,
) -> IssueDraft:
    return IssueDraft(
        record_table=record_table,
        record_id=record_id(record),
        severity=severity,
        rule_id=rule_id,
        field=field,
        message=message,
        details=details or {},
    )


def maybe_traceability_issue(
    record: object,
    record_table: RecordTable,
    field_name: str,
    context: ValidationContext,
) -> IssueDraft | None:
    id_ = record_id(record)
    if (record_table, id_, field_name) in context.traceability_keys:
        return None
    return issue(
        record,
        record_table,
        "warning",
        SOURCE_TRACEABILITY_MISSING,
        field_name,
        "Canonical financial field should have source traceability.",
        {"traceability_scope": "field"},
    )


def record_id(record: object) -> UUID:
    return cast(UUID, cast(FinancialValidationRecord, record).id)


def is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def is_blank_value(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def parse_available_amount(details: JsonObject) -> ParsedAvailableAmount | None:
    for field_name in AVAILABLE_AMOUNT_FIELDS:
        value = details.get(field_name)
        if value is None:
            continue
        parsed = parse_decimal(value)
        if parsed is not None:
            return ParsedAvailableAmount(field_name=field_name, value=parsed)
    return None


def decimal_json(value: Decimal) -> str:
    return format(value, "f")


INSTITUTION_FIELD_RULES: tuple[FieldRule[FinancialInstitution], ...] = (
    FieldRule(
        field="name",
        code=INSTITUTION_NAME_REQUIRED,
        message="Institution name is required.",
        value=lambda institution: institution.name,
        is_missing=is_blank_value,
    ),
)

ACCOUNT_FIELD_RULES: tuple[FieldRule[FinancialAccount], ...] = (
    FieldRule(
        field="account_name",
        code=ACCOUNT_NAME_REQUIRED,
        message="Account name is required.",
        value=lambda account: account.account_name,
        is_missing=is_blank_value,
    ),
    FieldRule(
        field="account_type",
        code=ACCOUNT_TYPE_REQUIRED,
        message="Account type is required.",
        value=lambda account: account.account_type,
        is_missing=is_blank_value,
    ),
    FieldRule(
        field="currency",
        code=ACCOUNT_CURRENCY_REQUIRED,
        message="Account currency is required.",
        value=lambda account: account.currency,
        is_missing=is_blank_value,
    ),
)

BALANCE_FIELD_RULES: tuple[FieldRule[FinancialBalance], ...] = (
    FieldRule(
        field="amount",
        code=BALANCE_AMOUNT_REQUIRED,
        message="Balance amount is required.",
        value=lambda balance: balance.amount,
    ),
    FieldRule(
        field="currency",
        code=BALANCE_CURRENCY_REQUIRED,
        message="Balance currency is required.",
        value=lambda balance: balance.currency,
        is_missing=is_blank_value,
    ),
)

OBLIGATION_FIELD_RULES: tuple[FieldRule[FinancialObligation], ...] = (
    FieldRule(
        field="principal_amount",
        code=OBLIGATION_PRINCIPAL_REQUIRED,
        message="Obligation principal or committed amount is required.",
        value=lambda obligation: obligation.principal_amount,
    ),
    FieldRule(
        field="outstanding_amount",
        code=OBLIGATION_OUTSTANDING_REQUIRED,
        message="Obligation outstanding or drawn amount is required.",
        value=lambda obligation: obligation.outstanding_amount,
    ),
    FieldRule(
        field="currency",
        code=OBLIGATION_CURRENCY_REQUIRED,
        message="Obligation currency is required.",
        value=lambda obligation: obligation.currency,
        is_missing=is_blank_value,
    ),
)

INSTITUTION_VALIDATION_SPEC = EntityValidationSpec[FinancialInstitution](
    table="financial_institutions",
    records=lambda dataset: dataset.institutions,
    fields=INSTITUTION_FIELD_RULES,
)

ACCOUNT_VALIDATION_SPEC = EntityValidationSpec[FinancialAccount](
    table="financial_accounts",
    records=lambda dataset: dataset.accounts,
    fields=ACCOUNT_FIELD_RULES,
)

REPORTING_PERIOD_VALIDATION_SPEC = EntityValidationSpec[FinancialReportingPeriod](
    table="financial_reporting_periods",
    records=lambda dataset: dataset.periods,
    record_rules=(validate_reporting_period_dates, validate_reporting_period_order),
)

BALANCE_VALIDATION_SPEC = EntityValidationSpec[FinancialBalance](
    table="financial_balances",
    records=lambda dataset: dataset.balances,
    fields=BALANCE_FIELD_RULES,
    record_rules=(validate_balance_links, validate_balance_account_currency),
)

CASH_FLOW_VALIDATION_SPEC = EntityValidationSpec[FinancialCashFlow](
    table="financial_cash_flows",
    records=lambda dataset: dataset.cash_flows,
    record_rules=(validate_cash_flow_review_fields,),
)

OBLIGATION_VALIDATION_SPEC = EntityValidationSpec[FinancialObligation](
    table="financial_obligations",
    records=lambda dataset: dataset.obligations,
    fields=OBLIGATION_FIELD_RULES,
    record_rules=(validate_obligation_amounts,),
)

COVENANT_VALIDATION_SPEC = EntityValidationSpec[FinancialCovenant](
    table="financial_covenants",
    records=lambda dataset: dataset.covenants,
    fields=(
        FieldRule(
            field="name",
            code="covenant_name_required",
            message="Covenant name is required.",
            value=lambda covenant: covenant.name,
            is_missing=is_blank_value,
        ),
        FieldRule(
            field="metric",
            code="covenant_metric_required",
            message="Covenant metric is required.",
            value=lambda covenant: covenant.metric,
            is_missing=is_blank_value,
        ),
        FieldRule(
            field="threshold",
            code="covenant_threshold_required",
            message="Covenant threshold is required.",
            value=lambda covenant: covenant.threshold,
        ),
    ),
    record_rules=(validate_covenant,),
)

ENTITY_VALIDATION_SPECS: tuple[FinancialValidationSpec, ...] = (
    INSTITUTION_VALIDATION_SPEC,
    ACCOUNT_VALIDATION_SPEC,
    REPORTING_PERIOD_VALIDATION_SPEC,
    BALANCE_VALIDATION_SPEC,
    CASH_FLOW_VALIDATION_SPEC,
    OBLIGATION_VALIDATION_SPEC,
    COVENANT_VALIDATION_SPEC,
)
