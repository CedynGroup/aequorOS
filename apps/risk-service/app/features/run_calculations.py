from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.calculations import (
    CalculationRerunCreate,
    CalculationRunCreate,
    CalculationRunListRead,
    CalculationRunRead,
)
from app.services import calculations

router = APIRouter(tags=["calculations"])


@router.get("/cases/{case_id}/calculation-runs", response_model=CalculationRunListRead)
def list_calculation_runs(  # noqa: PLR0913
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    scenario_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CalculationRunListRead:
    return calculations.list_runs(
        db, ctx, case_id, scenario_id=scenario_id, limit=limit, offset=offset
    )


@router.post(
    "/cases/{case_id}/calculation-runs",
    response_model=CalculationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def start_calculation_run(
    case_id: UUID, payload: CalculationRunCreate, db: DbSession, ctx: MutationTenant
) -> CalculationRunRead:
    return calculations.start_run(db, ctx, case_id, payload)


@router.get("/cases/{case_id}/calculation-runs/{run_id}", response_model=CalculationRunRead)
def get_calculation_run(
    case_id: UUID, run_id: UUID, db: DbSession, ctx: Tenant
) -> CalculationRunRead:
    return calculations.get_run(db, ctx, case_id, run_id)


@router.post(
    "/cases/{case_id}/calculation-runs/{run_id}/rerun",
    response_model=CalculationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def rerun_calculation(
    case_id: UUID,
    run_id: UUID,
    payload: CalculationRerunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> CalculationRunRead:
    return calculations.rerun(db, ctx, case_id, run_id, payload)
