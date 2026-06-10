from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    FinancialAccount,
    FinancialCashFlow,
    FinancialManualEditHistory,
    FinancialReportingPeriod,
    FinancialValidationIssue,
)
from app.schemas.common import JsonObject
from app.schemas.financial_workspace import FinancialCashFlowCreate, FinancialCashFlowUpdate
from app.services.cases import get_case_or_404
from app.services.financial_mapping.normalization import (
    normalize_currency,
    normalize_text,
    parse_decimal,
    string_value,
)
from app.services.financial_mapping.upserts import canonical_dedupe_key, get_or_create_cash_flow

VALID_DIRECTIONS = {"inflow", "outflow"}
CASH_FLOW_RECORD_TABLE = "financial_cash_flows"


@dataclass(frozen=True)
class CashFlowValidationRule:
    rule_id: str
    severity: str
    field_name: str
    message: str
    details: JsonObject


cash_flow_missing_currency = CashFlowValidationRule(
    rule_id="cash_flow_missing_currency",
    severity="warning",
    field_name="currency",
    message="Cash-flow currency should be confirmed from source data.",
    details={"field": "currency"},
)
cash_flow_missing_period_or_date = CashFlowValidationRule(
    rule_id="cash_flow_missing_period_or_date",
    severity="warning",
    field_name="cash_flow_date",
    message="Cash flow should have either a cash-flow date or reporting period.",
    details={"fields": ["cash_flow_date", "reporting_period_id"]},
)
CASH_FLOW_REVIEW_RULES = (
    cash_flow_missing_currency,
    cash_flow_missing_period_or_date,
)
CASH_FLOW_REVIEW_RULE_IDS = {rule.rule_id for rule in CASH_FLOW_REVIEW_RULES}


def create_cash_flow(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    payload: FinancialCashFlowCreate,
) -> FinancialCashFlow:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    values = validated_cash_flow_values(
        amount=payload.amount,
        direction=payload.direction,
        category=payload.category,
        currency=payload.currency,
    )
    validate_optional_links(
        db,
        ctx,
        case.id,
        account_id=payload.account_id,
        reporting_period_id=payload.reporting_period_id,
    )
    cash_flow, _created = get_or_create_cash_flow(
        db,
        ctx,
        case.id,
        account_id=payload.account_id,
        reporting_period_id=payload.reporting_period_id,
        cash_flow_date=payload.cash_flow_date,
        amount=values.amount,
        currency=values.currency,
        direction=values.direction,
        category=values.category,
        metadata=payload.metadata,
    )
    reconcile_cash_flow_review_issues(db, ctx, cash_flow)
    db.commit()
    db.refresh(cash_flow)
    return cash_flow


def update_cash_flow(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    cash_flow_id: UUID,
    payload: FinancialCashFlowUpdate,
) -> FinancialCashFlow:
    get_case_or_404(db, ctx.organization_id, case_id)
    cash_flow = get_cash_flow_or_404(db, ctx, case_id, cash_flow_id)

    updates = payload.model_dump(exclude_unset=True)
    reason = updates.pop("reason", None)
    if "amount" in updates and updates["amount"] is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow amount is required.",
        )
    if "direction" in updates and updates["direction"] is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow direction is required.",
        )
    if "category" in updates and updates["category"] is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow category is required.",
        )

    next_amount = updates.get("amount", cash_flow.amount)
    next_direction = updates.get("direction", cash_flow.direction)
    next_category = updates.get("category", cash_flow.category)
    next_currency = updates.get("currency", cash_flow.currency)
    values = validated_cash_flow_values(
        amount=next_amount,
        direction=next_direction,
        category=next_category,
        currency=next_currency,
    )
    account_id = updates.get("account_id", cash_flow.account_id)
    reporting_period_id = updates.get("reporting_period_id", cash_flow.reporting_period_id)
    validate_optional_links(
        db,
        ctx,
        case_id,
        account_id=account_id,
        reporting_period_id=reporting_period_id,
    )

    normalized_updates = {
        **updates,
        "amount": values.amount,
        "currency": values.currency,
        "direction": values.direction,
        "category": values.category,
    }
    for field_name, new_value in normalized_updates.items():
        model_field_name = "metadata_" if field_name == "metadata" else field_name
        normalized_new_value = {} if field_name == "metadata" and new_value is None else new_value
        previous_value = getattr(cash_flow, model_field_name)
        if previous_value == normalized_new_value:
            continue
        setattr(cash_flow, model_field_name, normalized_new_value)
        db.add(
            FinancialManualEditHistory(
                organization_id=ctx.organization_id,
                case_id=case_id,
                record_table=CASH_FLOW_RECORD_TABLE,
                record_id=cash_flow.id,
                field_name=field_name,
                previous_value=jsonable_encoder(previous_value),
                new_value=jsonable_encoder(normalized_new_value),
                edited_by=ctx.actor_user_id,
                reason=reason,
            )
        )

    cash_flow.dedupe_key = canonical_dedupe_key(
        "cash_flow",
        [
            cash_flow.account_id,
            cash_flow.reporting_period_id,
            cash_flow.cash_flow_date,
            cash_flow.direction,
            cash_flow.category,
            cash_flow.amount,
            cash_flow.currency,
        ],
    )
    reconcile_cash_flow_review_issues(db, ctx, cash_flow)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cash-flow correction conflicts with an existing record.",
        ) from exc
    db.refresh(cash_flow)
    return cash_flow


