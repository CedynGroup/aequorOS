"""Shared scenario-workbench authorization: one scoped policy per engine module.

A module may ride the shared workbench routes only with an explicit scoped
policy here. A module without one is denied: the shared routes never fall back
to a scalar role ladder, because authority comes from complete scoped bindings
(module x permission x sensitivity x institution scope), never from token
roles.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, Module, Permission, Sensitivity
from app.identity import public as scoped_authorization
from app.identity.public import Bank
from app.stress.schemas.scenario_workbench import WorkbenchModule


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
    "capital": _WorkbenchAuthorizationPolicy(
        Module.CAPITAL,
        "capital",
        # Saved workbench analyses are engine snapshots; the capital rollout
        # contract pins workbench entries at confidential, like the IRR policy.
        Sensitivity.CONFIDENTIAL,
    ),
    "fx": _WorkbenchAuthorizationPolicy(
        Module.FX,
        "fx",
        Sensitivity.AGGREGATED,
    ),
    "irr": _WorkbenchAuthorizationPolicy(
        Module.IRRBB,
        "irrbb",
        Sensitivity.CONFIDENTIAL,
    ),
    "ftp": _WorkbenchAuthorizationPolicy(
        Module.FTP,
        "ftp",
        Sensitivity.AGGREGATED,
    ),
}


def _policy(module: WorkbenchModule) -> _WorkbenchAuthorizationPolicy:
    policy = _WORKBENCH_POLICIES.get(module)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=scoped_authorization.DEFAULT_DENIAL_DETAIL,
        )
    return policy


def mutation_context(module: WorkbenchModule, ctx: TenantContext) -> TenantContext:
    """Carry identity only; an unknown module is denied, never role-gated.

    The scoped decision itself happens in the service, after the bank lookup
    and before any side effect.
    """

    _policy(module)
    return ctx


def surface(module: WorkbenchModule, action: str) -> str:
    """Name one module-specific workbench enforcement surface."""

    prefix = _WORKBENCH_POLICIES.get(module)
    return f"{prefix.surface_prefix if prefix is not None else module}_{action}"


def saved_analysis_list_sensitivity(module: WorkbenchModule) -> Sensitivity:
    """Return the sensitivity of each module's saved-analysis index."""

    return _policy(module).saved_analysis_list_sensitivity


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
    """Enforce the module's exact scoped binding; deny closed without a policy."""

    policy = _policy(module)
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
