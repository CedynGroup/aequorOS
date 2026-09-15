"""Reusable institution-scoped authorization enforcement.

Product routes resolve the tenant-owned institution first, then evaluate one
complete binding against an explicit module, sensitivity, and permission. The
helpers deny closed on evaluator failures, emit the shared decision telemetry,
and never consult scalar role claims.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
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
from app.core.observability import authorization_denied, cross_tenant_attempt
from app.models import Bank
from app.services import authorization
from app.services.public_ids import normalize_public_id

DEFAULT_DENIAL_DETAIL = "This action requires an active scoped binding."


def resolve_bank(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    module: Module,
) -> Bank:
    """Resolve one tenant-owned institution without leaking cross-tenant rows."""

    normalized = normalize_public_id(bank_id)
    bank = db.scalar(
        select(Bank).where(
            Bank.id == normalized,
            Bank.organization_id == ctx.organization_id,
        )
    )
    if bank is None:
        cross_tenant_attempt(
            reason="bank_not_visible_to_tenant",
            organization_id=ctx.organization_id,
            bank_id=normalized,
            module=module.value,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def evaluate_bank_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    permission: Permission,
    module: Module,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
) -> AuthorizationDecision | None:
    """Evaluate and record one bank-scoped decision.

    ``None`` represents a principal that cannot hold an interactive human
    binding or an evaluator failure. Callers must treat it as denial.
    """

    if ctx.actor_user_id is None or ctx.authorization_version is None:
        authorization_denied(
            reason="human_scoped_binding_required",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            module=module.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
        return None
    principal = PrincipalLocator(
        ctx.organization_id,
        ctx.actor_user_id,
        PrincipalType.HUMAN,
    )
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        module,
        sensitivity,
    )
    try:
        decision = authorization.evaluate_permission(
            db,
            principal,
            permission,
            resource,
            conditions=tuple(conditions),
        )
    except Exception as exc:  # noqa: BLE001 - enforcement must deny closed
        authorization.record_binding_evaluation_failure(
            principal,
            permission,
            resource,
            surface=surface,
            error=exc,
        )
        authorization_denied(
            reason="binding_evaluation_failed",
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module=module.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
        return None
    authorization.record_binding_decision(
        decision,
        surface=surface,
        severity="info" if decision.allowed else "warning",
    )
    if not decision.allowed:
        authorization_denied(
            reason=decision.reason,
            organization_id=ctx.organization_id,
            actor_user_id=str(ctx.actor_user_id),
            bank_id=bank.id,
            module=module.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
    return decision


def require_resolved_bank_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    permission: Permission,
    module: Module,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = DEFAULT_DENIAL_DETAIL,
) -> AuthorizationDecision:
    """Require one complete binding for an already resolved institution."""

    decision = evaluate_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=module,
        sensitivity=sensitivity,
        surface=surface,
        conditions=conditions,
    )
    if decision is None or not decision.allowed:
        raise HTTPException(status_code=denial_status, detail=denial_detail)
    return decision


def require_bank_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    permission: Permission,
    module: Module,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = DEFAULT_DENIAL_DETAIL,
) -> Bank:
    """Resolve an institution and require one complete matching binding."""

    bank = resolve_bank(db, ctx, bank_id, module=module)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=module,
        sensitivity=sensitivity,
        surface=surface,
        conditions=conditions,
        denial_status=denial_status,
        denial_detail=denial_detail,
    )
    return bank
