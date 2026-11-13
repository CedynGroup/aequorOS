"""The bank's own filing review chain: proposing one, and approving it by four eyes.

The package-plane twin of ``icaap/service/workflow_templates.py``, and for the
same reason: a bank whose Head of Compliance sits between the Approver and the
Validator does not have the platform default, and a platform that made it
pretend otherwise would be recording the wrong governance.

It is *governed* data. Changing who must approve a return is itself an approval:
a template is drafted, submitted, and approved by somebody other than whoever
proposed it — a rule the database also carries as a CHECK, because a four-eyes
rule that lives only in a service is one refactor away from not existing. An
approved template is sealed, so a change is a NEW version that supersedes the
old one in the same transaction.

In-flight packages never move. A package pins its chain when it is sent for
approval (``package_workflow_stages``), so approving a new template today cannot
change the governance a return already under review is being approved under.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, ConditionKind, Module, Permission, Sensitivity
from app.db.base import utc_now
from app.identity.public import (
    Bank,
    evaluate_bank_permission,
    require_institution_coverage,
    require_resolved_bank_permission,
)
from app.reporting.domain import workflow as domain
from app.reporting.models.filing_workflow import FilingWorkflowTemplate
from app.reporting.schemas.filing_workflow import (
    FilingStageInput,
    FilingWorkflowTemplateCreate,
    FilingWorkflowTemplateDecision,
    FilingWorkflowTemplateListRead,
    FilingWorkflowTemplateRead,
    FilingWorkflowTemplateSubmit,
    FilingWorkflowTemplateUpdate,
    FilingWorkflowTemplateWriteAcknowledgement,
)
from app.reporting.service.configure_access import (
    REG_DENIAL_DETAIL,
    require_reporting_configure_access,
)
from app.reporting.service.filing_workflow.errors import conflict, not_found, unprocessable
from app.services.audit import record_event

_EDITABLE = frozenset({"draft"})


def _require_reg_human(ctx: TenantContext) -> None:
    """Governance of the filing chain is a human duty of this institution.

    Machine principals and impersonated sessions never hold it — the same rule
    the REG configuration cutovers enforce — so the refusal is explicit here and
    does not depend on which route dependency resolved the caller.
    """
    if ctx.integration_key_id is not None or ctx.impersonation_context is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REG_DENIAL_DETAIL)


def _list_access(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    """Resolve the bank for the template list, then require REG/restricted view."""
    _require_reg_human(ctx)
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="filing_template_list",
        denial_detail=REG_DENIAL_DETAIL,
    )
    return bank


def _decide_access(db: Session, ctx: TenantContext, bank_id: str, template_id: UUID) -> Bank:
    """Resolve the bank, then require REG/restricted approval with four eyes.

    The maker-checker condition reaches the evaluator like the ICAAP chain's
    does, so the proposer's refusal is a scoped denial with its reason in the
    binding trace — evaluated before the row is locked or any state is read as
    decided. A template that does not exist cannot make the condition fail, so
    an unknown id still answers 404 after the gate, never before it.
    """
    _require_reg_human(ctx)
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.APPROVE,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="filing_template_decision",
        conditions=approval_conditions(db, ctx, bank.id, template_id),
        denial_detail=REG_DENIAL_DETAIL,
    )
    return bank


def stage_inputs(stages: tuple[domain.FilingStage, ...]) -> list[FilingStageInput]:
    return [FilingStageInput.model_validate(stage.as_dict()) for stage in stages]


def validate(raw: list[FilingStageInput]) -> tuple[domain.FilingStage, ...]:
    try:
        return domain.validate_stages([entry.model_dump() for entry in raw])
    except domain.StageChainError as exc:
        raise unprocessable(exc.code, exc.message) from exc


def _read(row: FilingWorkflowTemplate) -> FilingWorkflowTemplateRead:
    return FilingWorkflowTemplateRead(
        id=row.id,
        bank_id=row.bank_id,
        version=row.version,
        status=row.status,  # pyright: ignore[reportArgumentType]
        stages=[FilingStageInput.model_validate(entry) for entry in row.stages],
        reason=row.reason,
        proposed_by=row.proposed_by,
        submitted_at=row.submitted_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        superseded_at=row.superseded_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def approved_template(
    db: Session, ctx: TenantContext, bank_id: str
) -> FilingWorkflowTemplate | None:
    """The one approved chain for this institution, if it has one."""
    return db.scalar(
        select(FilingWorkflowTemplate).where(
            FilingWorkflowTemplate.organization_id == ctx.organization_id,
            FilingWorkflowTemplate.bank_id == bank_id,
            FilingWorkflowTemplate.status == "approved",
        )
    )


def effective_stages(
    db: Session, ctx: TenantContext, bank_id: str
) -> tuple[tuple[domain.FilingStage, ...], str, FilingWorkflowTemplate | None]:
    """The chain a package sent for approval now would pin: the bank's, else the default."""
    template = approved_template(db, ctx, bank_id)
    if template is not None:
        return domain.validate_stages(list(template.stages)), "bank_template", template
    return domain.default_stages(), "platform_default", None


