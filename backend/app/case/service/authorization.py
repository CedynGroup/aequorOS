"""Organization-wide authority for the advisory case workspace."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    Sensitivity,
)
from app.core.observability import authorization_denied
from app.core.tenancy import TenantContext
from app.identity import public as authorization


def _permission_result(
    db: Session,
    ctx: TenantContext,
    permission: Permission,
    sensitivity: Sensitivity,
) -> tuple[bool, str]:
    allowed = False
    reason = "human_scoped_binding_required"
    if ctx.actor_user_id is not None and ctx.authorization_version is not None:
        principal = PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.HUMAN)
        resource = ResourceLocator(
            ctx.organization_id, InstitutionScope.ORGANIZATION, None, Module.RISK, sensitivity
        )
        try:
            decision = authorization.evaluate_permission(db, principal, permission, resource)
            if decision.allowed:
                scope = authorization.effective_data_scope(
                    db,
                    organization_id=ctx.organization_id,
                    binding_ids=decision.matching_binding_ids,
                )
                decision = authorization.institution_grain_decision(
                    decision, whole_institution=scope.whole_institution
                )
            authorization.record_binding_decision(
                decision,
                surface="case_workspace",
                severity="info" if decision.allowed else "warning",
            )
            allowed, reason = decision.allowed, decision.reason
        except Exception as exc:  # noqa: BLE001 - evaluator errors refuse before side effects
            authorization.record_binding_evaluation_failure(
                principal, permission, resource, surface="case_workspace", error=exc
            )
            reason = "binding_evaluation_failed"
    return allowed, reason


def has_restricted_view(db: Session, ctx: TenantContext) -> bool:
    allowed, _ = _permission_result(db, ctx, Permission.VIEW, Sensitivity.RESTRICTED)
    return allowed


def require_permission(
    db: Session,
    ctx: TenantContext,
    permission: Permission,
    sensitivity: Sensitivity,
    *,
    opaque: bool,
) -> None:
    allowed, reason = _permission_result(db, ctx, permission, sensitivity)
    if allowed:
        return
    authorization_denied(
        reason=reason,
        organization_id=ctx.organization_id,
        module=Module.RISK.value,
        sensitivity=sensitivity.value,
        permission=permission.value,
        surface="case_workspace",
    )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND if opaque else status.HTTP_403_FORBIDDEN,
        detail="Case resource not found." if opaque else authorization.DEFAULT_DENIAL_DETAIL,
    )
