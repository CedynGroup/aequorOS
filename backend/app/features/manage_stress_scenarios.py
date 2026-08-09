"""Scenario library endpoints (Scenario Workbench)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.scenario_workbench import (
    ScenarioCatalogueRead,
    StressScenarioArchive,
    StressScenarioCreate,
    StressScenarioRead,
    StressScenarioUpdate,
    WorkbenchModule,
)
from app.services import stress_scenarios

router = APIRouter(tags=["scenario-workbench"])


@router.get(
    "/banks/{bank_id}/scenario-workbench/{module}/scenarios",
    response_model=ScenarioCatalogueRead,
    operation_id="listScenarioCatalogue",
)
def list_scenario_catalogue(  # noqa: PLR0913 - query surface of one read
    bank_id: str,
    module: WorkbenchModule,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> ScenarioCatalogueRead:
    return stress_scenarios.list_catalogue(
        db, ctx, bank_id, module, reporting_period_id, include_archived
    )


@router.post(
    "/banks/{bank_id}/scenario-workbench/{module}/scenarios",
    response_model=StressScenarioRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStressScenario",
)
def create_stress_scenario(
    bank_id: str,
    module: WorkbenchModule,
    payload: StressScenarioCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> StressScenarioRead:
    return stress_scenarios.create_scenario(db, ctx, bank_id, module, payload)


@router.patch(
    "/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}",
    response_model=StressScenarioRead,
    operation_id="updateStressScenario",
)
def update_stress_scenario(  # noqa: PLR0913 - path + payload of one write
    bank_id: str,
    module: WorkbenchModule,
    scenario_id: UUID,
    payload: StressScenarioUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> StressScenarioRead:
    return stress_scenarios.update_scenario(db, ctx, bank_id, module, scenario_id, payload)


@router.post(
    "/banks/{bank_id}/scenario-workbench/{module}/scenarios/{scenario_id}/archive",
    response_model=StressScenarioRead,
    operation_id="archiveStressScenario",
)
def archive_stress_scenario(  # noqa: PLR0913 - path + payload of one write
    bank_id: str,
    module: WorkbenchModule,
    scenario_id: UUID,
    payload: StressScenarioArchive,
    db: DbSession,
    ctx: MutationTenant,
) -> StressScenarioRead:
    return stress_scenarios.set_archived(db, ctx, bank_id, module, scenario_id, payload)
