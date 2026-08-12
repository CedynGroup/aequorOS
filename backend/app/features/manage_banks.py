from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.deps import DbSession, Tenant
from app.schemas.banks import (
    BankFactsRead,
    BankListRead,
    BankRead,
    BankReportingPeriodListRead,
)
from app.services import banks

router = APIRouter(tags=["banks"])

# The institution ID (BK-XXXXXXXX) — the banks primary key; resolution is
# tenant-scoped and tolerates lowercase input (services/banks.py).
BankReference = Annotated[
    str,
    Path(
        description="Institution ID (BK-XXXXXXXX).",
        # Generous bound: unknown/legacy-shaped references fall through to a
        # clean 404 from the lookup rather than a 422 format error.
        max_length=64,
    ),
]


@router.get("/banks", response_model=BankListRead, operation_id="listBanks")
def list_banks(db: DbSession, ctx: Tenant) -> BankListRead:
    return banks.list_banks(db, ctx)


@router.get("/banks/{bank_id}", response_model=BankRead, operation_id="getBank")
def get_bank(bank_id: BankReference, db: DbSession, ctx: Tenant) -> BankRead:
    return banks.get_bank(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/reporting-periods",
    response_model=BankReportingPeriodListRead,
    operation_id="listBankReportingPeriods",
)
def list_bank_reporting_periods(
    bank_id: BankReference, db: DbSession, ctx: Tenant
) -> BankReportingPeriodListRead:
    return banks.list_reporting_periods(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/reporting-periods/{period_id}/facts",
    response_model=BankFactsRead,
    operation_id="getBankPeriodFacts",
)
def get_bank_period_facts(
    bank_id: BankReference, period_id: UUID, db: DbSession, ctx: Tenant
) -> BankFactsRead:
    return banks.get_period_facts(db, ctx, bank_id, period_id)