def _ack(row: FilingWorkflowTemplate) -> FilingWorkflowTemplateWriteAcknowledgement:
    """Write-only evidence: the written identity, status and server timestamps.

    The chain the caller submitted is their own input; the stored row is never
    echoed through a write response.
    """
    return FilingWorkflowTemplateWriteAcknowledgement(
        id=row.id,
        bank_id=row.bank_id,
        version=row.version,
        status=row.status,  # pyright: ignore[reportArgumentType]
        created_at=row.created_at,
        updated_at=row.updated_at,
        submitted_at=row.submitted_at,
    )


def decision_payload(
    db: Session, ctx: TenantContext, bank: Bank, row: FilingWorkflowTemplate
) -> FilingWorkflowTemplateRead | FilingWorkflowTemplateWriteAcknowledgement:
    """The decided row for a decider who may read the chain; otherwise the ack.

    Approval authority and read authority are separate sentences. Today's
    approver bundle carries both, so a decided row is the ordinary answer; a
    future approval authority without view falls back to the write-only
    acknowledgement rather than borrowing read access.
    """
    decision = evaluate_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="filing_template_decision_read",
    )
    if decision is not None and decision.allowed:
        return _read(row)
    return _ack(row)


def list_templates(db: Session, ctx: TenantContext, bank_id: str) -> FilingWorkflowTemplateListRead:
    bank = _list_access(db, ctx, bank_id)
    stages, source, _template = effective_stages(db, ctx, bank.id)
    rows = list(
        db.scalars(
            select(FilingWorkflowTemplate)
            .where(
                FilingWorkflowTemplate.organization_id == ctx.organization_id,
                FilingWorkflowTemplate.bank_id == bank.id,
            )
            .order_by(FilingWorkflowTemplate.version.desc())
        )
    )
    return FilingWorkflowTemplateListRead(
        templates=[_read(row) for row in rows],
        effective_stages=stage_inputs(stages),
        effective_source=source,  # pyright: ignore[reportArgumentType]
    )


def _actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:  # pragma: no cover - refused upstream
        raise conflict("actor_required", "This action requires a signed-in user.")
    return ctx.actor_user_id


def _next_version(db: Session, ctx: TenantContext, bank_id: str) -> int:
    highest = db.scalar(
        select(FilingWorkflowTemplate.version)
        .where(
            FilingWorkflowTemplate.organization_id == ctx.organization_id,
            FilingWorkflowTemplate.bank_id == bank_id,
        )
        .order_by(FilingWorkflowTemplate.version.desc())
        .limit(1)
    )
    return (highest or 0) + 1


