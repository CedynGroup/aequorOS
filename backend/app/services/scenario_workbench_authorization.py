"""Transitional authorization helpers for shared scenario-workbench routes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext, get_mutation_tenant_context
from app.core.authorization import ConditionCheck, Module, Permission, Sensitivity
from app.models import Bank
from app.schemas.scenario_workbench import WorkbenchModule
from app.services import scoped_authorization


@dataclass(frozen=True)
class _WorkbenchAuthorizationPolicy:
    module: Module
    surface_prefix: str
    saved_analysis_list_sensitivity: Sensitivity


_WORKBENCH_POLICIES: dict[WorkbenchModule, _WorkbenchAuthorizationPolicy] = {
    "liquidity": _WorkbenchAuthorizationPolicy(
        Module.LIQUIDITY,
        "liquidity",
        Sensitivity.AGGREGATED,
    ),
    "irr": _WorkbenchAuthorizationPolicy(
        Module.IRRBB,
        "irrbb",
        Sensitivity.CONFIDENTIAL,
    ),
}


def mutation_context(module: WorkbenchModule, ctx: TenantContext) -> TenantContext:
    """Use exact-binding mutation authority for cut-over workbench modules."""

    if module in _WORKBENCH_POLICIES:
        return ctx
    return get_mutation_tenant_context(ctx)


def surface(module: WorkbenchModule, action: str) -> str:
    """Name one module-specific workbench enforcement surface."""

    prefix = _WORKBENCH_POLICIES.get(module)
    return f"{prefix.surface_prefix if prefix is not None else module}_{action}"


def saved_analysis_list_sensitivity(module: WorkbenchModule) -> Sensitivity:
    """Return the sensitivity of each cut-over module's saved-analysis index."""

    policy = _WORKBENCH_POLICIES.get(module)
    return policy.saved_analysis_list_sensitivity if policy is not None else Sensitivity.AGGREGATED


def require_module_permission(  # noqa: PLR0913 - complete authorization sentence
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    module: WorkbenchModule,
    *,
    permission: Permission,
    sensitivity: Sensitivity,
    surface: str,
    conditions: Sequence[ConditionCheck] = (),
    denial_status: int = status.HTTP_403_FORBIDDEN,
    denial_detail: str = scoped_authorization.DEFAULT_DENIAL_DETAIL,
) -> None:
    """Enforce exact authority for cut-over modules and preserve legacy gates."""

    policy = _WORKBENCH_POLICIES.get(module)
    if policy is None:
        return
    scoped_authorization.require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=policy.module,
        sensitivity=sensitivity,
        surface=surface,
        conditions=conditions,
        denial_status=denial_status,
        denial_detail=denial_detail,
    )
