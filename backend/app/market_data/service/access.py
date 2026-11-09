"""Exact human MARKETS authority for connection and source-preference writes."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.authorization import Module, Permission, Sensitivity
from app.core.tenancy import TenantContext
from app.identity.public import (
    Bank,
    require_institution_coverage,
    require_resolved_bank_permission,
    resolve_bank,
)


def resolve_markets_bank(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    if ctx.integration_key_id is None and ctx.impersonation_context is None:
        bank, _ = require_institution_coverage(db, ctx, bank_id)
        return bank
    return resolve_bank(db, ctx, bank_id, module=Module.MARKETS)


def require_markets_access(  # noqa: PLR0913 - one explicit authorization sentence
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    permission: Permission,
    sensitivity: Sensitivity,
    *,
    opaque: bool = False,
) -> Bank:
    """Authorize before reading rows, calling a vendor, or performing an effect."""
    if ctx.integration_key_id is not None or (
        permission is not Permission.VIEW and ctx.impersonation_context is not None
    ):
        raise HTTPException(status_code=404 if opaque else 403, detail="Markets access refused.")
    bank = resolve_markets_bank(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=Module.MARKETS,
        sensitivity=sensitivity,
        surface="market_data",
        denial_status=404 if opaque else 403,
        denial_detail="Market data object not found."
        if opaque
        else "This action requires an active MARKETS binding.",
    )
    return bank
