"""Operation-level authority for the examiner evidence routes (issue 180).

The dependency runs before the handler, so an unauthorized caller is refused
before a period, a run, a package or a register is read. The bank is resolved by
the router's shared ``resolve_tenant_bank`` dependency first, which keeps a
foreign institution indistinguishable from a missing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import DbSession, Tenant, TenantBank
from app.core.tenancy import TenantContext
from app.identity.public import Bank
from app.reporting.service import examiner_access


@dataclass(frozen=True)
class ExaminerEvidenceAccess:
    ctx: TenantContext
    bank: Bank


def require_examiner_evidence(
    db: DbSession,
    ctx: Tenant,
    bank: TenantBank,
) -> ExaminerEvidenceAccess:
    """The bundle's AUDIT/restricted ``view`` entry gate for one institution."""

    if bank is None:  # pragma: no cover - every examiner route is bank-scoped
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    examiner_access.require_evidence_view(db, ctx, bank)
    return ExaminerEvidenceAccess(ctx=ctx, bank=bank)


ExaminerEvidenceView = Annotated[ExaminerEvidenceAccess, Depends(require_examiner_evidence)]
