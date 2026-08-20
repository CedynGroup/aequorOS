"""Contingency Funding Plan engine (LRMD 2026 ¶70–77).

Versioned plans carrying the ¶72(a)–(g) minimum contents; Board approval is
annual (¶71) through the maker-checker discipline (the approver cannot
approve their own draft); activation and de-escalation are append-only ¶74
events that snapshot the EWI evaluation and enter the notification pipeline
addressed at the regulator-notification obligation.

Approval is where directive completeness is enforced: a plan missing any of
the seven content blocks, an intraday funding option (¶75(b)) or the
regulator/media communication entries (¶72(g)) cannot be approved — the same
"the institution's own discipline is the product" stance as the signing
policy.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.models import Bank, CfpActivationEvent, ContingencyFundingPlan
from app.schemas.liquidity_cfp import (
    CfpActivationCreate,
    CfpApprove,
    CfpContent,
    CfpEventListRead,
    CfpEventRead,
    CfpPut,
    CfpRead,
    CfpSummaryRead,
    EwiDashboardRead,
    EwiEvaluationRead,
)
from app.services import notifications
from app.services.audit import record_event
from app.services.jurisdictions import regulator_short
from app.services.liquidity_ewi import (
    _get_bank_or_404,  # noqa: PLC2701 - shared tenant guards, one definition
    _get_period_or_404,  # noqa: PLC2701
    escalation_state,
    evaluate_ewis,
)
from app.services.live_state import current_fact_period_or_409

# ¶71: Board approval is (at least) annual.
_APPROVAL_VALIDITY_DAYS = 365

_CONTENT_BLOCKS = (
    ("ewi_triggers", "early-warning triggers (¶72(a))"),
    ("funding_options", "funding options by horizon (¶72(b))"),
    ("action_plans", "asset/liability action plans (¶72(c))"),
    ("alternative_sources", "alternative funding sources (¶72(d))"),
    ("escalation_procedures", "escalation and prioritisation procedures (¶72(e))"),
    ("key_relationships", "the key-relationship register (¶72(f))"),
    ("communication_plans", "communication plans (¶72(g))"),
)


def _current_plan(
    db: Session, ctx: TenantContext, bank: Bank
) -> ContingencyFundingPlan | None:
    return db.scalar(
        select(ContingencyFundingPlan)
        .where(
            ContingencyFundingPlan.organization_id == ctx.organization_id,
            ContingencyFundingPlan.bank_id == bank.id,
        )
        .order_by(ContingencyFundingPlan.version.desc())
        .limit(1)
    )


def _approved_plan(
    db: Session, ctx: TenantContext, bank: Bank
) -> ContingencyFundingPlan | None:
    return db.scalar(
        select(ContingencyFundingPlan)
        .where(
            ContingencyFundingPlan.organization_id == ctx.organization_id,
            ContingencyFundingPlan.bank_id == bank.id,
            ContingencyFundingPlan.status == "approved",
        )
        .order_by(ContingencyFundingPlan.version.desc())
        .limit(1)
    )


def _approval_overdue(plan: ContingencyFundingPlan) -> bool:
    if plan.approval_expires_at is None:
        return False
    return plan.approval_expires_at < utc_now().date()


def _read(plan: ContingencyFundingPlan) -> CfpRead:
    return CfpRead(
        id=plan.id,
        bank_id=plan.bank_id,
        version=plan.version,
        status=plan.status,  # pyright: ignore[reportArgumentType]
        content=CfpContent.model_validate(plan.content),
        prepared_by=plan.prepared_by,
        approved_by_user_id=plan.approved_by_user_id,
        approval_reference=plan.approval_reference,
        approval_timestamp=plan.approval_timestamp,
        approval_expires_at=plan.approval_expires_at,
        approval_overdue=_approval_overdue(plan),
        active=plan.active,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def get_cfp(db: Session, ctx: TenantContext, bank_id: str) -> CfpSummaryRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    current = _current_plan(db, ctx, bank)
    approved = _approved_plan(db, ctx, bank)
    return CfpSummaryRead(
        current=_read(current) if current is not None else None,
        approved=_read(approved) if approved is not None else None,
    )


def ewi_dashboard(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> EwiDashboardRead:
    """The server-side EWI board: evaluations + escalation + CFP posture."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = (
        current_fact_period_or_409(db, ctx, bank, "liquidity")
        if reporting_period_id is None
        else _get_period_or_404(db, ctx, bank, reporting_period_id)
    )
    evaluations = evaluate_ewis(db, ctx, bank, period)
    approved = _approved_plan(db, ctx, bank)
    cfp_active = bool(approved is not None and approved.active)
    return EwiDashboardRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        as_of_date=period.period_end,
        indicators=evaluations,
        escalation_state=escalation_state(evaluations, cfp_active=cfp_active),
        cfp_approved_version=approved.version if approved is not None else None,
        cfp_approval_expires_at=(
            approved.approval_expires_at if approved is not None else None
        ),
        cfp_active=cfp_active,
    )


