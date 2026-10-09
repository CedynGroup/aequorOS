"""Check NOP currency coverage against accepted sources and official facts."""

from collections.abc import Collection
from datetime import date
from typing import cast

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.tenancy import TenantContext
from app.data_engine.public import CanonicalPosition, CanonicalPositionSnapshot
from app.domain.positions.fx import (
    FX_POSITION_TYPES,
    position_currencies,
    require_currency_coverage,
)
from app.identity.public import Bank
from app.live.position_book import INCLUDED_VALIDATION_STATUSES
from app.models.regulatory import BankFinancialFact, BankReportingPeriod
from app.models.regulatory_run import RegulatoryRun
from app.policy.public import base_currency


def required_fx_currencies(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> set[str]:
    """Notice BG/FMD/2026/07 ¶1(a)–(b): all accepted currencies, history or not."""
    return required_fx_currencies_by_date(db, ctx, bank, (as_of,))[as_of]


def required_fx_currencies_by_date(
    db: Session, ctx: TenantContext, bank: Bank, dates: Collection[date]
) -> dict[date, set[str]]:
    """Load accepted currency coverage for the dashboard window in one query."""
    currencies: dict[date, set[str]] = {as_of: set() for as_of in dates}
    if not currencies:
        return currencies
    hedge = CanonicalPosition.position_type == "FX_HEDGE"
    rows = db.execute(
        select(
            CanonicalPositionSnapshot.as_of_date,
            CanonicalPosition.position_type,
            CanonicalPosition.currency,
            case((hedge, CanonicalPositionSnapshot.attributes["sell_currency"].as_string())),
            case((hedge, CanonicalPositionSnapshot.attributes["buy_currency"].as_string())),
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date.in_(dates),
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type.in_(FX_POSITION_TYPES),
            or_(
                hedge,
                func.upper(func.trim(CanonicalPosition.currency)).not_in(
                    (base_currency(bank).strip().upper(), "")
                ),
            ),
        )
        .distinct()
    ).tuples()
    for as_of, position_type, currency, sell, buy in cast(
        Collection[tuple[date, str, str, str | None, str | None]], rows.all()
    ):
        currencies[as_of].update(
            position_currencies(
                position_type,
                currency,
                {"sell_currency": sell, "buy_currency": buy},
                base_currency(bank),
            )
        )
    return currencies


def require_fx_fact_coverage(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, currencies: Collection[str]
) -> None:
    require_currency_coverage(required_fx_currencies(db, ctx, bank, as_of), currencies)


def require_fx_run_coverage(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod, run: RegulatoryRun
) -> None:
    """Do not reuse a successful run that omitted a current currency."""
    expected = required_fx_currencies(db, ctx, bank, period.period_end)
    facts = db.scalars(
        select(BankFinancialFact).where(
            BankFinancialFact.organization_id == ctx.organization_id,
            BankFinancialFact.bank_id == bank.id,
            BankFinancialFact.reporting_period_id == period.id,
            BankFinancialFact.fact_group == "fx_position",
        )
    )
    for fact in facts:
        attrs = cast(dict[str, object], fact.attributes)
        expected.add(str(attrs.get("currency") or fact.category).strip().upper())
    require_run_currency_coverage(cast(dict[str, object], run.metrics), expected)


def require_run_currency_coverage(metrics: dict[str, object], expected: Collection[str]) -> None:
    """Check a stored run using the caller's complete, prefetched currency basis."""
    represented: set[str] = set()
    raw = metrics.get("currencies")
    if isinstance(raw, list):
        for row in cast(list[object], raw):
            if isinstance(row, dict):
                attrs = cast(dict[str, object], row)
                represented.add(str(attrs.get("currency") or "").strip().upper())
    flat = metrics.get("rate_not_required_currencies")
    if isinstance(flat, list):
        represented.update(str(currency).strip().upper() for currency in cast(list[object], flat))
    require_currency_coverage(expected, represented)
