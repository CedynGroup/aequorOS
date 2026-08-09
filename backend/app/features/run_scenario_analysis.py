"""Scenario analysis endpoints — compute-only what-if + saved analyses.

The analysis POST is deliberately Tenant-gated (not MutationTenant): it
mutates nothing, so a viewer can explore scenarios. Saving, and deleting a
saved analysis, are analyst acts.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.scenario_workbench import (
    AnalysisRunCreate,
    AnalysisRunRead,
    SavedAnalysisCreate,
    SavedAnalysisListRead,
    SavedAnalysisRead,
    WorkbenchModule,
)
from app.services import analysis_workbench

router = APIRouter(tags=["scenario-workbench"])


@router.post(
    "/banks/{bank_id}/scenario-workbench/{module}/analysis",
    response_model=AnalysisRunRead,
    operation_id="runScenarioAnalysis",
)
def run_scenario_analysis(
    bank_id: str,
    module: WorkbenchModule,
    payload: AnalysisRunCreate,
    db: DbSession,
    ctx: Tenant,
) -> AnalysisRunRead:
    return analysis_workbench.run_analysis(db, ctx, bank_id, module, payload)


@router.post(
    "/banks/{bank_id}/scenario-workbench/{module}/analyses",
    response_model=SavedAnalysisRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="saveScenarioAnalysis",
)
def save_scenario_analysis(
    bank_id: str,
    module: WorkbenchModule,
    payload: SavedAnalysisCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> SavedAnalysisRead:
    return analysis_workbench.save_analysis(db, ctx, bank_id, module, payload)


@router.get(
    "/banks/{bank_id}/scenario-workbench/{module}/analyses",
    response_model=SavedAnalysisListRead,
    operation_id="listScenarioAnalyses",
)
def list_scenario_analyses(  # noqa: PLR0913 - query surface of one read
    bank_id: str,
    module: WorkbenchModule,
    db: DbSession,
    ctx: Tenant,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SavedAnalysisListRead:
    return analysis_workbench.list_analyses(db, ctx, bank_id, module, limit, offset)


@router.get(
    "/banks/{bank_id}/scenario-workbench/{module}/analyses/{analysis_id}",
    response_model=SavedAnalysisRead,
    operation_id="getScenarioAnalysis",
)
def get_scenario_analysis(
    bank_id: str,
    module: WorkbenchModule,
    analysis_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> SavedAnalysisRead:
    return analysis_workbench.get_analysis(db, ctx, bank_id, module, analysis_id)


@router.delete(
    "/banks/{bank_id}/scenario-workbench/{module}/analyses/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteScenarioAnalysis",
)
def delete_scenario_analysis(
    bank_id: str,
    module: WorkbenchModule,
    analysis_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
) -> None:
    analysis_workbench.delete_analysis(db, ctx, bank_id, module, analysis_id)
