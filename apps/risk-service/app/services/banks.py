from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankFinancialFact, BankReportingPeriod
from app.schemas.banks import (
    BankFactRead,
    BankFactsRead,
    BankListRead,
    BankRead,
    BankReportingPeriodListRead,
    BankReportingPeriodRead,
    BankSeedSummaryRead,
)
from app.services.audit import record_event
from app.services.sample_bank_seed import DEMO_ORG_ID, seed_sample_bank

_FACT_GROUP_FIELDS: dict[str, str] = {
    "balance_sheet": "balance_sheet",
    "loan_exposure": "loan_exposures",
    "securities": "securities",
    "off_balance": "off_balance",
    "lcr_inflow": "lcr_inflows",
    "market_risk": "market_risk",
    "operational_income": "operational_income",
    "capital_component": "capital_components",
    "deposit_behavior": "deposit_behavior",
}


def list_banks(db: Session, ctx: TenantContext) -> BankListRead:
    banks = list(
        db.scalars(
            select(Bank)
            .where(Bank.organization_id == ctx.organization_id)
            .order_by(Bank.name, Bank.id)
        )
    )
    return BankListRead(
        banks=[BankRead.model_validate(bank, from_attributes=True) for bank in banks]
    )


def get_bank(db: Session, ctx: TenantContext, bank_id: UUID) -> BankRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    return BankRead.model_validate(bank, from_attributes=True)


def list_reporting_periods(
    db: Session, ctx: TenantContext, bank_id: UUID
) -> BankReportingPeriodListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    periods = list(
        db.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
    )
    return BankReportingPeriodListRead(
        bank_id=bank.id,
        periods=[
            BankReportingPeriodRead.model_validate(period, from_attributes=True)
            for period in periods
        ],
    )


def get_period_facts(
    db: Session, ctx: TenantContext, bank_id: UUID, period_id: UUID
) -> BankFactsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    facts = db.scalars(
        select(BankFinancialFact)
        .where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
        )
        .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
    )
    grouped: dict[str, list[BankFactRead]] = {field: [] for field in _FACT_GROUP_FIELDS.values()}
    for fact in facts:
        # Analytical-overlay groups (irr_*, fx_*, ftp_*) are surfaced by their own
        # module dashboards, not this canonical balance-sheet facts view.
        field = _FACT_GROUP_FIELDS.get(fact.fact_group)
        if field is None:
            continue
        grouped[field].append(BankFactRead.model_validate(fact, from_attributes=True))
    return BankFactsRead(
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        **grouped,
    )


def seed_demo(db: Session, ctx: TenantContext) -> BankSeedSummaryRead:
    if ctx.organization_id != DEMO_ORG_ID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo bank seeding is only available to the demo organization.",
        )
    summary = seed_sample_bank(db)
    record_event(
        db,
        ctx,
        event_type="bank_seed.completed",
        entity_type="bank",
        entity_id=summary.bank_id,
        details={
            "periods": summary.periods,
            "fact_count": summary.fact_count,
            "param_count": summary.param_count,
        },
    )
    db.commit()
    return BankSeedSummaryRead(
        bank_id=summary.bank_id,
        periods=summary.periods,
        fact_count=summary.fact_count,
        param_count=summary.param_count,
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
