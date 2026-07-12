from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    FinancialAccount,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialObligation,
    FinancialReportingPeriod,
    FinancialSourceRow,
)
from app.schemas.common import JsonObject
from app.services.financial_cash_flows import (
    normalize_direction,
    reconcile_cash_flow_review_issues,
    validated_cash_flow_values,
)
from app.services.financial_mapping.links import link_field, mapper_metadata
from app.services.financial_mapping.normalization import (
    first_decimal,
    first_field,
    normalize_currency,
    normalize_row,
    normalize_text,
    parse_date,
    parse_decimal,
    string_value,
)
from app.services.financial_mapping.types import (
    ACCOUNT_ALIASES,
    BALANCE_AMOUNT_ALIASES,
    CASH_FLOW_AMOUNT_ALIASES,
    CASH_FLOW_CATEGORY_ALIASES,
    CASH_FLOW_DATE_ALIASES,
    CASH_FLOW_DIRECTION_ALIASES,
    COVENANT_ACTUAL_ALIASES,
    COVENANT_METRIC_ALIASES,
    COVENANT_NAME_ALIASES,
    COVENANT_OPERATOR_ALIASES,
    COVENANT_STATUS_ALIASES,
    COVENANT_THRESHOLD_ALIASES,
    CURRENCY_ALIASES,
    INSTITUTION_ALIASES,
    OBLIGATION_AMOUNT_ALIASES,
    CountKey,
    ExtractedRow,
    FieldValue,
    MapperCounts,
    RecordTable,
    count,
)
from app.services.financial_mapping.upserts import (
    get_or_create_account,
    get_or_create_balance,
    get_or_create_cash_flow,
    get_or_create_covenant,
    get_or_create_institution,
    get_or_create_obligation,
    get_or_create_reporting_period,
)


@dataclass
class RowMappingContext:
    db: Session
    tenant: TenantContext
    case_id: UUID
    source_row: FinancialSourceRow
    row: ExtractedRow
    document_extraction_id: UUID
    created_counts: MapperCounts
    reused_counts: MapperCounts
    normalized: dict[str, FieldValue] = dataclass_field(init=False)
    currency_field: FieldValue | None = dataclass_field(init=False)
    currency: str | None = dataclass_field(init=False)

    def __post_init__(self) -> None:
        self.normalized = normalize_row(self.row.payload)
        self.currency_field = first_field(self.normalized, CURRENCY_ALIASES)
        self.currency = (
            normalize_currency(self.currency_field.value)
            if self.currency_field is not None
            else None
        )

    def metadata(self) -> JsonObject:
        return mapper_metadata(self.row, self.document_extraction_id)

    def count_record(self, created: bool, key: CountKey) -> None:
        count(self.created_counts if created else self.reused_counts, key)

    def link(
        self,
        *,
        record_table: RecordTable,
        record_id: UUID,
        field_name: str,
        source_field: str,
    ) -> None:
        link_field(
            self.db,
            self.tenant,
            self.case_id,
            record_table=record_table,
            record_id=record_id,
            source_row_id=self.source_row.id,
            field_name=field_name,
            source_field=source_field,
            metadata=self.metadata(),
            created_counts=self.created_counts,
            reused_counts=self.reused_counts,
        )


def map_source_row(mapping: RowMappingContext) -> int:
    mapped_record_count = 0
    institution = map_institution(mapping)
    if institution is not None:
        mapped_record_count += 1

    account = map_account(mapping, institution)
    if account is not None:
        mapped_record_count += 1

    reporting_period = map_reporting_period(mapping)
    if reporting_period is not None:
        mapped_record_count += 1

    cash_flow_intent = has_cash_flow_mapping_intent(mapping)
    cash_flow = map_cash_flow(mapping, account, reporting_period)
    if cash_flow is not None:
        mapped_record_count += 1

    if not cash_flow_intent and map_balance(mapping, account, reporting_period):
        mapped_record_count += 1

    obligation = None
    if not cash_flow_intent:
        obligation = map_obligation(mapping, institution, account, reporting_period)
    if obligation is not None:
        mapped_record_count += 1

    if map_covenant(mapping, obligation, reporting_period) is not None:
        mapped_record_count += 1

    return mapped_record_count


