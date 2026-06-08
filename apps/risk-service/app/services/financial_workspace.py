from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
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
from app.schemas.financial_workspace import FinancialDataWorkspaceRead
from app.services.cases import get_case_or_404
from app.services.financial_validation import summarize_validation_issues


def get_financial_workspace(
    db: Session, ctx: TenantContext, case_id: UUID
) -> FinancialDataWorkspaceRead:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    validation_issues = list(
        db.scalars(financial_stmt(FinancialValidationIssue, ctx.organization_id, case_id))
    )
    return FinancialDataWorkspaceRead(
        case_id=case.id,
        organization_id=case.organization_id,
        institutions=list(
            db.scalars(financial_stmt(FinancialInstitution, ctx.organization_id, case_id))
        ),
        accounts=list(db.scalars(financial_stmt(FinancialAccount, ctx.organization_id, case_id))),
        reporting_periods=list(
            db.scalars(financial_stmt(FinancialReportingPeriod, ctx.organization_id, case_id))
        ),
        balances=list(db.scalars(financial_stmt(FinancialBalance, ctx.organization_id, case_id))),
        obligations=list(
            db.scalars(financial_stmt(FinancialObligation, ctx.organization_id, case_id))
        ),
        source_rows=list(
            db.scalars(financial_stmt(FinancialSourceRow, ctx.organization_id, case_id))
        ),
        record_source_links=list(
            db.scalars(financial_stmt(FinancialRecordSourceLink, ctx.organization_id, case_id))
        ),
        manual_edits=list(
            db.scalars(financial_stmt(FinancialManualEditHistory, ctx.organization_id, case_id))
        ),
        validation_issues=validation_issues,
        validation_summary=summarize_validation_issues(validation_issues),
    )


def financial_stmt(model: type, organization_id: UUID, case_id: UUID) -> Select:
    return (
        select(model)
        .where(model.organization_id == organization_id, model.case_id == case_id)
        .order_by(model.created_at.asc(), model.id.asc())
    )
