from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
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
    FinancialValidationIssue,
)
from app.schemas.common import JsonObject
from app.schemas.financial_workspace import (
    ENTITY_TYPE_BY_TABLE,
    FinancialValidationEntityType,
    FinancialValidationIssueRead,
    FinancialValidationRunResponse,
    FinancialValidationSeverity,
    FinancialValidationSummaryRead,
)
from app.services.cases import get_case_or_404
from app.services.financial_validation_rules import (
    FinancialValidationDataset,
    IssueDraft,
    RecordTable,
    evaluate_financial_validation,
)

TABLE_BY_ENTITY_TYPE: dict[FinancialValidationEntityType, RecordTable] = {
    "institution": "financial_institutions",
    "account": "financial_accounts",
    "reporting_period": "financial_reporting_periods",
    "balance": "financial_balances",
    "cash_flow": "financial_cash_flows",
    "obligation": "financial_obligations",
    "covenant": "financial_covenants",
}


def validate_financial_data(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    commit: bool = True,
) -> FinancialValidationRunResponse:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    drafts = evaluate_financial_validation(load_validation_dataset(db, ctx, case.id))

    db.execute(
        delete(FinancialValidationIssue).where(
            FinancialValidationIssue.organization_id == ctx.organization_id,
            FinancialValidationIssue.case_id == case.id,
        )
    )
    db.add_all(
        [
            FinancialValidationIssue(
                organization_id=ctx.organization_id,
                case_id=case.id,
                record_table=draft.record_table,
                record_id=draft.record_id,
                issue_key=issue_key(draft),
                field_name=draft.field or "",
                severity=draft.severity,
                status="open",
                rule_id=draft.rule_id,
                message=draft.message,
                details=issue_details(draft),
            )
            for draft in drafts
        ]
    )
    if commit:
        db.commit()
    else:
        db.flush()

    issues = validation_issue_reads(list_validation_issue_models(db, ctx, case.id))
    return FinancialValidationRunResponse(
        case_id=case.id,
        organization_id=case.organization_id,
        issue_count=len(issues),
        summary=summarize_validation_issues(issues),
        issues=issues,
    )


def list_validation_issues(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    severity: FinancialValidationSeverity | None = None,
    entity_type: FinancialValidationEntityType | None = None,
) -> list[FinancialValidationIssueRead]:
    return validation_issue_reads(
        list_validation_issue_models(
            db,
            ctx,
            case_id,
            severity=severity,
            entity_type=entity_type,
        )
    )


def list_validation_issue_models(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
    *,
    severity: FinancialValidationSeverity | None = None,
    entity_type: FinancialValidationEntityType | None = None,
) -> list[FinancialValidationIssue]:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    stmt = select(FinancialValidationIssue).where(
        FinancialValidationIssue.organization_id == ctx.organization_id,
        FinancialValidationIssue.case_id == case.id,
    )
    if severity is not None:
        stmt = stmt.where(FinancialValidationIssue.severity == severity)
    if entity_type is not None:
        stmt = stmt.where(
            FinancialValidationIssue.record_table == TABLE_BY_ENTITY_TYPE[entity_type]
        )
    return list(
        db.scalars(
            stmt.order_by(
                FinancialValidationIssue.created_at.asc(),
                FinancialValidationIssue.id.asc(),
            )
        )
    )


def summarize_validation_issues(
    issues: Sequence[FinancialValidationIssue | FinancialValidationIssueRead],
) -> FinancialValidationSummaryRead:
    summary = FinancialValidationSummaryRead(total=len(issues))
    for issue in issues:
        if issue.severity == "error":
            summary.error += 1
        elif issue.severity == "warning":
            summary.warning += 1
        elif issue.severity == "info":
            summary.info += 1
    return summary


def load_validation_dataset(
    db: Session,
    ctx: TenantContext,
    case_id: UUID,
) -> FinancialValidationDataset:
    return FinancialValidationDataset(
        institutions=list(db.scalars(financial_stmt(FinancialInstitution, ctx, case_id))),
        accounts=list(db.scalars(financial_stmt(FinancialAccount, ctx, case_id))),
        periods=list(db.scalars(financial_stmt(FinancialReportingPeriod, ctx, case_id))),
        balances=list(db.scalars(financial_stmt(FinancialBalance, ctx, case_id))),
        cash_flows=list(db.scalars(financial_stmt(FinancialCashFlow, ctx, case_id))),
        obligations=list(db.scalars(financial_stmt(FinancialObligation, ctx, case_id))),
        covenants=list(db.scalars(financial_stmt(FinancialCovenant, ctx, case_id))),
        links=list(db.scalars(financial_stmt(FinancialRecordSourceLink, ctx, case_id))),
        manual_edits=list(db.scalars(financial_stmt(FinancialManualEditHistory, ctx, case_id))),
    )


def financial_stmt(model: type, ctx: TenantContext, case_id: UUID) -> Select:
    return select(model).where(
        model.organization_id == ctx.organization_id,
        model.case_id == case_id,
    )


def issue_details(draft: IssueDraft) -> JsonObject:
    return {
        "entity_type": ENTITY_TYPE_BY_TABLE[draft.record_table],
        "issue_key": issue_key(draft),
        **({"field": draft.field} if draft.field is not None else {}),
        **draft.details,
    }


def issue_key(draft: IssueDraft) -> str:
    payload = "|".join(
        [
            draft.record_table,
            str(draft.record_id),
            draft.rule_id,
            draft.field or "",
        ]
    )
    return f"{draft.rule_id}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def validation_issue_reads(
    issues: list[FinancialValidationIssue],
) -> list[FinancialValidationIssueRead]:
    return [FinancialValidationIssueRead.model_validate(issue) for issue in issues]
