"""The filing review chain: the bank's template, and one return's chain.

Two surfaces. The TEMPLATE routes govern who must approve a return at this
institution — Bernard's Preparer → Approver → Validator is the seeded default,
not the only possibility, and changing it is itself a four-eyed approval. The
CHAIN routes are one return in motion: where it is, who holds it, every decision
with its comment and round, and the single control the signed-in officer can
actually exercise.

The chain read is deliberately the ONE thing the workspace binds to. The old
``LifecycleStepper`` showed six fixed statuses that do not correspond to people,
which is the visible half of the problem the redesign describes; ``viewer``
here says what THIS caller may do and why not, so a screen can render a control
only where the server would accept it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    ApproverTenant,
    DbSession,
    MutationTenant,
    PackageEdit,
    PackageStageDecide,
    PackageView,
    Tenant,
)
from app.schemas.common import ErrorResponse
from app.schemas.filing_workflow import (
    FilingWorkflowTemplateCreate,
    FilingWorkflowTemplateDecision,
    FilingWorkflowTemplateListRead,
    FilingWorkflowTemplateRead,
    FilingWorkflowTemplateSubmit,
    FilingWorkflowTemplateUpdate,
    PackageChainRead,
    PackageSendForApproval,
    PackageStageDecisionCreate,
)
from app.services.filing_workflow import chain as filing_chain
from app.services.filing_workflow import templates as filing_templates
from app.services.regulatory_reporting import workflow as reporting_workflow
from app.services.regulatory_reporting.common import get_bank_or_404

router = APIRouter(tags=["regulatory-reporting"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_TEMPLATES = "/banks/{bank_id}/filing-workflow-templates"
_TEMPLATE = f"{_TEMPLATES}/{{template_id}}"
_CHAIN = "/banks/{bank_id}/regulatory-packages/{package_id}/workflow"


# ---------------------------------------------------------------------------
# The bank's template
# ---------------------------------------------------------------------------


@router.get(
    _TEMPLATES,
    response_model=FilingWorkflowTemplateListRead,
    operation_id="listFilingWorkflowTemplates",
    responses=_ERRORS,
)
def list_filing_workflow_templates(
    bank_id: str, db: DbSession, ctx: Tenant
) -> FilingWorkflowTemplateListRead:
    """Every proposed chain, plus the one a return sent for approval now would pin."""
    return filing_templates.list_templates(db, ctx, get_bank_or_404(db, ctx, bank_id))


@router.post(
    _TEMPLATES,
    response_model=FilingWorkflowTemplateRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="proposeFilingWorkflowTemplate",
    responses=_ERRORS,
)
def propose_filing_workflow_template(
    bank_id: str,
    payload: FilingWorkflowTemplateCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> FilingWorkflowTemplateRead:
    """Propose a filing chain. It governs nothing until somebody else approves it."""
    return filing_templates.propose_template(db, ctx, get_bank_or_404(db, ctx, bank_id), payload)


@router.patch(
    _TEMPLATE,
    response_model=FilingWorkflowTemplateRead,
    operation_id="updateFilingWorkflowTemplate",
    responses=_ERRORS,
)
def update_filing_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FilingWorkflowTemplateRead:
    return filing_templates.update_template(
        db, ctx, get_bank_or_404(db, ctx, bank_id), template_id, payload
    )


@router.post(
    f"{_TEMPLATE}/submit",
    response_model=FilingWorkflowTemplateRead,
    operation_id="submitFilingWorkflowTemplate",
    responses=_ERRORS,
)
def submit_filing_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateSubmit,
    db: DbSession,
    ctx: MutationTenant,
) -> FilingWorkflowTemplateRead:
    return filing_templates.submit_template(
        db, ctx, get_bank_or_404(db, ctx, bank_id), template_id, payload
    )


@router.post(
    f"{_TEMPLATE}/decision",
    response_model=FilingWorkflowTemplateRead,
    operation_id="decideFilingWorkflowTemplate",
    responses=_ERRORS,
)
def decide_filing_workflow_template(
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateDecision,
    db: DbSession,
    ctx: ApproverTenant,
) -> FilingWorkflowTemplateRead:
    """Approve or reject a proposed chain. Never by whoever proposed it."""
    return filing_templates.decide_template(
        db, ctx, get_bank_or_404(db, ctx, bank_id), template_id, payload
    )


# ---------------------------------------------------------------------------
# One return's chain
# ---------------------------------------------------------------------------


@router.get(
    _CHAIN,
    response_model=PackageChainRead,
    operation_id="getPackageFilingChain",
    responses=_ERRORS,
)
def get_package_filing_chain(
    bank_id: str, package_id: UUID, db: DbSession, access: PackageView
) -> PackageChainRead:
    """Where the return is, who holds it, every decision, and what THIS caller may do."""
    _ = (bank_id, package_id)
    return filing_chain.get_chain(db, access.ctx, access.package)


@router.post(
    f"{_CHAIN}/send-for-approval",
    response_model=PackageChainRead,
    operation_id="sendPackageForApproval",
    responses=_ERRORS,
)
def send_package_for_approval(
    bank_id: str,
    package_id: UUID,
    payload: PackageSendForApproval,
    db: DbSession,
    access: PackageEdit,
) -> PackageChainRead:
    """The Preparer's act: pin the chain and hand the return to the next stage.

    ``checks_passed`` gates entry. Machine validation is not a stage and not a
    person, so there is nothing to press called "Validate" — the checks run with
    generation and the Preparer clears them.
    """
    _ = (bank_id, package_id)
    package = access.package
    if package is None:  # pragma: no cover - resolved by the dependency
        raise AssertionError
    state = filing_chain.load_state(db, access.ctx, package)
    if payload.review_digest != state.review_digest:
        from app.services.filing_workflow.errors import conflict  # noqa: PLC0415

        raise conflict(
            "review_basis_changed",
            "The return changed since this page was loaded. Reload it and send it again.",
            expected=state.review_digest,
        )
    filing_chain.start_chain(db, access.ctx, package, note=payload.note)
    db.commit()
    return filing_chain.read_chain(db, access.ctx, package)


@router.post(
    f"{_CHAIN}/hand-off",
    response_model=PackageChainRead,
    operation_id="handOffPackageFilingStage",
    responses=_ERRORS,
)
def hand_off_package_filing_stage(
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    access: PackageStageDecide,
) -> PackageChainRead:
    """Pass an approved return to the next stage — or file it, if this is last.

    The second of the two acts the filing chain now has. It carries the SAME
    authority as the decision it follows, because handing a return to the
    regulator is not a lesser act than approving it: the dependency resolves
    the stage's own permission, so the transmitting stage still demands
    ``SUBMIT`` and nobody but the Validator can complete a filing.
    """
    package = access.package
    if package is None:  # pragma: no cover - resolved by the dependency
        raise AssertionError

    before = filing_chain.load_state(db, access.ctx, package)
    stage_seq = package.current_stage_seq
    stage = None if stage_seq is None else before.stage(stage_seq)
    filing_chain.hand_off(db, access.ctx, package)

    # The transmitting stage's hand-off IS the filing. It runs here rather than
    # in the chain so ``submit_package_via_channel`` stays the single
    # submission entry point with its own gates intact, and so a transmission
    # that fails rolls the hand-off back with it.
    if stage is not None and stage.transmit_on_approve:
        reporting_workflow.submit_package_via_channel(db, access.ctx, bank_id, package_id)

    db.commit()
    return filing_chain.read_chain(db, access.ctx, package)


@router.post(
    f"{_CHAIN}/decisions",
    response_model=PackageChainRead,
    operation_id="decidePackageFilingStage",
    responses=_ERRORS,
)
def decide_package_filing_stage(
    bank_id: str,
    package_id: UUID,
    payload: PackageStageDecisionCreate,
    db: DbSession,
    access: PackageStageDecide,
) -> PackageChainRead:
    """Approve this stage, or send the return back to a NAMED earlier stage.

    The authority is the stage's: the transmitting stage takes
    ``Permission.SUBMIT``, which only the Validator bundle carries. A send-back
    here is INTERNAL and is never the regulator's ``rejected``.
    """
    _ = (bank_id, package_id)
    package = access.package
    if package is None:  # pragma: no cover - resolved by the dependency
        raise AssertionError
    filing_chain.decide(db, access.ctx, package, payload)
    # Deciding no longer moves the return on: the officer approves, then hands
    # it off (``POST .../chain/hand-off``). Transmission therefore belongs to
    # the hand-off, not here.
    db.commit()
    return filing_chain.read_chain(db, access.ctx, package)