def get_cash_flow_or_404(
    db: Session, ctx: TenantContext, case_id: UUID, cash_flow_id: UUID
) -> FinancialCashFlow:
    cash_flow = db.scalar(
        select(FinancialCashFlow).where(
            FinancialCashFlow.id == cash_flow_id,
            FinancialCashFlow.organization_id == ctx.organization_id,
            FinancialCashFlow.case_id == case_id,
        )
    )
    if cash_flow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cash flow not found.",
        )
    return cash_flow


@dataclass(frozen=True)
class ValidatedCashFlowValues:
    amount: Decimal
    direction: str
    category: str
    currency: str | None


def validated_cash_flow_values(
    *,
    amount: object,
    direction: object,
    category: object,
    currency: object | None,
) -> ValidatedCashFlowValues:
    parsed_amount = parse_decimal(amount)
    if parsed_amount is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow amount must be a valid number.",
        )
    if parsed_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow amount must be greater than zero.",
        )
    normalized_direction = normalize_direction(direction)
    if normalized_direction is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow direction must be inflow or outflow.",
        )
    normalized_category = normalize_category(category)
    if normalized_category is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow category is required.",
        )
    normalized_currency = normalize_currency(currency) if currency is not None else None
    if currency is not None and normalized_currency is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash-flow currency must be a 3-letter uppercase code.",
        )
    return ValidatedCashFlowValues(
        amount=parsed_amount,
        direction=normalized_direction,
        category=normalized_category,
        currency=normalized_currency,
    )


def normalize_direction(value: object) -> str | None:
    text = string_value(value)
    if text is None:
        return None
    normalized = normalize_text(text).replace(" ", "_")
    if normalized in {"in", "credit", "in_flow"}:
        normalized = "inflow"
    if normalized in {"out", "debit", "out_flow"}:
        normalized = "outflow"
    return normalized if normalized in VALID_DIRECTIONS else None


def normalize_category(value: object) -> str | None:
    text = string_value(value)
    if text is None:
        return None
    normalized = normalize_text(text)
    return normalized or None


def validate_optional_links(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    account_id: UUID | None,
    reporting_period_id: UUID | None,
) -> None:
    if account_id is not None:
        account = db.scalar(
            select(FinancialAccount.id).where(
                FinancialAccount.id == account_id,
                FinancialAccount.organization_id == ctx.organization_id,
                FinancialAccount.case_id == case_id,
            )
        )
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if reporting_period_id is not None:
        reporting_period = db.scalar(
            select(FinancialReportingPeriod.id).where(
                FinancialReportingPeriod.id == reporting_period_id,
                FinancialReportingPeriod.organization_id == ctx.organization_id,
                FinancialReportingPeriod.case_id == case_id,
            )
        )
        if reporting_period is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reporting period not found.",
            )


def reconcile_cash_flow_review_issues(
    db: Session,
    ctx: TenantContext,
    cash_flow: FinancialCashFlow,
) -> None:
    violated_rules = {
        rule.rule_id: rule
        for rule in cash_flow_review_rules(
            currency=cash_flow.currency,
            cash_flow_date=cash_flow.cash_flow_date,
            reporting_period_id=cash_flow.reporting_period_id,
        )
    }
    open_issues = list(
        db.scalars(
            select(FinancialValidationIssue).where(
                FinancialValidationIssue.organization_id == ctx.organization_id,
                FinancialValidationIssue.case_id == cash_flow.case_id,
                FinancialValidationIssue.record_table == CASH_FLOW_RECORD_TABLE,
                FinancialValidationIssue.record_id == cash_flow.id,
                FinancialValidationIssue.rule_id.in_(CASH_FLOW_REVIEW_RULE_IDS),
                FinancialValidationIssue.status == "open",
            )
        )
    )
    open_by_rule = {(issue.rule_id, issue.field_name): issue for issue in open_issues}

    for rule_id, rule in violated_rules.items():
        if (rule_id, rule.field_name) in open_by_rule:
            continue
        db.add(
            FinancialValidationIssue(
                organization_id=ctx.organization_id,
                case_id=cash_flow.case_id,
                record_table=CASH_FLOW_RECORD_TABLE,
                record_id=cash_flow.id,
                issue_key=f"{rule.rule_id}:{cash_flow.id.hex[:32]}",
                field_name=rule.field_name,
                severity=rule.severity,
                status="open",
                rule_id=rule.rule_id,
                message=rule.message,
                details=rule.details,
            )
        )

    now = utc_now()
    for issue in open_issues:
        if issue.rule_id not in violated_rules:
            issue.status = "resolved"
            issue.resolved_at = now


def cash_flow_review_rules(
    *,
    currency: str | None,
    cash_flow_date: date | None,
    reporting_period_id: UUID | None,
) -> list[CashFlowValidationRule]:
    rules: list[CashFlowValidationRule] = []
    if currency is None:
        rules.append(cash_flow_missing_currency)
    if cash_flow_date is None and reporting_period_id is None:
        rules.append(cash_flow_missing_period_or_date)
    return rules