def map_institution(mapping: RowMappingContext) -> FinancialInstitution | None:
    institution_name = first_field(mapping.normalized, INSTITUTION_ALIASES)
    if institution_name is None or not string_value(institution_name.value):
        return None

    institution, created = get_or_create_institution(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        name=string_value(institution_name.value) or "",
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "institutions")
    mapping.link(
        record_table="financial_institutions",
        record_id=institution.id,
        field_name="name",
        source_field=institution_name.source_field,
    )
    return institution


def map_account(
    mapping: RowMappingContext,
    institution: FinancialInstitution | None,
) -> FinancialAccount | None:
    account_name = first_field(mapping.normalized, ACCOUNT_ALIASES)
    if account_name is None or not string_value(account_name.value):
        return None

    account_type = first_field(mapping.normalized, ("account_type",))
    account, created = get_or_create_account(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        institution_id=institution.id if institution is not None else None,
        account_name=string_value(account_name.value) or "",
        account_type=string_value(account_type.value) if account_type else None,
        currency=mapping.currency,
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "accounts")
    mapping.link(
        record_table="financial_accounts",
        record_id=account.id,
        field_name="account_name",
        source_field=account_name.source_field,
    )
    link_currency(mapping, "financial_accounts", account.id)
    return account


def map_reporting_period(mapping: RowMappingContext) -> FinancialReportingPeriod | None:
    period = first_field(mapping.normalized, ("period",))
    period_start = first_field(mapping.normalized, ("period_start",))
    period_end = first_field(mapping.normalized, ("period_end",))
    as_of = first_field(mapping.normalized, ("as_of_date",))

    start_date = parse_date(period_start.value) if period_start is not None else None
    end_date = parse_date(period_end.value) if period_end is not None else None
    as_of_date = parse_date(as_of.value) if as_of is not None else None
    label = string_value(period.value) if period is not None else None

    if as_of_date is None and period is not None:
        as_of_date = parse_date(period.value)
    if label is None and start_date is not None and end_date is not None:
        label = f"{start_date.isoformat()} to {end_date.isoformat()}"

    if start_date is None and end_date is None and as_of_date is None and label is None:
        return None

    period_type = "as_of" if as_of_date is not None and start_date is None else "custom"
    reporting_period, created = get_or_create_reporting_period(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
        label=label,
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "reporting_periods")

    for source in (period, period_start, period_end, as_of):
        if source is None:
            continue
        target_field = {
            "period": "label",
            "period_start": "start_date",
            "period_end": "end_date",
            "as_of_date": "as_of_date",
        }[source.canonical_name]
        mapping.link(
            record_table="financial_reporting_periods",
            record_id=reporting_period.id,
            field_name=target_field,
            source_field=source.source_field,
        )

    return reporting_period


def map_balance(
    mapping: RowMappingContext,
    account: FinancialAccount | None,
    reporting_period: FinancialReportingPeriod | None,
) -> bool:
    balance_amount = first_field(mapping.normalized, BALANCE_AMOUNT_ALIASES)
    if balance_amount is None:
        return False
    amount = parse_decimal(balance_amount.value)
    if amount is None:
        return False

    balance, created = get_or_create_balance(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        source_row_id=mapping.source_row.id,
        account_id=account.id if account is not None else None,
        reporting_period_id=reporting_period.id if reporting_period is not None else None,
        balance_type=balance_amount.canonical_name,
        amount=amount,
        currency=mapping.currency,
        as_of_date=reporting_period.as_of_date if reporting_period is not None else None,
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "balances")
    mapping.link(
        record_table="financial_balances",
        record_id=balance.id,
        field_name="amount",
        source_field=balance_amount.source_field,
    )
    link_currency(mapping, "financial_balances", balance.id)
    return True


