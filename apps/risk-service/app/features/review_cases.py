from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse

from app.api.deps import DbSession, Tenant
from app.domain.risk_constants import (
    CASE_DECISIONS,
    CASE_SORT_OPTIONS,
    CASE_STATUSES,
    RISK_LEVELS,
    CaseDecision,
    CaseSort,
    CaseStatus,
    RiskLevel,
)
from app.models import RiskCase, RiskCaseDecision, RiskScore
from app.schemas.cases import (
    CaseAssign,
    CaseCreate,
    CaseDecisionCreate,
    CaseDecisionRead,
    CaseListRead,
    CaseRead,
    CaseSummaryRead,
    CaseTaxonomyRead,
    CaseUpdate,
    ScoreRead,
)
from app.services import cases as cases_service
from app.services import reports as reports_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", response_model=CaseRead, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.create_case(db, ctx, payload.to_command())


@router.get("", response_model=CaseListRead)
def list_cases(  # noqa: PLR0913
    db: DbSession,
    ctx: Tenant,
    include_archived: Annotated[
        bool,
        Query(description="Include archived cases in the queue response."),
    ] = False,
    status: Annotated[
        CaseStatus | None,
        Query(description="Filter cases by workflow status."),
    ] = None,
    assigned_to_user_id: UUID | None = None,
    decision: Annotated[
        CaseDecision | None,
        Query(description="Filter cases by recorded decision."),
    ] = None,
    risk_level: Annotated[
        RiskLevel | None,
        Query(description="Filter cases by deterministic risk level."),
    ] = None,
    q: Annotated[
        str | None,
        Query(description="Case-insensitive search over title, subject, and description."),
    ] = None,
    sort: Annotated[
        CaseSort,
        Query(description="Server-side case queue sort order."),
    ] = CaseSort.CREATED_AT_DESC,
    limit: Annotated[
        int,
        Query(ge=1, le=200, description="Maximum number of cases to return."),
    ] = 50,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of cases to skip before returning results."),
    ] = 0,
) -> CaseListRead:
    result = cases_service.list_cases(
        db,
        ctx,
        cases_service.CaseFilters(
            include_archived=include_archived,
            status=status.value if status is not None else None,
            assigned_to_user_id=assigned_to_user_id,
            decision=decision.value if decision is not None else None,
            risk_level=risk_level.value if risk_level is not None else None,
            q=q,
            sort=sort.value,
            limit=limit,
            offset=offset,
        ),
    )
    return CaseListRead.from_result(result)


@router.get("/summary", response_model=CaseSummaryRead)
def case_summary(db: DbSession, ctx: Tenant) -> CaseSummaryRead:
    return CaseSummaryRead(**cases_service.case_summary(db, ctx).__dict__)


@router.get("/taxonomy", response_model=CaseTaxonomyRead)
def case_taxonomy() -> CaseTaxonomyRead:
    return CaseTaxonomyRead(
        statuses=sorted(CASE_STATUSES),
        decisions=sorted(CASE_DECISIONS),
        risk_levels=sorted(RISK_LEVELS),
        sort_options=sorted(CASE_SORT_OPTIONS),
    )


@router.get("/{case_id}", response_model=CaseRead)
def get_case(case_id: UUID, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.get_case_or_404(db, ctx.organization_id, case_id)


@router.patch("/{case_id}", response_model=CaseRead)
def update_case(case_id: UUID, payload: CaseUpdate, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.update_case(db, ctx, case_id, payload.to_command())


@router.post("/{case_id}/assign", response_model=CaseRead)
def assign_case(case_id: UUID, payload: CaseAssign, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.assign_case(db, ctx, case_id, payload.assigned_to_user_id)


@router.post("/{case_id}/decision", response_model=CaseDecisionRead)
def decide_case(
    case_id: UUID, payload: CaseDecisionCreate, db: DbSession, ctx: Tenant
) -> RiskCaseDecision:
    return cases_service.decide_case(db, ctx, case_id, payload.to_command())


@router.get("/{case_id}/decisions", response_model=list[CaseDecisionRead])
def list_case_decisions(case_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskCaseDecision]:
    return cases_service.list_case_decisions(db, ctx, case_id)


@router.get("/{case_id}/scores", response_model=list[ScoreRead])
def list_case_scores(case_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskScore]:
    return cases_service.list_case_scores(db, ctx, case_id)


@router.get("/{case_id}/report.json", response_model=reports_service.RiskReportPayload)
def case_report_json(
    case_id: UUID, db: DbSession, ctx: Tenant
) -> reports_service.RiskReportPayload:
    return reports_service.report_payload(db, ctx, case_id)


@router.get(
    "/{case_id}/report.html",
    response_class=HTMLResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {"text/html": {"schema": {"type": "string"}}},
            "description": "Analyst-readable HTML risk review report.",
        }
    },
)
def case_report_html(case_id: UUID, db: DbSession, ctx: Tenant) -> HTMLResponse:
    payload = reports_service.report_payload(db, ctx, case_id)
    return HTMLResponse(content=reports_service.report_html(payload))


@router.post("/{case_id}/archive", response_model=CaseRead)
def archive_case(case_id: UUID, db: DbSession, ctx: Tenant) -> RiskCase:
    return cases_service.archive_case(db, ctx, case_id)
