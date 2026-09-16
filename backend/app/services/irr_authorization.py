"""Exact scoped-binding enforcement for tenant IRRBB surfaces."""

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
    ResourceLocator,
    Sensitivity,
)
from app.core.observability import authorization_denied, cross_tenant_attempt
from app.models import Bank
from app.services import authorization
from app.services.public_ids import normalize_public_id

IRRBB_DENIAL_DETAIL = "IRRBB access requires an active scoped binding."


def resolve_bank(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    """Resolve one tenant-owned institution without leaking foreign rows."""

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
            module=Module.IRRBB.value,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def require_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    permission: Permission,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = IRRBB_DENIAL_DETAIL,
) -> AuthorizationDecision:
    """Require one independently complete active IRRBB binding."""

    if ctx.actor_user_id is None or ctx.authorization_version is None:
        authorization_denied(
            reason="human_scoped_binding_required",
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            module=Module.IRRBB.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=denial_status, detail=denial_detail)

    principal = authorization.principal_locator(ctx)
    resource = ResourceLocator(
        ctx.organization_id,
        InstitutionScope.INSTITUTION,
        bank.id,
        Module.IRRBB,
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
            module=Module.IRRBB.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=denial_status, detail=denial_detail) from exc

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
            module=Module.IRRBB.value,
            sensitivity=sensitivity.value,
            permission=permission.value,
            surface=surface,
        )
        raise HTTPException(status_code=denial_status, detail=denial_detail)
    return decision


def require_bank_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    permission: Permission,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = IRRBB_DENIAL_DETAIL,
) -> Bank:
    """Resolve one tenant institution, then enforce exact IRRBB authority."""

    bank = resolve_bank(db, ctx, bank_id)
    require_permission(
        db,
        ctx,
        bank,
        permission=permission,
        sensitivity=sensitivity,
        surface=surface,
        conditions=conditions,
        denial_status=denial_status,
        denial_detail=denial_detail,
    )
    return bank
