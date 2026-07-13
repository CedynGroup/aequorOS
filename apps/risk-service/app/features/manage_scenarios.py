from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.scenarios import (
    AssumptionCreate,
    AssumptionReview,
    AssumptionUpdate,
    ScenarioArchive,
    ScenarioCopy,
    ScenarioCreate,
    ScenarioInitialize,
    ScenarioMutationResponse,
    ScenarioRead,
    ScenarioReadinessRead,
    ScenarioUpdate,
    ScenarioValidationRead,
    ScenarioWorkspaceRead,
)
from app.services import scenarios as scenario_service

router = APIRouter(tags=["scenarios"])


@router.get("/cases/{case_id}/scenarios", response_model=ScenarioWorkspaceRead)
def list_case_scenarios(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    include_archived: Annotated[bool, Query()] = False,
) -> ScenarioWorkspaceRead:
    return scenario_service.list_workspace(db, ctx, case_id, include_archived=include_archived)


@router.post("/cases/{case_id}/scenarios/initialize", response_model=ScenarioWorkspaceRead)
def initialize_case_scenarios(
    case_id: UUID, payload: ScenarioInitialize, db: DbSession, ctx: MutationTenant
) -> ScenarioWorkspaceRead:
    return scenario_service.initialize_defaults(db, ctx, case_id, payload)


@router.post("/cases/{case_id}/scenarios", response_model=ScenarioMutationResponse)
def create_case_scenario(
    case_id: UUID, payload: ScenarioCreate, db: DbSession, ctx: MutationTenant
) -> ScenarioMutationResponse:
    return scenario_service.create_scenario(db, ctx, case_id, payload)


@router.get("/cases/{case_id}/scenarios/readiness", response_model=ScenarioReadinessRead)
def get_case_scenario_readiness(case_id: UUID, db: DbSession, ctx: Tenant) -> ScenarioReadinessRead:
    return scenario_service.readiness_for(db, ctx, case_id)


@router.get("/cases/{case_id}/scenarios/{scenario_id}", response_model=ScenarioRead)
def get_case_scenario(case_id: UUID, scenario_id: UUID, db: DbSession, ctx: Tenant) -> ScenarioRead:
    return scenario_service.get_scenario(db, ctx, case_id, scenario_id)


@router.patch("/cases/{case_id}/scenarios/{scenario_id}", response_model=ScenarioMutationResponse)
def update_case_scenario(
    case_id: UUID,
    scenario_id: UUID,
    payload: ScenarioUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.update_scenario(db, ctx, case_id, scenario_id, payload)


@router.post(
    "/cases/{case_id}/scenarios/{scenario_id}/copy", response_model=ScenarioMutationResponse
)
def copy_case_scenario(
    case_id: UUID,
    scenario_id: UUID,
    payload: ScenarioCopy,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.copy_scenario(db, ctx, case_id, scenario_id, payload)


@router.post(
    "/cases/{case_id}/scenarios/{scenario_id}/archive", response_model=ScenarioMutationResponse
)
def archive_case_scenario(
    case_id: UUID,
    scenario_id: UUID,
    payload: ScenarioArchive,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.archive_scenario(db, ctx, case_id, scenario_id, payload)


@router.get(
    "/cases/{case_id}/scenarios/{scenario_id}/validation",
    response_model=ScenarioValidationRead,
)
def validate_case_scenario(
    case_id: UUID, scenario_id: UUID, db: DbSession, ctx: Tenant
) -> ScenarioValidationRead:
    return scenario_service.validation_for(db, ctx, case_id, scenario_id)


@router.post(
    "/cases/{case_id}/scenarios/{scenario_id}/assumptions",
    response_model=ScenarioMutationResponse,
)
def create_scenario_assumption(
    case_id: UUID,
    scenario_id: UUID,
    payload: AssumptionCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.create_assumption(db, ctx, case_id, scenario_id, payload)


@router.patch(
    "/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}",
    response_model=ScenarioMutationResponse,
)
def update_scenario_assumption(  # noqa: PLR0913
    case_id: UUID,
    scenario_id: UUID,
    assumption_id: UUID,
    payload: AssumptionUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.update_assumption(db, ctx, case_id, scenario_id, assumption_id, payload)


@router.post(
    "/cases/{case_id}/scenarios/{scenario_id}/assumptions/{assumption_id}/review",
    response_model=ScenarioMutationResponse,
)
def review_scenario_assumption(  # noqa: PLR0913
    case_id: UUID,
    scenario_id: UUID,
    assumption_id: UUID,
    payload: AssumptionReview,
    db: DbSession,
    ctx: MutationTenant,
) -> ScenarioMutationResponse:
    return scenario_service.review_assumption(db, ctx, case_id, scenario_id, assumption_id, payload)
