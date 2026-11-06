"""Exact human REG authority for channel-config and reporting-settings writes.

Configuration of the submission channel and the deadline-override map is a
duty of its own (VAL-CFG-007): a complete active ``reg/configure/restricted``
exact-bank binding, evaluated BEFORE any credential encryption, row mutation
or audit row. Machine principals and impersonated sessions never hold a human
configuration duty and are refused outright. Denial statuses follow the module
split the DATA/MARKETS cutovers established: a principal who can see the bank
but holds no reg configure right is refused (403), and a principal with no
effective coverage for the bank in ANY module is told nothing about it
(opaque 404).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authorization import Module, Permission, Sensitivity
from app.core.tenancy import TenantContext
from app.identity.public import (
    Bank,
    require_institution_coverage,
    require_resolved_bank_permission,
)

REG_DENIAL_DETAIL = "This action requires an active Regulatory Reporting binding."


def require_reporting_configure_access(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    """Authorize one exact bank before any REG configuration side effect."""
    if ctx.integration_key_id is not None or ctx.impersonation_context is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=REG_DENIAL_DETAIL
        )
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.CONFIGURE,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="reporting_configure",
        denial_detail=REG_DENIAL_DETAIL,
    )
    return bank
