"""Governed management-actions plan library (docs/stress.md §3.7, Phase 3, ¶78–81).

The authoritative CRUD + maker-checker lifecycle for ``ManagementActionPlan`` — the
governed, documented set of credible management actions the directive requires
(¶80). Governance mirrors ``macro_scenarios`` exactly:

- ``draft`` plans are freely editable by an analyst (maker);
- ``submit`` moves a draft to ``pending_approval``;
- ``approve`` requires a DIFFERENT user with the approver role (maker ≠ checker,
  ¶16), stamps ``approved_by`` / ``approval_timestamp`` and bumps the version;
- an ``approved`` plan is immutable; ``archive`` retires a plan.

Only an ``approved`` plan may feed an official enterprise-stress run —
``resolve_for_official_run`` is the guard the service calls. ``build_domain_plan``
converts the stored rows into the pure
``app.domain.stress.management_actions.ManagementActionPlan`` with no inference,
and ``plan_snapshot`` yields a value-based snapshot for the run's ``input_hash``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
)
from app.domain.stress.management_actions import (
    ManagementActionPlan as DomainPlan,
)
from app.models import Bank, ManagementActionItem, ManagementActionPlan
from app.schemas.management_actions import (
    ActionItemIn,
    ActionItemRead,
    ManagementActionPlanApproval,
    ManagementActionPlanCreate,
    ManagementActionPlanListRead,
    ManagementActionPlanRead,
    ManagementActionPlanSummaryRead,
    ManagementActionPlanTransition,
    ManagementActionPlanUpdate,
)
from app.services.audit import record_event


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_plan_or_404(
    db: Session, ctx: TenantContext, plan_id: UUID
) -> ManagementActionPlan:
    plan = db.scalar(
        select(ManagementActionPlan).where(
            ManagementActionPlan.id == plan_id,
            ManagementActionPlan.organization_id == ctx.organization_id,
        )
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Management-action plan not found."
        )
    return plan


def _load_items(db: Session, plan_id: UUID) -> list[ManagementActionItem]:
    return list(
        db.scalars(
            select(ManagementActionItem)
            .where(ManagementActionItem.plan_id == plan_id)
            .order_by(ManagementActionItem.sort_order, ManagementActionItem.action_id)
        )
    )


def _item_read(item: ManagementActionItem) -> ActionItemRead:
    return ActionItemRead(
        action_id=item.action_id,
        kind=item.kind,  # pyright: ignore[reportArgumentType]
        label=item.label,
        sort_order=item.sort_order,
        trigger_kind=item.trigger_kind,  # pyright: ignore[reportArgumentType]
        watch_minima=list(item.watch_minima) if item.watch_minima else None,
        min_severity=item.min_severity,
        effective_year=item.effective_year,
        capital_raise_ghs=item.capital_raise_ghs,
        capital_raise_tier=item.capital_raise_tier,  # pyright: ignore[reportArgumentType]
        counts_as_paid_up=item.counts_as_paid_up,
        sizing=item.sizing,  # pyright: ignore[reportArgumentType]
        dividend_reduction_pct=item.dividend_reduction_pct,
        rwa_reduction_ghs=item.rwa_reduction_ghs,
        shrinks_leverage_exposure=item.shrinks_leverage_exposure,
        severity_factors=(
            {k: Decimal(str(v)) for k, v in item.severity_factors.items()}
            if item.severity_factors
            else None
        ),
        rationale=item.rationale,
    )


def _read(
    plan: ManagementActionPlan, items: list[ManagementActionItem]
) -> ManagementActionPlanRead:
    return ManagementActionPlanRead(
        id=plan.id,
        organization_id=plan.organization_id,
        bank_id=plan.bank_id,
        code=plan.code,
        name=plan.name,
        description=plan.description,
        status=plan.status,  # pyright: ignore[reportArgumentType]
        version=plan.version,
        created_by=plan.created_by,
        approved_by=plan.approved_by,
        approval_timestamp=plan.approval_timestamp,
        actions=[_item_read(item) for item in items],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _duplicate_code_exists(
    db: Session, ctx: TenantContext, bank_id: str | None, code: str
) -> bool:
    condition = (
        ManagementActionPlan.bank_id.is_(None)
        if bank_id is None
        else (ManagementActionPlan.bank_id == bank_id)
    )
    return (
        db.scalar(
            select(ManagementActionPlan.id).where(
                ManagementActionPlan.organization_id == ctx.organization_id,
                condition,
                ManagementActionPlan.code == code,
            )
        )
        is not None
    )


def _add_items(
    db: Session, ctx: TenantContext, plan_id: UUID, actions: list[ActionItemIn]
) -> None:
    for action in actions:
        db.add(
            ManagementActionItem(
                organization_id=ctx.organization_id,
                plan_id=plan_id,
                action_id=action.action_id,
                kind=action.kind,
                label=action.label,
                sort_order=action.sort_order,
                trigger_kind=action.trigger_kind,
                watch_minima=list(action.watch_minima) if action.watch_minima else None,
                min_severity=action.min_severity,
                effective_year=action.effective_year,
                capital_raise_ghs=action.capital_raise_ghs,
                capital_raise_tier=action.capital_raise_tier,
                counts_as_paid_up=action.counts_as_paid_up,
                sizing=action.sizing,
                dividend_reduction_pct=action.dividend_reduction_pct,
                rwa_reduction_ghs=action.rwa_reduction_ghs,
                shrinks_leverage_exposure=action.shrinks_leverage_exposure,
                severity_factors=(
                    {k: str(v) for k, v in action.severity_factors.items()}
                    if action.severity_factors
                    else None
                ),
                rationale=action.rationale,
            )
        )


def create_plan(
    db: Session, ctx: TenantContext, payload: ManagementActionPlanCreate
) -> ManagementActionPlanRead:
    if payload.bank_id is not None:
        _get_bank_or_404(db, ctx, payload.bank_id)
    if _duplicate_code_exists(db, ctx, payload.bank_id, payload.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "plan_code_exists",
                "message": f"A management-action plan with code '{payload.code}' already exists.",
            },
        )
    plan = ManagementActionPlan(
        organization_id=ctx.organization_id,
        bank_id=payload.bank_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        status="draft",
        version=1,
        created_by=ctx.actor_user_id,
    )
    db.add(plan)
    db.flush()
    _add_items(db, ctx, plan.id, payload.actions)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "plan_code_exists",
                "message": f"A management-action plan with code '{payload.code}' already exists.",
            },
        ) from exc
    record_event(
        db,
        ctx,
        event_type="management_action_plan.created",
        entity_type="management_action_plan",
        entity_id=plan.id,
        details={"code": payload.code, "bank_id": payload.bank_id, "reason": payload.reason},
    )
    db.commit()
    return _read(plan, _load_items(db, plan.id))


def list_plans(
    db: Session,
    ctx: TenantContext,
    bank_id: str | None = None,
    status_filter: str | None = None,
    include_archived: bool = False,
) -> ManagementActionPlanListRead:
    conditions = [ManagementActionPlan.organization_id == ctx.organization_id]
    if bank_id is not None:
        conditions.append(ManagementActionPlan.bank_id == bank_id)
    if status_filter is not None:
        conditions.append(ManagementActionPlan.status == status_filter)
    elif not include_archived:
        conditions.append(ManagementActionPlan.status != "archived")
    plans = list(
        db.scalars(
            select(ManagementActionPlan).where(*conditions).order_by(ManagementActionPlan.code)
        )
    )
    counts: dict[UUID, int] = {}
    if plans:
        rows = db.execute(
            select(ManagementActionItem.plan_id, func.count())
            .where(ManagementActionItem.plan_id.in_([p.id for p in plans]))
            .group_by(ManagementActionItem.plan_id)
        ).all()
        counts = {row[0]: int(row[1]) for row in rows}
    summaries = [
        ManagementActionPlanSummaryRead(
            id=plan.id,
            bank_id=plan.bank_id,
            code=plan.code,
            name=plan.name,
            status=plan.status,  # pyright: ignore[reportArgumentType]
            version=plan.version,
            action_count=int(counts.get(plan.id, 0)),
            created_by=plan.created_by,
            approved_by=plan.approved_by,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
        for plan in plans
    ]
    return ManagementActionPlanListRead(plans=summaries, total=len(summaries))


def get_plan(db: Session, ctx: TenantContext, plan_id: UUID) -> ManagementActionPlanRead:
    plan = _get_plan_or_404(db, ctx, plan_id)
    return _read(plan, _load_items(db, plan.id))


def update_plan(
    db: Session, ctx: TenantContext, plan_id: UUID, payload: ManagementActionPlanUpdate
) -> ManagementActionPlanRead:
    plan = _get_plan_or_404(db, ctx, plan_id)
    if plan.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_editable",
                "message": f"Only draft plans can be edited; this plan is '{plan.status}'.",
            },
        )
    if payload.name is not None:
        plan.name = payload.name
    if payload.description is not None:
        plan.description = payload.description
    if payload.actions is not None:
        for existing in _load_items(db, plan.id):
            db.delete(existing)
        db.flush()
        _add_items(db, ctx, plan.id, payload.actions)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="management_action_plan.updated",
        entity_type="management_action_plan",
        entity_id=plan.id,
        details={"code": plan.code, "reason": payload.reason},
    )
    db.commit()
    return _read(plan, _load_items(db, plan.id))


def submit_plan(
    db: Session, ctx: TenantContext, plan_id: UUID, payload: ManagementActionPlanTransition
) -> ManagementActionPlanRead:
    plan = _get_plan_or_404(db, ctx, plan_id)
    if plan.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_submittable",
                "message": (
                    f"Only a draft can be submitted for approval; this plan is '{plan.status}'."
                ),
            },
        )
    plan.status = "pending_approval"
    record_event(
        db,
        ctx,
        event_type="management_action_plan.submitted",
        entity_type="management_action_plan",
        entity_id=plan.id,
        details={"code": plan.code, "reason": payload.reason},
    )
    db.commit()
    return _read(plan, _load_items(db, plan.id))


def approve_plan(
    db: Session, ctx: TenantContext, plan_id: UUID, payload: ManagementActionPlanApproval
) -> ManagementActionPlanRead:
    plan = _get_plan_or_404(db, ctx, plan_id)
    if plan.status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "not_pending_approval",
                "message": (
                    f"Only a plan pending approval can be approved; this plan is '{plan.status}'."
                ),
            },
        )
    if plan.created_by is not None and plan.created_by == ctx.actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "maker_is_checker",
                "message": "The plan's creator cannot approve it (maker ≠ checker).",
            },
        )
    plan.status = "approved"
    plan.approved_by = ctx.actor_user_id
    plan.approval_timestamp = utc_now()
    plan.version = plan.version + 1
    record_event(
        db,
        ctx,
        event_type="management_action_plan.approved",
        entity_type="management_action_plan",
        entity_id=plan.id,
        details={"code": plan.code, "version": plan.version, "reason": payload.reason},
    )
    db.commit()
    return _read(plan, _load_items(db, plan.id))


def archive_plan(
    db: Session, ctx: TenantContext, plan_id: UUID, payload: ManagementActionPlanTransition
) -> ManagementActionPlanRead:
    plan = _get_plan_or_404(db, ctx, plan_id)
    if plan.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "already_archived",
                "message": "This plan is already archived.",
            },
        )
    plan.status = "archived"
    record_event(
        db,
        ctx,
        event_type="management_action_plan.archived",
        entity_type="management_action_plan",
        entity_id=plan.id,
        details={"code": plan.code, "reason": payload.reason},
    )
    db.commit()
    return _read(plan, _load_items(db, plan.id))


def resolve_for_official_run(
    db: Session, ctx: TenantContext, plan_id: UUID
) -> ManagementActionPlan:
    """Return an APPROVED plan, or refuse (the official-run consumption guard)."""
    plan = _get_plan_or_404(db, ctx, plan_id)
    if plan.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "plan_not_approved",
                "message": (
                    f"Only an approved management-action plan may be consumed by an official "
                    f"run; this plan is '{plan.status}'."
                ),
            },
        )
    return plan


def build_domain_plan(db: Session, plan: ManagementActionPlan) -> DomainPlan:
    """Convert the stored plan rows into the pure domain plan (no inference)."""
    items = _load_items(db, plan.id)
    actions: list[ManagementAction] = []
    for item in items:
        kwargs: dict[str, Any] = {
            "action_id": item.action_id,
            "kind": item.kind,
            "label": item.label,
            "trigger": ActionTrigger(
                kind=item.trigger_kind,  # pyright: ignore[reportArgumentType]
                watch_minima=tuple(item.watch_minima) if item.watch_minima else (),
                min_severity=item.min_severity,
            ),
            "effective_year": item.effective_year,
            "capital_raise_ghs": Decimal(item.capital_raise_ghs),
            "capital_raise_tier": item.capital_raise_tier,
            "counts_as_paid_up": item.counts_as_paid_up,
            "sizing": item.sizing,
            "dividend_reduction_pct": Decimal(item.dividend_reduction_pct),
            "rwa_reduction_ghs": Decimal(item.rwa_reduction_ghs),
            "shrinks_leverage_exposure": item.shrinks_leverage_exposure,
            "rationale": item.rationale or "",
        }
        if item.severity_factors:
            kwargs["severity_factors"] = {
                k: Decimal(str(v)) for k, v in item.severity_factors.items()
            }
        actions.append(ManagementAction(**kwargs))
    return DomainPlan(plan_id=plan.code, name=plan.name, actions=tuple(actions))


def plan_snapshot(db: Session, plan: ManagementActionPlan) -> dict[str, Any]:
    """A value-based snapshot of the plan for the run's ``input_hash``."""
    items = _load_items(db, plan.id)
    return {
        "code": plan.code,
        "version": plan.version,
        "actions": sorted(
            (
                {
                    "action_id": item.action_id,
                    "kind": item.kind,
                    "trigger_kind": item.trigger_kind,
                    "watch_minima": sorted(item.watch_minima) if item.watch_minima else [],
                    "min_severity": item.min_severity,
                    "effective_year": item.effective_year,
                    "capital_raise_ghs": str(Decimal(item.capital_raise_ghs)),
                    "capital_raise_tier": item.capital_raise_tier,
                    "counts_as_paid_up": item.counts_as_paid_up,
                    "sizing": item.sizing,
                    "dividend_reduction_pct": str(Decimal(item.dividend_reduction_pct)),
                    "rwa_reduction_ghs": str(Decimal(item.rwa_reduction_ghs)),
                    "shrinks_leverage_exposure": item.shrinks_leverage_exposure,
                    "severity_factors": (
                        {k: str(Decimal(str(v))) for k, v in item.severity_factors.items()}
                        if item.severity_factors
                        else None
                    ),
                }
                for item in items
            ),
            key=lambda entry: entry["action_id"],
        ),
    }
