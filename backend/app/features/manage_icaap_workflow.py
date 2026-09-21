"""The bank's ICAAP review chain: propose, edit, submit, approve."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, IcaapCreate, IcaapEdit, IcaapView, IcaapWorkflowApprove
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapWorkflowTemplateCreate,
    IcaapWorkflowTemplateDecision,
    IcaapWorkflowTemplateListRead,
    IcaapWorkflowTemplateRead,
    IcaapWorkflowTemplateSubmit,
    IcaapWorkflowTemplateUpdate,
)
from app.services.icaap import cycles as cycles_service
from app.services.icaap import workflow_templates

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_TEMPLATES = "/banks/{bank_id}/icaap/workflow-templates"
_TEMPLATE = f"{_TEMPLATES}/{{template_id}}"


@router.get(
    _TEMPLATES,
    response_model=IcaapWorkflowTemplateListRead,
    operation_id="listIcaapWorkflowTemplates",
    responses=_ERRORS,
)
def list_icaap_workflow_templates(
    bank_id: str, db: DbSession, access: IcaapView
) -> IcaapWorkflowTemplateListRead:
    """Every proposed chain, plus the one a cycle submitted now would pin."""
    _ = bank_id
    return workflow_templates.list_templates(
        db, access, cycles_service.default_framework(db, access)
    )


@router.post(
    _TEMPLATES,
    response_model=IcaapWorkflowTemplateRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="proposeIcaapWorkflowTemplate",
    responses=_ERRORS,
)
def propose_icaap_workflow_template(
    bank_id: str,
    payload: IcaapWorkflowTemplateCreate,
    db: DbSession,
    access: IcaapCreate,
) -> IcaapWorkflowTemplateRead:
    """Propose a review chain. It governs nothing until somebody else approves it."""
    _ = bank_id
    return workflow_templates.propose_template(
        db, access, cycles_service.default_framework(db, access), payload
    )


@router.patch(
    _TEMPLATE,
    response_model=IcaapWorkflowTemplateRead,
    operation_id="updateIcaapWorkflowTemplate",
    responses=_ERRORS,
)
def update_icaap_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: IcaapWorkflowTemplateUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapWorkflowTemplateRead:
    _ = bank_id
    return workflow_templates.update_template(db, access, template_id, payload)


@router.post(
    f"{_TEMPLATE}/submit",
    response_model=IcaapWorkflowTemplateRead,
    operation_id="submitIcaapWorkflowTemplate",
    responses=_ERRORS,
)
def submit_icaap_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: IcaapWorkflowTemplateSubmit,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapWorkflowTemplateRead:
    _ = bank_id
    return workflow_templates.submit_template(db, access, template_id, payload)


@router.post(
    f"{_TEMPLATE}/decision",
    response_model=IcaapWorkflowTemplateRead,
    operation_id="decideIcaapWorkflowTemplate",
    responses=_ERRORS,
)
def decide_icaap_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: IcaapWorkflowTemplateDecision,
    db: DbSession,
    access: IcaapWorkflowApprove,
) -> IcaapWorkflowTemplateRead:
    """Approve or reject a proposed chain. Never by whoever proposed it."""
    _ = bank_id
    return workflow_templates.decide_template(db, access, template_id, payload)
