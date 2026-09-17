"""Transitional authorization helpers for shared scenario-workbench routes."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, get_mutation_tenant_context
from app.core.authorization import ConditionCheck, Module, Permission, Sensitivity
from app.models import Bank
from app.services import scoped_authorization


def mutation_context(module: str, ctx: TenantContext) -> TenantContext:
    """Use exact-binding mutation authority for Liquidity only."""

    if module == "liquidity":
        return ctx
    return get_mutation_tenant_context(ctx)


def require_liquidity_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module: str,
    *,
    permission: Permission,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = scoped_authorization.DEFAULT_DENIAL_DETAIL,
) -> None:
    """Enforce exact LIQ authority while other modules retain legacy gates."""

    if module != "liquidity":
        return
    scoped_authorization.require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=Module.LIQUIDITY,
        sensitivity=sensitivity,
        surface=surface,
        conditions=conditions,
        denial_status=denial_status,
        denial_detail=denial_detail,
    )