def propose_template(
    db: Session, ctx: TenantContext, bank_id: str, payload: FilingWorkflowTemplateCreate
) -> FilingWorkflowTemplateWriteAcknowledgement:
    # Proposing a chain is a configuration duty (exact reg/configure/restricted
    # on this bank), evaluated before any row, version number or audit event.
    bank = require_reporting_configure_access(db, ctx, bank_id)
    stages = validate(payload.stages)
    open_draft = db.scalar(
        select(FilingWorkflowTemplate).where(
            FilingWorkflowTemplate.organization_id == ctx.organization_id,
            FilingWorkflowTemplate.bank_id == bank.id,
            FilingWorkflowTemplate.status.in_(["draft", "pending_approval"]),
        )
    )
    if open_draft is not None:
        raise conflict(
            "workflow_template_open",
            "A proposed filing chain is already open for this institution. Finish or "
            "withdraw it before proposing another.",
            template_id=str(open_draft.id),
            status=open_draft.status,
        )
    row = FilingWorkflowTemplate(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        version=_next_version(db, ctx, bank.id),
        status="draft",
        stages=[stage.as_dict() for stage in stages],
        reason=payload.reason,
        proposed_by=_actor(ctx),
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="filing_workflow_template.proposed",
        entity_type="filing_workflow_template",
        entity_id=row.id,
        details={
            "bank_id": bank.id,
            "version": row.version,
            "stages": [stage.stage_key for stage in stages],
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _ack(row)


def _open_or_404(
    db: Session, ctx: TenantContext, bank: Bank, template_id: UUID
) -> FilingWorkflowTemplate:
    row = db.scalar(
        select(FilingWorkflowTemplate)
        .where(
            FilingWorkflowTemplate.id == template_id,
            FilingWorkflowTemplate.organization_id == ctx.organization_id,
            FilingWorkflowTemplate.bank_id == bank.id,
        )
        .with_for_update()
    )
    if row is None:
        not_found()
    return row


def update_template(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateUpdate,
) -> FilingWorkflowTemplateWriteAcknowledgement:
    bank = require_reporting_configure_access(db, ctx, bank_id)
    row = _open_or_404(db, ctx, bank, template_id)
    if row.status not in _EDITABLE:
        raise conflict(
            "workflow_template_not_editable",
            "Only a draft filing chain can be edited.",
            status=row.status,
        )
    stages = validate(payload.stages)
    row.stages = [stage.as_dict() for stage in stages]
    row.reason = payload.reason
    record_event(
        db,
        ctx,
        event_type="filing_workflow_template.updated",
        entity_type="filing_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "stages": [stage.stage_key for stage in stages]},
    )
    db.commit()
    db.refresh(row)
    return _ack(row)


def submit_template(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateSubmit,
) -> FilingWorkflowTemplateWriteAcknowledgement:
    bank = require_reporting_configure_access(db, ctx, bank_id)
    row = _open_or_404(db, ctx, bank, template_id)
    if row.status not in _EDITABLE:
        raise conflict(
            "workflow_template_not_editable",
            "Only a draft filing chain can be submitted for approval.",
            status=row.status,
        )
    validate([FilingStageInput.model_validate(entry) for entry in row.stages])
    row.status = "pending_approval"
    row.submitted_at = utc_now()
    row.reason = payload.reason
    record_event(
        db,
        ctx,
        event_type="filing_workflow_template.submitted",
        entity_type="filing_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _ack(row)


def decide_template(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    template_id: UUID,
    payload: FilingWorkflowTemplateDecision,
) -> FilingWorkflowTemplateRead | FilingWorkflowTemplateWriteAcknowledgement:
    bank = _decide_access(db, ctx, bank_id, template_id)
    row = _open_or_404(db, ctx, bank, template_id)
    if row.status != "pending_approval":
        raise conflict(
            "workflow_template_not_pending",
            "Only a filing chain that has been submitted can be decided.",
            status=row.status,
        )
    # Re-checked under the row lock, because two requests can race between the
    # evaluator's condition and this act; the database CHECK is the last line.
    actor = _actor(ctx)
    if row.proposed_by == actor:
        raise conflict(
            "maker_checker",
            "Whoever proposed a filing chain cannot approve it. A different officer must decide.",
        )
    if payload.decision == "rejected":
        row.status = "rejected"
    else:
        # The prior approved row steps aside FIRST: the partial unique index
        # admits exactly one approved chain per institution.
        previous = approved_template(db, ctx, bank.id)
        if previous is not None:
            previous.status = "superseded"
            previous.superseded_at = utc_now()
            previous.superseded_by_id = row.id
            db.flush()
        row.status = "approved"
    row.decided_by = actor
    row.decided_at = utc_now()
    row.decision_reason = payload.reason
    record_event(
        db,
        ctx,
        event_type=f"filing_workflow_template.{payload.decision}",
        entity_type="filing_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return decision_payload(db, ctx, bank, row)


def approval_conditions(
    db: Session, ctx: TenantContext, bank_id: str, template_id: UUID | None
) -> tuple[ConditionCheck, ...]:
    """Four eyes on the chain itself: the proposer may not approve it."""
    if template_id is None:
        return ()
    row = db.scalar(
        select(FilingWorkflowTemplate).where(
            FilingWorkflowTemplate.id == template_id,
            FilingWorkflowTemplate.organization_id == ctx.organization_id,
            FilingWorkflowTemplate.bank_id == bank_id,
        )
    )
    distinct = row is None or row.proposed_by != ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "filing chain approver is distinct from its proposer"
                if distinct
                else "whoever proposed a filing chain cannot approve it"
            ),
        ),
    )


__all__ = [
    "approval_conditions",
    "approved_template",
    "decide_template",
    "decision_payload",
    "effective_stages",
    "list_templates",
    "propose_template",
    "stage_inputs",
    "submit_template",
    "update_template",
    "validate",
]
