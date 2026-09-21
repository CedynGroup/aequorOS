"""The bank's own ICAAP review chain: proposing one, and approving it by four eyes.

A bank whose Board Risk Committee sits between the CRO and the CEO does not
have the framework's default chain, and a platform that made it pretend
otherwise would be recording the wrong governance. So the chain is data.

It is *governed* data, though, and that is the whole shape of this module.
Changing who must approve an ICAAP is itself an approval: a template is drafted,
submitted, and then approved by somebody other than whoever proposed it — a rule
the database also carries as a CHECK, because a four-eyes rule that lives only
in a service is one refactor away from not existing. An approved template is
sealed, so a change is a NEW version that supersedes the old one in the same
transaction; the superseded row stays exactly as it was approved.

In-flight cycles never move. A cycle pins its chain at its first submission
(``icaap_cycle_stages``), so approving a new template today cannot change the
governance a report already under review is being approved under.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import workflow as domain
from app.domain.icaap.frameworks.schema import Framework
from app.models.icaap import IcaapWorkflowTemplate
from app.schemas.icaap import (
    IcaapStageInput,
    IcaapWorkflowTemplateCreate,
    IcaapWorkflowTemplateDecision,
    IcaapWorkflowTemplateListRead,
    IcaapWorkflowTemplateRead,
    IcaapWorkflowTemplateSubmit,
    IcaapWorkflowTemplateUpdate,
)
from app.services.audit import record_event
from app.services.icaap import guards

_EDITABLE = frozenset({"draft"})

#: The only review chains an impersonated examiner may read. A supervisor reads
#: the governance a Board approved — the chain in force, and the ones it
#: replaced. A ``draft``, a ``pending_approval`` and a ``rejected`` chain are
#: the bank's internal argument about who should approve its ICAAP, which
#: nobody has yet adopted; serving them is the same defect as serving an
#: unfrozen cycle (``guards.EXAMINER_VISIBLE_STATUSES``) one table across.
EXAMINER_VISIBLE: tuple[str, ...] = ("approved", "superseded")


def _stage_inputs(stages: tuple[domain.Stage, ...]) -> list[IcaapStageInput]:
    return [IcaapStageInput.model_validate(stage.as_dict()) for stage in stages]


def _validate(raw: list[IcaapStageInput]) -> tuple[domain.Stage, ...]:
    try:
        return domain.validate_stages([entry.model_dump() for entry in raw])
    except domain.StageChainError as exc:
        raise guards.unprocessable(exc.code, exc.message) from exc


def _read(row: IcaapWorkflowTemplate) -> IcaapWorkflowTemplateRead:
    return IcaapWorkflowTemplateRead(
        id=row.id,
        bank_id=row.bank_id,
        version=row.version,
        status=row.status,  # pyright: ignore[reportArgumentType]
        stages=[IcaapStageInput.model_validate(entry) for entry in row.stages],
        framework_code=row.framework_code,
        framework_version=row.framework_version,
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


def _rows(db: Session, access: IcaapAccess) -> list[IcaapWorkflowTemplate]:
    statement = select(IcaapWorkflowTemplate).where(
        IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
        IcaapWorkflowTemplate.bank_id == access.bank.id,
    )
    if access.examiner:
        statement = statement.where(IcaapWorkflowTemplate.status.in_(EXAMINER_VISIBLE))
    return list(db.scalars(statement.order_by(IcaapWorkflowTemplate.version.desc())))


def approved_template(db: Session, access: IcaapAccess) -> IcaapWorkflowTemplate | None:
    """The one approved chain for this institution, if it has one."""
    return db.scalar(
        select(IcaapWorkflowTemplate).where(
            IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
            IcaapWorkflowTemplate.bank_id == access.bank.id,
            IcaapWorkflowTemplate.status == "approved",
        )
    )


def effective_stages(
    db: Session, access: IcaapAccess, framework: Framework
) -> tuple[tuple[domain.Stage, ...], str, IcaapWorkflowTemplate | None]:
    """The chain a cycle submitted now would pin: the bank's, else the framework's."""
    template = approved_template(db, access)
    if template is not None:
        return domain.validate_stages(list(template.stages)), "bank_template", template
    return domain.stages_from_framework(framework.stages), "framework_default", None


def list_templates(
    db: Session, access: IcaapAccess, framework: Framework
) -> IcaapWorkflowTemplateListRead:
    stages, source, _template = effective_stages(db, access, framework)
    return IcaapWorkflowTemplateListRead(
        templates=[_read(row) for row in _rows(db, access)],
        effective_stages=_stage_inputs(stages),
        effective_source=source,  # pyright: ignore[reportArgumentType]
    )


def _next_version(db: Session, access: IcaapAccess) -> int:
    highest = db.scalar(
        select(IcaapWorkflowTemplate.version)
        .where(
            IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
            IcaapWorkflowTemplate.bank_id == access.bank.id,
        )
        .order_by(IcaapWorkflowTemplate.version.desc())
        .limit(1)
    )
    return (highest or 0) + 1


def propose_template(
    db: Session, access: IcaapAccess, framework: Framework, payload: IcaapWorkflowTemplateCreate
) -> IcaapWorkflowTemplateRead:
    stages = _validate(payload.stages)
    open_draft = db.scalar(
        select(IcaapWorkflowTemplate).where(
            IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
            IcaapWorkflowTemplate.bank_id == access.bank.id,
            IcaapWorkflowTemplate.status.in_(["draft", "pending_approval"]),
        )
    )
    if open_draft is not None:
        raise guards.conflict(
            "workflow_template_open",
            "A proposed review chain is already open for this institution. Finish or "
            "withdraw it before proposing another.",
            template_id=str(open_draft.id),
            status=open_draft.status,
        )
    row = IcaapWorkflowTemplate(
        organization_id=access.ctx.organization_id,
        bank_id=access.bank.id,
        version=_next_version(db, access),
        status="draft",
        stages=[stage.as_dict() for stage in stages],
        framework_code=framework.code,
        framework_version=framework.version,
        reason=payload.reason,
        proposed_by=guards.actor_id(access),
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.workflow_template.proposed",
        entity_type="icaap_workflow_template",
        entity_id=row.id,
        details={
            "bank_id": access.bank.id,
            "version": row.version,
            "stages": [stage.stage_key for stage in stages],
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def _open_or_404(db: Session, access: IcaapAccess, template_id: UUID) -> IcaapWorkflowTemplate:
    row = db.scalar(
        select(IcaapWorkflowTemplate)
        .where(
            IcaapWorkflowTemplate.id == template_id,
            IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
            IcaapWorkflowTemplate.bank_id == access.bank.id,
        )
        .with_for_update()
    )
    if row is None:
        guards.not_found()
    return row


def update_template(
    db: Session, access: IcaapAccess, template_id: UUID, payload: IcaapWorkflowTemplateUpdate
) -> IcaapWorkflowTemplateRead:
    row = _open_or_404(db, access, template_id)
    if row.status not in _EDITABLE:
        raise guards.conflict(
            "workflow_template_not_editable",
            "Only a draft review chain can be edited.",
            status=row.status,
        )
    stages = _validate(payload.stages)
    row.stages = [stage.as_dict() for stage in stages]
    row.reason = payload.reason
    record_event(
        db,
        access.ctx,
        event_type="icaap.workflow_template.updated",
        entity_type="icaap_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "stages": [stage.stage_key for stage in stages]},
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def submit_template(
    db: Session, access: IcaapAccess, template_id: UUID, payload: IcaapWorkflowTemplateSubmit
) -> IcaapWorkflowTemplateRead:
    row = _open_or_404(db, access, template_id)
    if row.status not in _EDITABLE:
        raise guards.conflict(
            "workflow_template_not_editable",
            "Only a draft review chain can be submitted for approval.",
            status=row.status,
        )
    _validate([IcaapStageInput.model_validate(entry) for entry in row.stages])
    row.status = "pending_approval"
    row.submitted_at = utc_now()
    row.reason = payload.reason
    record_event(
        db,
        access.ctx,
        event_type="icaap.workflow_template.submitted",
        entity_type="icaap_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row)


def approval_conditions(
    db: Session, access: IcaapAccess, template_id: UUID | None
) -> tuple[object, ...]:
    """Four eyes on the chain itself: the proposer may not approve it."""
    from app.core.authorization import ConditionCheck, ConditionKind  # noqa: PLC0415

    if template_id is None:
        return ()
    row = db.scalar(
        select(IcaapWorkflowTemplate).where(
            IcaapWorkflowTemplate.id == template_id,
            IcaapWorkflowTemplate.organization_id == access.ctx.organization_id,
            IcaapWorkflowTemplate.bank_id == access.bank.id,
        )
    )
    distinct = row is None or row.proposed_by != access.ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "review chain approver is distinct from its proposer"
                if distinct
                else "whoever proposed a review chain cannot approve it"
            ),
        ),
    )


def decide_template(
    db: Session, access: IcaapAccess, template_id: UUID, payload: IcaapWorkflowTemplateDecision
) -> IcaapWorkflowTemplateRead:
    row = _open_or_404(db, access, template_id)
    if row.status != "pending_approval":
        raise guards.conflict(
            "workflow_template_not_pending",
            "Only a review chain that has been submitted can be decided.",
            status=row.status,
        )
    actor = guards.actor_id(access)
    if row.proposed_by == actor:
        raise guards.conflict(
            "maker_checker",
            "Whoever proposed a review chain cannot approve it. A different officer must decide.",
        )
    if payload.decision == "rejected":
        row.status = "rejected"
        row.decided_by = actor
        row.decided_at = utc_now()
        row.decision_reason = payload.reason
    else:
        # The prior approved row steps aside FIRST: the partial unique index
        # admits exactly one approved chain per institution, and the seal on an
        # approved row admits only the supersession transition.
        previous = approved_template(db, access)
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
        access.ctx,
        event_type=f"icaap.workflow_template.{payload.decision}",
        entity_type="icaap_workflow_template",
        entity_id=row.id,
        details={"version": row.version, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row)


__all__ = [
    "EXAMINER_VISIBLE",
    "approval_conditions",
    "approved_template",
    "decide_template",
    "effective_stages",
    "list_templates",
    "propose_template",
    "submit_template",
    "update_template",
]
