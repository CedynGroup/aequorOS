"""Governed management-actions plan library endpoints (docs/stress.md §3.7, Phase 3).

The plan library's CRUD + maker-checker lifecycle (¶78–81). Plans are org-scoped
(``bank_id`` optional in the body — NULL = an org-wide default plan), so the
collection lives at the tenant root rather than under ``/banks/{id}``. Only an
APPROVED plan may be selected by an enterprise-stress run.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ApproverTenant, DbSession, MutationTenant, Tenant
from app.schemas.management_actions import (
    ManagementActionPlanApproval,
    ManagementActionPlanCreate,
    ManagementActionPlanListRead,
    ManagementActionPlanRead,
    ManagementActionPlanTransition,
    ManagementActionPlanUpdate,
    PlanStatus,
)
from app.services import management_action_plans

router = APIRouter(tags=["management-action-plans"])


@router.post(
    "/management-action-plans",
    response_model=ManagementActionPlanRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createManagementActionPlan",
)
def create_management_action_plan(
    payload: ManagementActionPlanCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> ManagementActionPlanRead:
    return management_action_plans.create_plan(db, ctx, payload)


@router.get(
    "/management-action-plans",
    response_model=ManagementActionPlanListRead,
    operation_id="listManagementActionPlans",
)
def list_management_action_plans(
    db: DbSession,
    ctx: Tenant,
    bank_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[PlanStatus | None, Query(alias="status")] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> ManagementActionPlanListRead:
    return management_action_plans.list_plans(db, ctx, bank_id, status_filter, include_archived)


@router.get(
    "/management-action-plans/{plan_id}",
    response_model=ManagementActionPlanRead,
    operation_id="getManagementActionPlan",
)
def get_management_action_plan(
    plan_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> ManagementActionPlanRead:
    return management_action_plans.get_plan(db, ctx, plan_id)


@router.patch(
    "/management-action-plans/{plan_id}",
    response_model=ManagementActionPlanRead,
    operation_id="updateManagementActionPlan",
)
def update_management_action_plan(
    plan_id: UUID,
    payload: ManagementActionPlanUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> ManagementActionPlanRead:
    return management_action_plans.update_plan(db, ctx, plan_id, payload)


@router.post(
    "/management-action-plans/{plan_id}/submit",
    response_model=ManagementActionPlanRead,
    operation_id="submitManagementActionPlan",
)
def submit_management_action_plan(
    plan_id: UUID,
    payload: ManagementActionPlanTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> ManagementActionPlanRead:
    return management_action_plans.submit_plan(db, ctx, plan_id, payload)


@router.post(
    "/management-action-plans/{plan_id}/approve",
    response_model=ManagementActionPlanRead,
    operation_id="approveManagementActionPlan",
)
def approve_management_action_plan(
    plan_id: UUID,
    payload: ManagementActionPlanApproval,
    db: DbSession,
    ctx: ApproverTenant,
) -> ManagementActionPlanRead:
    return management_action_plans.approve_plan(db, ctx, plan_id, payload)


@router.post(
    "/management-action-plans/{plan_id}/archive",
    response_model=ManagementActionPlanRead,
    operation_id="archiveManagementActionPlan",
)
def archive_management_action_plan(
    plan_id: UUID,
    payload: ManagementActionPlanTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> ManagementActionPlanRead:
    return management_action_plans.archive_plan(db, ctx, plan_id, payload)
