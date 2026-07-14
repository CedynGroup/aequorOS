from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.capital import (
    CapitalComparisonRead,
    CapitalProjectionCreate,
    CapitalProjectionRead,
    CapitalSummaryRead,
)
from app.services import capital

router = APIRouter(tags=["capital"])


@router.post(
    "/cases/{case_id}/capital-projections",
    response_model=CapitalProjectionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_capital_projection(
    case_id: UUID, payload: CapitalProjectionCreate, db: DbSession, ctx: MutationTenant
) -> CapitalProjectionRead:
    return capital.create_projection(db, ctx, case_id, payload)


@router.get(
    "/cases/{case_id}/capital-projections/{projection_id}",
    response_model=CapitalProjectionRead,
)
def get_capital_projection(
    case_id: UUID, projection_id: UUID, db: DbSession, ctx: Tenant
) -> CapitalProjectionRead:
    return capital.get_projection(db, ctx, case_id, projection_id)


@router.get("/cases/{case_id}/capital-summary", response_model=CapitalSummaryRead)
def get_capital_summary(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    scenario_id: Annotated[UUID | None, Query()] = None,
) -> CapitalSummaryRead:
    return capital.get_summary(db, ctx, case_id, scenario_id)


@router.get("/cases/{case_id}/capital-comparison", response_model=CapitalComparisonRead)
def get_capital_comparison(case_id: UUID, db: DbSession, ctx: Tenant) -> CapitalComparisonRead:
    return capital.get_comparison(db, ctx, case_id)
