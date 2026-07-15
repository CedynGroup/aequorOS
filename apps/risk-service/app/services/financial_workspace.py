from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
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
    FinancialSourceRow,
    FinancialValidationIssue,
)
from app.schemas.financial_workspace import FinancialDataWorkspaceRead, FinancialManualEditRead
from app.services.cases import get_case_or_404, user_display_names
from app.services.financial_validation import summarize_validation_issues


def get_financial_workspace(
    db: Session, ctx: TenantContext, case_id: UUID
) -> FinancialDataWorkspaceRead:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    validation_issues = list(
        db.scalars(financial_stmt(FinancialValidationIssue, ctx.organization_id, case_id))
    )
    manual_edits = list(
        db.scalars(financial_stmt(FinancialManualEditHistory, ctx.organization_id, case_id))
    )
    editor_ids = {edit.edited_by for edit in manual_edits if edit.edited_by is not None}
    editor_names = user_display_names(db, ctx.organization_id, editor_ids)
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
        cash_flows=list(
            db.scalars(financial_stmt(FinancialCashFlow, ctx.organization_id, case_id))
        ),
        obligations=list(
            db.scalars(financial_stmt(FinancialObligation, ctx.organization_id, case_id))
        ),
        covenants=list(db.scalars(financial_stmt(FinancialCovenant, ctx.organization_id, case_id))),
        source_rows=list(
            db.scalars(financial_stmt(FinancialSourceRow, ctx.organization_id, case_id))
        ),
        record_source_links=list(
            db.scalars(financial_stmt(FinancialRecordSourceLink, ctx.organization_id, case_id))
        ),
        manual_edits=[
            FinancialManualEditRead.model_validate(edit).model_copy(
                update={"edited_by_display_name": editor_names.get(edit.edited_by)}
            )
            for edit in manual_edits
        ],
        validation_issues=validation_issues,
        validation_summary=summarize_validation_issues(validation_issues),
    )


def financial_stmt(model: type, organization_id: UUID, case_id: UUID) -> Select:
    return (
        select(model)
        .where(model.organization_id == organization_id, model.case_id == case_id)
        .order_by(model.created_at.asc(), model.id.asc())
    )
