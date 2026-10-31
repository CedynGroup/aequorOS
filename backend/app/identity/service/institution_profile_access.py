"""Exact Institution authority for corporate register sections and mutations."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import Module, Permission, Sensitivity
from app.core.tenancy import TenantContext
from app.identity.models.bank import Bank
from app.identity.service.scoped_authorization import (
    evaluate_bank_permission,
    require_bank_permission,
    resolve_bank,
)


@dataclass(frozen=True)
class RegisterAccess:
    bank: Bank
    general: bool
    restricted: bool


def view_access(db: Session, ctx: TenantContext, bank_id: str) -> RegisterAccess:
    bank = resolve_bank(db, ctx, bank_id, module=Module.INSTITUTION)
    general = evaluate_bank_permission(
        db,
        ctx,
        bank,
        module=Module.INSTITUTION,
        sensitivity=Sensitivity.CONFIDENTIAL,
        permission=Permission.VIEW,
        surface="institution_profile.general",
    )
    restricted = evaluate_bank_permission(
        db,
        ctx,
        bank,
        module=Module.INSTITUTION,
        sensitivity=Sensitivity.RESTRICTED,
        permission=Permission.VIEW,
        surface="institution_profile.restricted",
    )
    access = RegisterAccess(
        bank=bank,
        general=general is not None and general.allowed,
        restricted=restricted is not None and restricted.allowed,
    )
    if not access.general and not access.restricted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Institution view authority is required for this register.",
        )
    return access


def require_mutation(  # noqa: PLR0913 - the explicit authorization dimensions
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    permission: Permission,
    sensitivity: Sensitivity,
    hide_object: bool = False,
) -> Bank:
    return require_bank_permission(
        db,
        ctx,
        bank_id,
        module=Module.INSTITUTION,
        sensitivity=sensitivity,
        permission=permission,
        surface="institution_profile.mutation",
        denial_status=status.HTTP_404_NOT_FOUND if hide_object else status.HTTP_403_FORBIDDEN,
        denial_detail="Institution record not found."
        if hide_object
        else "This action requires exact Institution authority.",
    )