def put_cfp(db: Session, ctx: TenantContext, bank_id: str, payload: CfpPut) -> CfpRead:
    """Create or update the draft plan (a new draft supersedes no approval)."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    current = _current_plan(db, ctx, bank)
    if current is not None and current.status == "draft":
        plan = current
        plan.content = payload.content.model_dump(mode="json")
        plan.prepared_by = ctx.actor_user_id
    else:
        plan = ContingencyFundingPlan(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            version=(current.version + 1) if current is not None else 1,
            status="draft",
            content=payload.content.model_dump(mode="json"),
            prepared_by=ctx.actor_user_id,
        )
        db.add(plan)
        db.flush()
    record_event(
        db,
        ctx,
        event_type="liquidity_cfp.draft_saved",
        entity_type="contingency_funding_plan",
        entity_id=plan.id,
        details={"version": plan.version, "reason": payload.reason},
    )
    db.commit()
    return _read(plan)


def _require_directive_complete(content: CfpContent) -> None:
    missing = [label for key, label in _CONTENT_BLOCKS if not getattr(content, key)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "cfp_incomplete",
                "message": "The plan is missing required contents: " + "; ".join(missing) + ".",
            },
        )
    if not any(option.horizon == "intraday" for option in content.funding_options):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "cfp_missing_intraday",
                "message": (
                    "Funding options must cover the intraday horizon (LRMD ¶75(b))."
                ),
            },
        )
    audiences = {plan.audience for plan in content.communication_plans}
    if not {"regulator", "media"} <= audiences:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "cfp_missing_communications",
                "message": (
                    "Communication plans must include the regulator and media "
                    "audiences (LRMD ¶72(g))."
                ),
            },
        )


def approve_cfp(db: Session, ctx: TenantContext, bank_id: str, payload: CfpApprove) -> CfpRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    plan = _current_plan(db, ctx, bank)
    if plan is None or plan.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_draft_cfp",
                "message": "There is no draft plan awaiting approval.",
            },
        )
    if plan.prepared_by is not None and plan.prepared_by == ctx.actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "self_approval",
                "message": "The preparer of a plan cannot approve it (maker-checker).",
            },
        )
    _require_directive_complete(CfpContent.model_validate(plan.content))
    now = utc_now()
    prior = _approved_plan(db, ctx, bank)
    if prior is not None:
        prior.status = "superseded"
        plan.active = prior.active  # an activation survives a plan refresh
        prior.active = False
    plan.status = "approved"
    plan.approved_by_user_id = ctx.actor_user_id
    plan.approval_reference = payload.approval_reference
    plan.approval_timestamp = now
    plan.approval_expires_at = (now + timedelta(days=_APPROVAL_VALIDITY_DAYS)).date()
    record_event(
        db,
        ctx,
        event_type="liquidity_cfp.approved",
        entity_type="contingency_funding_plan",
        entity_id=plan.id,
        details={
            "version": plan.version,
            "approval_reference": payload.approval_reference,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(plan)


def _event_read(event: CfpActivationEvent, version: int) -> CfpEventRead:
    return CfpEventRead(
        id=event.id,
        cfp_id=event.cfp_id,
        cfp_version=version,
        event_type=event.event_type,  # pyright: ignore[reportArgumentType]
        reason=event.reason,
        ewi_snapshot=[
            EwiEvaluationRead.model_validate(entry) for entry in event.ewi_snapshot
        ],
        approval_overdue=event.approval_overdue,
        regulator_notification_id=event.regulator_notification_id,
        created_by=event.created_by,
        created_at=event.created_at,
    )


def _record_transition(  # noqa: PLR0913 - the ¶74 event carries its full evidence set
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    plan: ContingencyFundingPlan,
    payload: CfpActivationCreate,
    *,
    event_type: str,
) -> CfpEventRead:
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    evaluations = evaluate_ewis(db, ctx, bank, period)
    overdue = _approval_overdue(plan)
    regulator = regulator_short(db, bank)
    if event_type == "activated":
        title = f"CFP activated — notify {regulator} (LRMD ¶74)"
        body = (
            f"The contingency funding plan (v{plan.version}) has been ACTIVATED: "
            f"{payload.reason} The directive requires prompt notification of "
            f"{regulator} with regular updates until de-escalation."
        )
        severity = "critical"
    else:
        title = f"CFP de-escalated — notify {regulator} (LRMD ¶74)"
        body = (
            f"The contingency funding plan (v{plan.version}) has been DE-ESCALATED: "
            f"{payload.reason} The directive requires notifying {regulator} of the "
            "de-escalation."
        )
        severity = "warning"
    if overdue:
        body += (
            " Note: the plan's annual Board approval is overdue (LRMD ¶71) — "
            "re-approval must be scheduled."
        )
    rows = notifications.emit(
        db,
        ctx,
        type=f"cfp.{event_type}",
        severity=severity,
        title=title,
        body=body,
        entity_type="contingency_funding_plan",
        entity_id=plan.id,
        recipient_role="approver",
    )
    if not rows:
        # The ¶74 obligation must surface even when no user currently holds
        # the approver role — fall back to an org-wide notification.
        rows = notifications.emit(
            db,
            ctx,
            type=f"cfp.{event_type}",
            severity=severity,
            title=title,
            body=body,
            entity_type="contingency_funding_plan",
            entity_id=plan.id,
        )
    event = CfpActivationEvent(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        cfp_id=plan.id,
        event_type=event_type,
        reason=payload.reason,
        ewi_snapshot=[entry.model_dump(mode="json") for entry in evaluations],
        approval_overdue=overdue,
        regulator_notification_id=rows[0].id if rows else None,
        created_by=ctx.actor_user_id,
    )
    db.add(event)
    plan.active = event_type == "activated"
    record_event(
        db,
        ctx,
        event_type=f"liquidity_cfp.{event_type}",
        entity_type="contingency_funding_plan",
        entity_id=plan.id,
        details={
            "version": plan.version,
            "reason": payload.reason,
            "approval_overdue": overdue,
        },
    )
    version = plan.version
    db.commit()
    return _event_read(event, version)


def activate_cfp(
    db: Session, ctx: TenantContext, bank_id: str, payload: CfpActivationCreate
) -> CfpEventRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    plan = _approved_plan(db, ctx, bank)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_approved_cfp",
                "message": (
                    "No Board-approved contingency funding plan exists; approve a "
                    "plan before activating it."
                ),
            },
        )
    if plan.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "cfp_already_active", "message": "The CFP is already active."},
        )
    return _record_transition(db, ctx, bank, plan, payload, event_type="activated")


def de_escalate_cfp(
    db: Session, ctx: TenantContext, bank_id: str, payload: CfpActivationCreate
) -> CfpEventRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    plan = _approved_plan(db, ctx, bank)
    if plan is None or not plan.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "cfp_not_active", "message": "The CFP is not active."},
        )
    return _record_transition(db, ctx, bank, plan, payload, event_type="de_escalated")


def list_events(db: Session, ctx: TenantContext, bank_id: str) -> CfpEventListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    records = db.execute(
        select(CfpActivationEvent, ContingencyFundingPlan.version)
        .join(
            ContingencyFundingPlan,
            CfpActivationEvent.cfp_id == ContingencyFundingPlan.id,
        )
        .where(
            CfpActivationEvent.organization_id == ctx.organization_id,
            CfpActivationEvent.bank_id == bank.id,
        )
        .order_by(CfpActivationEvent.created_at.desc())
    ).all()
    return CfpEventListRead(
        events=[_event_read(event, version) for event, version in records]
    )
