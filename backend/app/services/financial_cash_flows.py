from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import (
    FinancialAccount,
    FinancialCashFlow,
    FinancialReportingPeriod,
    FinancialValidationIssue,
)
from app.schemas.common import JsonObject
from app.schemas.financial_workspace import (
    FinancialCashFlowCreate,
    FinancialCashFlowMutationResponse,
    FinancialCashFlowUpdate,
)
from app.services.financial_canonical_edits import (
    bad_request,
    create_record,
    manual_metadata,
    payload_values,
    update_record,
    validate_link,
)
from app.services.financial_mapping.normalization import (
    normalize_currency,
    normalize_text,
    parse_decimal,
    string_value,
)
from app.services.financial_mapping.upserts import canonical_dedupe_key

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
) -> FinancialCashFlowMutationResponse:
    values, reason = payload_values(payload, exclude_unset=True)
    normalized = validated_cash_flow_values(
        amount=values["amount"],
        direction=values["direction"],
        category=values["category"],
        currency=values.get("currency"),
    )
    validate_links(db, ctx, case_id, values)
    values.update(
        amount=normalized.amount,
        currency=normalized.currency,
        direction=normalized.direction,
        category=normalized.category,
    )
    values["metadata_"] = manual_metadata(values.pop("metadata", {}), "manual")
    values["dedupe_key"] = cash_flow_dedupe(values)
    return cast(
        FinancialCashFlowMutationResponse,
        create_record(
            db,
            ctx,
            case_id,
            FinancialCashFlow,
            CASH_FLOW_RECORD_TABLE,
            values,
            reason,
            FinancialCashFlowMutationResponse,
        ),
    )


def update_cash_flow(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    cash_flow_id: UUID,
    payload: FinancialCashFlowUpdate,
) -> FinancialCashFlowMutationResponse:
    updates = payload.model_dump(exclude_unset=True)
    reason = updates.pop("reason")
    if "amount" in updates and updates["amount"] is None:
        bad_request("Cash-flow amount is required.")
    if "direction" in updates and updates["direction"] is None:
        bad_request("Cash-flow direction is required.")
    if "category" in updates and updates["category"] is None:
        bad_request("Cash-flow category is required.")
    validate_links(db, ctx, case_id, updates)
    if "amount" in updates:
        amount = parse_decimal(updates["amount"])
        if amount is None or amount <= 0:
            bad_request("Cash-flow amount must be greater than zero.")
        updates["amount"] = amount
    if "currency" in updates and updates["currency"] is not None:
        currency = normalize_currency(updates["currency"])
        if currency is None:
            bad_request("Cash-flow currency must be a 3-letter uppercase code.")
        updates["currency"] = currency
    if "category" in updates:
        category = normalize_category(updates["category"])
        if category is None:
            bad_request("Cash-flow category is required.")
        updates["category"] = category
    return cast(
        FinancialCashFlowMutationResponse,
        update_record(
            db,
            ctx,
            case_id,
            cash_flow_id,
            FinancialCashFlow,
            CASH_FLOW_RECORD_TABLE,
            updates,
            reason,
            cash_flow_dedupe,
            FinancialCashFlowMutationResponse,
        ),
    )


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


def validate_links(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    values: dict[str, Any],
) -> None:
    if "account_id" in values:
        validate_link(db, ctx, case_id, FinancialAccount, values["account_id"], "Account")
    if "reporting_period_id" in values:
        validate_link(
            db,
            ctx,
            case_id,
            FinancialReportingPeriod,
            values["reporting_period_id"],
            "Reporting period",
        )


def cash_flow_dedupe(values: dict[str, Any]) -> str:
    return canonical_dedupe_key(
        "cash_flow",
        [
            values.get("account_id"),
            values.get("reporting_period_id"),
            values.get("cash_flow_date"),
            values.get("direction"),
            normalize_text(values.get("category")),
            values.get("amount"),
            values.get("currency"),
        ],
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