def map_cash_flow(
    mapping: RowMappingContext,
    account: FinancialAccount | None,
    reporting_period: FinancialReportingPeriod | None,
) -> FinancialCashFlow | None:
    amount_field = first_cash_flow_amount_field(mapping)
    if amount_field is None:
        return None
    amount = parse_decimal(amount_field.value)
    if amount is None:
        return None

    direction_field = first_field(mapping.normalized, CASH_FLOW_DIRECTION_ALIASES)
    category_field = first_field(mapping.normalized, CASH_FLOW_CATEGORY_ALIASES)
    date_field = first_field(mapping.normalized, CASH_FLOW_DATE_ALIASES)
    direction = cash_flow_direction_from_fields(amount_field, direction_field, amount)
    if direction is None or category_field is None:
        return None

    if amount < 0:
        amount = abs(amount)
    try:
        values = validated_cash_flow_values(
            amount=amount,
            direction=direction,
            category=category_field.value,
            currency=mapping.currency,
        )
    except HTTPException:
        return None

    cash_flow_date = parse_date(date_field.value) if date_field is not None else None
    cash_flow, created = get_or_create_cash_flow(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        source_row_id=mapping.source_row.id,
        account_id=account.id if account is not None else None,
        reporting_period_id=reporting_period.id if reporting_period is not None else None,
        cash_flow_date=cash_flow_date,
        amount=values.amount,
        currency=values.currency,
        direction=values.direction,
        category=values.category,
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "cash_flows")
    mapping.link(
        record_table="financial_cash_flows",
        record_id=cash_flow.id,
        field_name="amount",
        source_field=amount_field.source_field,
    )
    if direction_field is not None:
        mapping.link(
            record_table="financial_cash_flows",
            record_id=cash_flow.id,
            field_name="direction",
            source_field=direction_field.source_field,
        )
    elif amount_field.canonical_name in {"inflow", "outflow"}:
        mapping.link(
            record_table="financial_cash_flows",
            record_id=cash_flow.id,
            field_name="direction",
            source_field=amount_field.source_field,
        )
    mapping.link(
        record_table="financial_cash_flows",
        record_id=cash_flow.id,
        field_name="category",
        source_field=category_field.source_field,
    )
    if date_field is not None:
        mapping.link(
            record_table="financial_cash_flows",
            record_id=cash_flow.id,
            field_name="cash_flow_date",
            source_field=date_field.source_field,
        )
    link_currency(mapping, "financial_cash_flows", cash_flow.id)
    reconcile_cash_flow_review_issues(mapping.db, mapping.tenant, cash_flow)
    return cash_flow


def first_cash_flow_amount_field(mapping: RowMappingContext) -> FieldValue | None:
    amount_field = first_field(mapping.normalized, CASH_FLOW_AMOUNT_ALIASES)
    if amount_field is None:
        return None
    has_explicit_amount = amount_field.canonical_name not in {"amount"}
    has_cash_flow_intent = (
        first_field(mapping.normalized, CASH_FLOW_DIRECTION_ALIASES) is not None
        or first_field(mapping.normalized, CASH_FLOW_CATEGORY_ALIASES) is not None
        or first_field(mapping.normalized, CASH_FLOW_DATE_ALIASES) is not None
        or amount_field.canonical_name in {"inflow", "outflow"}
    )
    if has_explicit_amount or has_cash_flow_intent:
        return amount_field
    return None


def has_cash_flow_mapping_intent(mapping: RowMappingContext) -> bool:
    return first_field(mapping.normalized, CASH_FLOW_AMOUNT_ALIASES) is not None and (
        first_field(mapping.normalized, CASH_FLOW_DIRECTION_ALIASES) is not None
        or first_field(mapping.normalized, CASH_FLOW_CATEGORY_ALIASES) is not None
        or first_field(mapping.normalized, CASH_FLOW_DATE_ALIASES) is not None
    )


def cash_flow_direction_from_fields(
    amount_field: FieldValue,
    direction_field: FieldValue | None,
    amount: Decimal,
) -> str | None:
    if amount_field.canonical_name == "inflow":
        return "inflow"
    if amount_field.canonical_name == "outflow":
        return "outflow"
    if direction_field is not None:
        return normalize_direction(direction_field.value)
    if amount < 0:
        return "outflow"
    return None


def map_obligation(
    mapping: RowMappingContext,
    institution: FinancialInstitution | None,
    account: FinancialAccount | None,
    reporting_period: FinancialReportingPeriod | None,
) -> FinancialObligation | None:
    obligation_fields = [
        field
        for field in (
            first_field(mapping.normalized, (alias,)) for alias in OBLIGATION_AMOUNT_ALIASES
        )
        if field is not None and parse_decimal(field.value) is not None
    ]
    if not obligation_fields:
        return None

    principal = first_decimal(mapping.normalized, ("principal", "committed"))
    outstanding = first_decimal(mapping.normalized, ("outstanding", "drawn"))
    obligation, created = get_or_create_obligation(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        source_row_id=mapping.source_row.id,
        institution_id=institution.id if institution is not None else None,
        account_id=account.id if account is not None else None,
        reporting_period_id=reporting_period.id if reporting_period is not None else None,
        principal_amount=principal.value if principal is not None else None,
        outstanding_amount=outstanding.value if outstanding is not None else None,
        currency=mapping.currency,
        details=mapping.metadata(),
    )
    mapping.count_record(created, "obligations")
    for obligation_field in obligation_fields:
        target_field = (
            "principal_amount"
            if obligation_field.canonical_name in {"principal", "committed"}
            else "outstanding_amount"
        )
        mapping.link(
            record_table="financial_obligations",
            record_id=obligation.id,
            field_name=target_field,
            source_field=obligation_field.source_field,
        )
    link_currency(mapping, "financial_obligations", obligation.id)
    return obligation


