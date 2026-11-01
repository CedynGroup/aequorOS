"""Exact human DATA authority for bank ingestion and governance surfaces."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.authorization import ConditionCheck, ConditionKind, Module, Permission, Sensitivity
from app.core.tenancy import TenantContext
from app.identity.public import Bank, require_bank_permission


def require_data_access(  # noqa: PLR0913 - one explicit authorization sentence
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    permission: Permission,
    sensitivity: Sensitivity,
    *,
    opaque: bool = False,
    maker_user_id: UUID | None = None,
    historical_maker: bool = False,
) -> Bank:
    """Authorize before reading rows, aggregating, or performing an effect."""
    if ctx.integration_key_id is not None or (
        permission is not Permission.VIEW and ctx.impersonation_context is not None
    ):
        raise HTTPException(status_code=404 if opaque else 403, detail="Data access refused.")
    return require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=permission,
        module=Module.DATA,
        sensitivity=sensitivity,
        surface="data_engine",
        conditions=(
            ConditionCheck(
                ConditionKind.MAKER_CHECKER,
                ctx.actor_user_id is not None
                and (maker_user_id is not None or historical_maker)
                and ctx.actor_user_id != maker_user_id,
                "A different authenticated officer is required.",
            ),
        )
        if permission is Permission.APPROVE
        else (),
        denial_status=404 if opaque else 403,
        denial_detail="Data object not found."
        if opaque
        else "This action requires an active DATA binding.",
    )
