from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.identity.schemas.banks import (
    BankFactsRead,
    BankListRead,
    BankRead,
    BankReference,
    BankReportingPeriodListRead,
)
from app.identity.service import banks

router = APIRouter(tags=["banks"])


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