def map_covenant(
    mapping: RowMappingContext,
    obligation: FinancialObligation | None,
    reporting_period: FinancialReportingPeriod | None,
) -> FinancialCovenant | None:
    name = first_field(mapping.normalized, COVENANT_NAME_ALIASES)
    metric = first_field(mapping.normalized, COVENANT_METRIC_ALIASES)
    operator_field = first_field(mapping.normalized, COVENANT_OPERATOR_ALIASES)
    threshold_field = first_field(mapping.normalized, COVENANT_THRESHOLD_ALIASES)
    if any(field is None for field in (name, metric, operator_field, threshold_field)):
        return None
    assert name is not None
    assert metric is not None
    assert operator_field is not None
    assert threshold_field is not None
    threshold = parse_decimal(threshold_field.value)
    operator = normalize_covenant_operator(operator_field.value)
    if threshold is None or operator is None:
        return None
    actual_field = first_field(mapping.normalized, COVENANT_ACTUAL_ALIASES)
    actual = parse_decimal(actual_field.value) if actual_field is not None else None
    status_field = first_field(mapping.normalized, COVENANT_STATUS_ALIASES)
    compliance = normalized_covenant_status(status_field.value) if status_field else None
    if compliance is None:
        compliance = computed_covenant_status(operator, threshold, actual)
    covenant, created = get_or_create_covenant(
        mapping.db,
        mapping.tenant,
        mapping.case_id,
        source_row_id=mapping.source_row.id,
        obligation_id=obligation.id if obligation is not None else None,
        reporting_period_id=reporting_period.id if reporting_period is not None else None,
        name=string_value(name.value) or "",
        metric=string_value(metric.value) or "",
        operator=operator,
        threshold=threshold,
        actual_value=actual,
        compliance_status=compliance,
        source_record=mapping.row.payload,
        reporting_context=mapping.metadata(),
        metadata=mapping.metadata(),
    )
    mapping.count_record(created, "covenants")
    for field_name, field in (
        ("name", name),
        ("metric", metric),
        ("operator", operator_field),
        ("threshold", threshold_field),
        ("actual_value", actual_field),
        ("compliance_status", status_field),
    ):
        if field is not None:
            mapping.link(
                record_table="financial_covenants",
                record_id=covenant.id,
                field_name=field_name,
                source_field=field.source_field,
            )
    return covenant


def normalize_covenant_operator(value: object) -> str | None:
    aliases = {
        "<": "lt",
        "lt": "lt",
        "<=": "lte",
        "lte": "lte",
        "=": "eq",
        "==": "eq",
        "eq": "eq",
        ">=": "gte",
        "gte": "gte",
        ">": "gt",
        "gt": "gt",
    }
    return aliases.get(str(value).strip().lower())


def normalized_covenant_status(value: object) -> str | None:
    normalized = normalize_text(value).replace("-", "_").replace(" ", "_")
    return normalized if normalized in {"compliant", "non_compliant", "unknown"} else None


def computed_covenant_status(operator: str, threshold: Decimal, actual: Decimal | None) -> str:
    if actual is None:
        return "unknown"
    comparisons = {
        "lt": actual < threshold,
        "lte": actual <= threshold,
        "eq": actual == threshold,
        "gte": actual >= threshold,
        "gt": actual > threshold,
    }
    return "compliant" if comparisons[operator] else "non_compliant"


def link_currency(
    mapping: RowMappingContext,
    record_table: RecordTable,
    record_id: UUID,
) -> None:
    if mapping.currency_field is None:
        return
    mapping.link(
        record_table=record_table,
        record_id=record_id,
        field_name="currency",
        source_field=mapping.currency_field.source_field,
    )
