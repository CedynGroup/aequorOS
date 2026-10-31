"""Exact scope decisions for the governed stress libraries and sign-off."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    AuthorizationDecision,
    ConditionCheck,
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    Sensitivity,
)
from app.core.tenancy import TenantContext
from app.identity import public as authorization

RESOURCE_NOT_FOUND_DETAIL = "Stress resource not found."


def evaluate(  # noqa: PLR0913 - the resource and action are explicit
    db: Session,
    ctx: TenantContext,
    bank_id: str | None,
    permission: Permission,
    *,
    module: Module = Module.RISK,
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
    conditions: tuple[ConditionCheck, ...] = (),
) -> AuthorizationDecision | None:
    """NULL is an organization resource, never an unspecified bank."""
    if bank_id is not None:
        bank = authorization.resolve_bank(db, ctx, bank_id, module=module)
        decision = authorization.evaluate_bank_permission(
            db,
            ctx,
            bank,
            permission=permission,
            module=module,
            sensitivity=sensitivity,
            surface="stress_governance",
            conditions=conditions,
        )
        return decision
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        return None
    principal = PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.HUMAN)
    resource = ResourceLocator(
        ctx.organization_id, InstitutionScope.ORGANIZATION, None, module, sensitivity
    )
    try:
        decision = authorization.evaluate_permission(
            db, principal, permission, resource, conditions=conditions
        )
        if decision.allowed:
            scope = authorization.effective_data_scope(
                db,
                organization_id=ctx.organization_id,
                binding_ids=decision.matching_binding_ids,
            )
            decision = authorization.institution_grain_decision(
                decision, whole_institution=scope.whole_institution
            )
    except Exception as exc:  # noqa: BLE001 - evaluator failures deny closed
        authorization.record_binding_evaluation_failure(
            principal, permission, resource, surface="stress_governance", error=exc
        )
        return None
    authorization.record_binding_decision(
        decision,
        surface="stress_governance",
        severity="info" if decision.allowed else "warning",
    )
    return decision


def allowed(
    db: Session,
    ctx: TenantContext,
    bank_id: str | None,
    permission: Permission,
) -> bool:
    decision = evaluate(db, ctx, bank_id, permission)
    return decision is not None and decision.allowed


def require_permission(  # noqa: PLR0913 - complete policy tuple plus refusal mode
    db: Session,
    ctx: TenantContext,
    bank_id: str | None,
    permission: Permission,
    *,
    module: Module = Module.RISK,
    sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
    opaque: bool = False,
    conditions: tuple[ConditionCheck, ...] = (),
) -> None:
    decision = evaluate(
        db, ctx, bank_id, permission, module=module, sensitivity=sensitivity, conditions=conditions
    )
    if decision is not None and decision.reason == "condition_denied:maker_checker":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "maker_is_checker",
                "message": "The creator cannot approve their own work.",
            },
        )
    if decision is None or not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if opaque else status.HTTP_403_FORBIDDEN,
            detail=RESOURCE_NOT_FOUND_DETAIL if opaque else authorization.DEFAULT_DENIAL_DETAIL,
        )


def visible_scopes(db: Session, ctx: TenantContext) -> tuple[bool, frozenset[str]]:
    """Evaluate organization and exact-bank rows independently before aggregation."""
    organization_visible = allowed(db, ctx, None, Permission.VIEW)
    banks = db.scalars(
        select(authorization.Bank).where(authorization.Bank.organization_id == ctx.organization_id)
    )
    return organization_visible, frozenset(
        bank.id for bank in banks if allowed(db, ctx, bank.id, Permission.VIEW)
    )
