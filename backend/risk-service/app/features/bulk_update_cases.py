from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.schemas.cases import CaseBulkActionCreate, CaseBulkActionRead
from app.services import cases as cases_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("/bulk-actions", response_model=CaseBulkActionRead)
def bulk_case_actions(
    payload: CaseBulkActionCreate, db: DbSession, ctx: Tenant
) -> CaseBulkActionRead:
    result = cases_service.bulk_case_action(db, ctx, payload.to_command())
    return CaseBulkActionRead.from_result(result)
