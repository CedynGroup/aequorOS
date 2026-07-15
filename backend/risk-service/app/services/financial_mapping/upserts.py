from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    FinancialAccount,
    FinancialBalance,
    FinancialCashFlow,
    FinancialCovenant,
    FinancialInstitution,
    FinancialObligation,
    FinancialReportingPeriod,
    FinancialSourceRow,
)
from app.schemas.common import JsonObject
from app.services.financial_mapping.links import linked_record
from app.services.financial_mapping.normalization import normalize_text
from app.services.financial_mapping.types import ExtractedRow


def dedupe_value(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def canonical_dedupe_key(kind: str, parts: list[object | None]) -> str:
    payload = json.dumps(
        [dedupe_value(part) for part in parts],
        separators=(",", ":"),
        sort_keys=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


def canonical_account_dedupe_key(
    institution_id: UUID | None,
    account_number: str | None,
    account_name: str,
    currency: str | None,
) -> str:
    parts: list[object | None] = [institution_id]
    normalized_account_number = normalize_text(account_number)
    if normalized_account_number:
        parts.append(normalized_account_number)
    parts.extend([normalize_text(account_name), currency])
    return canonical_dedupe_key(
        "account",
        parts,
    )


def existing_by_dedupe[
    T: (
        FinancialAccount,
        FinancialBalance,
        FinancialCashFlow,
        FinancialCovenant,
        FinancialInstitution,
        FinancialObligation,
        FinancialReportingPeriod,
    )
](
    db: Session,
    model: type[T],
    ctx: TenantContext,
    case_id: UUID,
    dedupe_key: str,
) -> T | None:
    return db.scalar(
        select(model).where(
            model.organization_id == ctx.organization_id,
            model.case_id == case_id,
            model.dedupe_key == dedupe_key,
        )
    )


def add_or_get_by_dedupe[  # noqa: PLR0913
    T: (
        FinancialAccount,
        FinancialBalance,
        FinancialCashFlow,
        FinancialCovenant,
        FinancialInstitution,
        FinancialObligation,
        FinancialReportingPeriod,
    )
](
    db: Session,
    record: T,
    model: type[T],
    ctx: TenantContext,
    case_id: UUID,
    dedupe_key: str,
) -> tuple[T, bool]:
    existing = existing_by_dedupe(db, model, ctx, case_id, dedupe_key)
    if existing is not None:
        return existing, False

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        with db.no_autoflush:
            existing = existing_by_dedupe(db, model, ctx, case_id, dedupe_key)
        if existing is None:
            raise
        return existing, False

    return record, True


def existing_source_row(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    document_extraction_id: UUID,
    row_index: int,
) -> FinancialSourceRow | None:
    return db.scalar(
        select(FinancialSourceRow).where(
            FinancialSourceRow.organization_id == ctx.organization_id,
            FinancialSourceRow.case_id == case_id,
            FinancialSourceRow.document_extraction_id == document_extraction_id,
            FinancialSourceRow.row_index == row_index,
        )
    )


def get_or_create_source_row(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    document_id: UUID,
    document_extraction_id: UUID,
    row: ExtractedRow,
) -> tuple[FinancialSourceRow, bool]:
    existing = existing_source_row(db, ctx, case_id, document_extraction_id, row.index)
    if existing is not None:
        return existing, False

    source_row = FinancialSourceRow(
        organization_id=ctx.organization_id,
        case_id=case_id,
        document_id=document_id,
        document_extraction_id=document_extraction_id,
        row_index=row.index,
        locator=row.locator,
        raw_payload=row.payload,
    )
    try:
        with db.begin_nested():
            db.add(source_row)
            db.flush()
    except IntegrityError:
        with db.no_autoflush:
            existing = existing_source_row(db, ctx, case_id, document_extraction_id, row.index)
        if existing is None:
            raise
        return existing, False

    return source_row, True


def get_or_create_institution(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    name: str,
    metadata: JsonObject,
) -> tuple[FinancialInstitution, bool]:
    normalized_name = normalize_text(name)
    dedupe_key = canonical_dedupe_key("institution", [normalized_name])

    institution = FinancialInstitution(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        name=name,
        institution_type="financial_institution",
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(db, institution, FinancialInstitution, ctx, case_id, dedupe_key)


def get_or_create_account(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    institution_id: UUID | None,
    account_name: str,
    account_type: str | None,
    currency: str | None,
    metadata: JsonObject,
) -> tuple[FinancialAccount, bool]:
    dedupe_key = canonical_account_dedupe_key(institution_id, None, account_name, currency)

    account = FinancialAccount(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        institution_id=institution_id,
        account_name=account_name,
        account_type=account_type,
        currency=currency,
        status="unknown",
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(db, account, FinancialAccount, ctx, case_id, dedupe_key)


def get_or_create_reporting_period(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    period_type: str,
    start_date: date | None,
    end_date: date | None,
    as_of_date: date | None,
    label: str | None,
    metadata: JsonObject,
) -> tuple[FinancialReportingPeriod, bool]:
    normalized_label = normalize_text(label)
    dedupe_key = canonical_dedupe_key(
        "reporting_period",
        [period_type, start_date, end_date, as_of_date, normalized_label],
    )

    reporting_period = FinancialReportingPeriod(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
        label=label,
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(
        db,
        reporting_period,
        FinancialReportingPeriod,
        ctx,
        case_id,
        dedupe_key,
    )


def get_or_create_balance(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    source_row_id: UUID,
    account_id: UUID | None,
    reporting_period_id: UUID | None,
    balance_type: str,
    amount: Decimal,
    currency: str | None,
    as_of_date: date | None,
    metadata: JsonObject,
) -> tuple[FinancialBalance, bool]:
    linked = linked_record(db, ctx, case_id, source_row_id, "financial_balances")
    if isinstance(linked, FinancialBalance):
        return linked, False

    dedupe_key = canonical_dedupe_key(
        "balance",
        [account_id, reporting_period_id, balance_type, amount, currency, as_of_date],
    )

    balance = FinancialBalance(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        account_id=account_id,
        reporting_period_id=reporting_period_id,
        balance_type=balance_type,
        amount=amount,
        currency=currency,
        as_of_date=as_of_date,
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(db, balance, FinancialBalance, ctx, case_id, dedupe_key)


def get_or_create_cash_flow(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    source_row_id: UUID | None = None,
    account_id: UUID | None,
    reporting_period_id: UUID | None,
    cash_flow_date: date | None,
    amount: Decimal,
    currency: str | None,
    direction: str,
    category: str,
    metadata: JsonObject,
) -> tuple[FinancialCashFlow, bool]:
    if source_row_id is not None:
        linked = linked_record(db, ctx, case_id, source_row_id, "financial_cash_flows")
        if isinstance(linked, FinancialCashFlow):
            return linked, False

    dedupe_key = canonical_dedupe_key(
        "cash_flow",
        [
            account_id,
            reporting_period_id,
            cash_flow_date,
            direction,
            category,
            amount,
            currency,
        ],
    )

    cash_flow = FinancialCashFlow(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        account_id=account_id,
        reporting_period_id=reporting_period_id,
        cash_flow_date=cash_flow_date,
        amount=amount,
        currency=currency,
        direction=direction,
        category=category,
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(db, cash_flow, FinancialCashFlow, ctx, case_id, dedupe_key)


def get_or_create_obligation(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    source_row_id: UUID,
    institution_id: UUID | None,
    account_id: UUID | None,
    reporting_period_id: UUID | None,
    principal_amount: Decimal | None,
    outstanding_amount: Decimal | None,
    currency: str | None,
    details: JsonObject,
) -> tuple[FinancialObligation, bool]:
    linked = linked_record(db, ctx, case_id, source_row_id, "financial_obligations")
    if isinstance(linked, FinancialObligation):
        return linked, False

    dedupe_key = canonical_dedupe_key(
        "obligation",
        [
            institution_id,
            account_id,
            reporting_period_id,
            "facility",
            principal_amount,
            outstanding_amount,
            currency,
        ],
    )

    obligation = FinancialObligation(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        institution_id=institution_id,
        account_id=account_id,
        reporting_period_id=reporting_period_id,
        obligation_type="facility",
        facility_type="credit_facility",
        principal_amount=principal_amount,
        outstanding_amount=outstanding_amount,
        currency=currency,
        status="unknown",
        details=details,
    )
    return add_or_get_by_dedupe(
        db,
        obligation,
        FinancialObligation,
        ctx,
        case_id,
        dedupe_key,
    )


def get_or_create_covenant(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    source_row_id: UUID,
    obligation_id: UUID | None,
    reporting_period_id: UUID | None,
    name: str,
    metric: str,
    operator: str,
    threshold: Decimal,
    actual_value: Decimal | None,
    compliance_status: str,
    source_record: JsonObject,
    reporting_context: JsonObject,
    metadata: JsonObject,
) -> tuple[FinancialCovenant, bool]:
    linked = linked_record(db, ctx, case_id, source_row_id, "financial_covenants")
    if isinstance(linked, FinancialCovenant):
        return linked, False
    dedupe_key = canonical_dedupe_key(
        "covenant",
        [
            obligation_id,
            reporting_period_id,
            normalize_text(name),
            normalize_text(metric),
            operator,
            threshold,
        ],
    )
    covenant = FinancialCovenant(
        organization_id=ctx.organization_id,
        case_id=case_id,
        dedupe_key=dedupe_key,
        obligation_id=obligation_id,
        reporting_period_id=reporting_period_id,
        name=name,
        metric=normalize_text(metric),
        operator=operator,
        threshold=threshold,
        actual_value=actual_value,
        compliance_status=compliance_status,
        source_record=source_record,
        reporting_context=reporting_context,
        metadata_=metadata,
    )
    return add_or_get_by_dedupe(db, covenant, FinancialCovenant, ctx, case_id, dedupe_key)
