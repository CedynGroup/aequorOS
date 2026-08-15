from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankFinancialFact, BankReportingPeriod, Jurisdiction
from app.schemas.banks import (
    BankFactRead,
    BankFactsRead,
    BankListRead,
    BankRead,
    BankReportingPeriodListRead,
    BankReportingPeriodRead,
    JurisdictionRead,
)
from app.services.public_ids import normalize_public_id

_FACT_GROUP_FIELDS: dict[str, str] = {
    "balance_sheet": "balance_sheet",
    "loan_exposure": "loan_exposures",
    "securities": "securities",
    "off_balance": "off_balance",
    "lcr_inflow": "lcr_inflows",
    "market_risk": "market_risk",
    "operational_income": "operational_income",
    "cashflow": "cash_flows",
    "capital_component": "capital_components",
    "deposit_behavior": "deposit_behavior",
}


def _jurisdictions_by_code(db: Session, codes: set[str]) -> dict[str, JurisdictionRead]:
    """Resolve registry rows for the given codes (global reference data)."""
    if not codes:
        return {}
    rows = db.scalars(select(Jurisdiction).where(Jurisdiction.code.in_(codes)))
    return {row.code: JurisdictionRead.model_validate(row, from_attributes=True) for row in rows}


def _bank_read(bank: Bank, registry: dict[str, JurisdictionRead]) -> BankRead:
    read = BankRead.model_validate(bank, from_attributes=True)
    return read.model_copy(update={"jurisdiction": registry.get(bank.jurisdiction_code)})


def list_banks(db: Session, ctx: TenantContext) -> BankListRead:
    banks = list(
        db.scalars(
            select(Bank)
            .where(Bank.organization_id == ctx.organization_id)
            .order_by(Bank.name, Bank.id)
        )
    )
    registry = _jurisdictions_by_code(db, {bank.jurisdiction_code for bank in banks})
    return BankListRead(banks=[_bank_read(bank, registry) for bank in banks])


def get_bank(db: Session, ctx: TenantContext, bank_reference: str) -> BankRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
    registry = _jurisdictions_by_code(db, {bank.jurisdiction_code})
    return _bank_read(bank, registry)


def list_reporting_periods(
    db: Session, ctx: TenantContext, bank_reference: str
) -> BankReportingPeriodListRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
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
    db: Session, ctx: TenantContext, bank_reference: str, period_id: UUID
) -> BankFactsRead:
    bank = resolve_bank_reference(db, ctx, bank_reference)
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


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def resolve_bank_reference(db: Session, ctx: TenantContext, reference: str) -> Bank:
    """Resolve a bank path token — the institution ID (BK-XXXXXXXX).

    Canonical form is uppercase; lowercase input from integrations is
    tolerated. Lookup is tenant-scoped.
    """
    return _get_bank_or_404(db, ctx, normalize_public_id(reference))
