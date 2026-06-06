from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    FinancialAccount,
    FinancialInstitution,
    FinancialReportingPeriod,
    FinancialSourceRow,
)
from app.schemas.common import JsonObject
from app.services.financial_mapping.links import link_field, mapper_metadata
from app.services.financial_mapping.normalization import (
    first_decimal,
    first_field,
    normalize_currency,
    normalize_row,
    parse_date,
    parse_decimal,
    string_value,
)
from app.services.financial_mapping.types import (
    ACCOUNT_ALIASES,
    BALANCE_AMOUNT_ALIASES,
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

    if map_balance(mapping, account, reporting_period):
        mapped_record_count += 1

    if map_obligation(mapping, institution, account, reporting_period):
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


def map_obligation(
    mapping: RowMappingContext,
    institution: FinancialInstitution | None,
    account: FinancialAccount | None,
    reporting_period: FinancialReportingPeriod | None,
) -> bool:
    obligation_fields = [
        field
        for field in (
            first_field(mapping.normalized, (alias,)) for alias in OBLIGATION_AMOUNT_ALIASES
        )
        if field is not None and parse_decimal(field.value) is not None
    ]
    if not obligation_fields:
        return False

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
    return True


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
