"""Governed macro-scenario library endpoints (docs/stress.md §3.1, Phase 1).

The scenario spine's CRUD + maker-checker lifecycle. Scenarios are org-scoped
(``bank_id`` optional in the body — NULL = a supervisory, org-wide scenario), so
the collection lives at the tenant root rather than under ``/banks/{id}``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ApproverTenant, DbSession, MutationTenant, Tenant
from app.schemas.stress import (
    MacroModule,
    MacroScenarioApproval,
    MacroScenarioCreate,
    MacroScenarioListRead,
    MacroScenarioRead,
    MacroScenarioTransition,
    MacroScenarioUpdate,
    ScenarioStatus,
    ScenarioTranslationRead,
    ScenarioType,
)
from app.services import macro_scenarios

router = APIRouter(tags=["macro-scenarios"])


@router.post(
    "/macro-scenarios",
    response_model=MacroScenarioRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMacroScenario",
)
def create_macro_scenario(
    payload: MacroScenarioCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> MacroScenarioRead:
    return macro_scenarios.create_scenario(db, ctx, payload)


@router.get(
    "/macro-scenarios",
    response_model=MacroScenarioListRead,
    operation_id="listMacroScenarios",
)
def list_macro_scenarios(  # noqa: PLR0913 - query surface of one read
    db: DbSession,
    ctx: Tenant,
    bank_id: Annotated[str | None, Query()] = None,
    scenario_type: Annotated[ScenarioType | None, Query()] = None,
    status_filter: Annotated[ScenarioStatus | None, Query(alias="status")] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> MacroScenarioListRead:
    return macro_scenarios.list_scenarios(
        db, ctx, bank_id, scenario_type, status_filter, include_archived
    )


@router.get(
    "/macro-scenarios/{scenario_id}",
    response_model=MacroScenarioRead,
    operation_id="getMacroScenario",
)
def get_macro_scenario(
    scenario_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> MacroScenarioRead:
    return macro_scenarios.get_scenario(db, ctx, scenario_id)


@router.patch(
    "/macro-scenarios/{scenario_id}",
    response_model=MacroScenarioRead,
    operation_id="updateMacroScenario",
)
def update_macro_scenario(
    scenario_id: UUID,
    payload: MacroScenarioUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> MacroScenarioRead:
    return macro_scenarios.update_scenario(db, ctx, scenario_id, payload)


@router.post(
    "/macro-scenarios/{scenario_id}/submit",
    response_model=MacroScenarioRead,
    operation_id="submitMacroScenario",
)
def submit_macro_scenario(
    scenario_id: UUID,
    payload: MacroScenarioTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> MacroScenarioRead:
    return macro_scenarios.submit_scenario(db, ctx, scenario_id, payload)


@router.post(
    "/macro-scenarios/{scenario_id}/approve",
    response_model=MacroScenarioRead,
    operation_id="approveMacroScenario",
)
def approve_macro_scenario(
    scenario_id: UUID,
    payload: MacroScenarioApproval,
    db: DbSession,
    ctx: ApproverTenant,
) -> MacroScenarioRead:
    return macro_scenarios.approve_scenario(db, ctx, scenario_id, payload)


@router.post(
    "/macro-scenarios/{scenario_id}/archive",
    response_model=MacroScenarioRead,
    operation_id="archiveMacroScenario",
)
def archive_macro_scenario(
    scenario_id: UUID,
    payload: MacroScenarioTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> MacroScenarioRead:
    return macro_scenarios.archive_scenario(db, ctx, scenario_id, payload)


@router.get(
    "/macro-scenarios/{scenario_id}/translation/{module}",
    response_model=ScenarioTranslationRead,
    operation_id="translateMacroScenario",
)
def translate_macro_scenario(
    scenario_id: UUID,
    module: MacroModule,
    db: DbSession,
    ctx: Tenant,
) -> ScenarioTranslationRead:
    return macro_scenarios.translate_scenario(db, ctx, scenario_id, module)
