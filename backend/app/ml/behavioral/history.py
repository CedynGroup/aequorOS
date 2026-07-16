"""Per-bank canonical-history loaders — the training-data source for ANY bank.

Generalizes ``fact_derivation._load_canonical`` to a multi-period window (drops
the single ``as_of_date`` equality) and aggregates in SQL so a 100k+ position
book collapses to a few thousand product-month rows before it reaches Python.
``balance_ghs`` lives in the snapshot ``attributes`` JSON (mirrors
``_position_row``: fall back to ``balance`` for GHS books).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
)

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")


def months_before(as_of: datetime.date, n: int) -> datetime.date:
    total = (as_of.year * 12 + (as_of.month - 1)) - n
    year, month = divmod(total, 12)
    return datetime.date(year, month + 1, min(as_of.day, 28))


def _balance_ghs():
    """SQL expression for balance_ghs: attributes->>'balance_ghs' else balance."""
    return func.coalesce(
        cast(CanonicalPositionSnapshot.attributes["balance_ghs"].as_string(), Float),
        cast(CanonicalPositionSnapshot.balance, Float),
    )


def _attr_float(key: str):
    return cast(CanonicalPositionSnapshot.attributes[key].as_string(), Float)


@dataclass(frozen=True, slots=True)
class DepositMonthAgg:
    product_code: str
    as_of_date: datetime.date
    balance_ghs: float
    n_accounts: int
    avg_rate: float | None
    counterparty_type: str | None


@dataclass(frozen=True, slots=True)
class LoanMonthRow:
    source_reference: str
    as_of_date: datetime.date
    product_code: str | None
    balance_ghs: float
    scheduled_principal_ghs: float
    interest_rate: float | None
    contractual_maturity: datetime.date | None
    months_on_book: int | None


def available_as_of_dates(db: Session, ctx: TenantContext, bank_id: UUID) -> list[datetime.date]:
    """Distinct as-of dates with accepted position snapshots for this bank."""
    rows = db.execute(
        select(CanonicalPositionSnapshot.as_of_date)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .distinct()
        .order_by(CanonicalPositionSnapshot.as_of_date)
    ).all()
    return [r[0] for r in rows]


def load_deposit_month_aggregates(  # noqa: PLR0913
    db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date, window_months: int,
    *, non_maturing_only: bool,
) -> list[DepositMonthAgg]:
    """Deposit balances aggregated to (product, counterparty_type, month)."""
    start = months_before(as_of, window_months)
    conds = [
        CanonicalPositionSnapshot.organization_id == ctx.organization_id,
        CanonicalPositionSnapshot.bank_id == bank_id,
        CanonicalPositionSnapshot.as_of_date <= as_of,
        CanonicalPositionSnapshot.as_of_date >= start,
        CanonicalPositionSnapshot.superseded_by.is_(None),
        CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        CanonicalPosition.position_type == "DEPOSIT",
    ]
    if non_maturing_only:
        conds.append(CanonicalPositionSnapshot.contractual_maturity.is_(None))
    rows = db.execute(
        select(
            CanonicalProduct.product_code,
            CanonicalCounterparty.counterparty_type,
            CanonicalPositionSnapshot.as_of_date,
            func.sum(_balance_ghs()),
            func.count(),
            func.avg(cast(CanonicalPositionSnapshot.interest_rate, Float)),
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .where(*conds)
        .group_by(
            CanonicalProduct.product_code,
            CanonicalCounterparty.counterparty_type,
            CanonicalPositionSnapshot.as_of_date,
        )
    ).all()
    return [
        DepositMonthAgg(
            product_code=pc or "<none>", as_of_date=d, balance_ghs=float(bal or 0.0),
            n_accounts=int(n or 0), avg_rate=float(rate) if rate is not None else None,
            counterparty_type=ct,
        )
        for pc, ct, d, bal, n, rate in rows
    ]


def load_loan_month_rows(
    db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date, window_months: int,
) -> list[LoanMonthRow]:
    """Per-loan monthly snapshots (for prepayment delta computation)."""
    start = months_before(as_of, window_months)
    rows = db.execute(
        select(
            CanonicalPosition.source_reference,
            CanonicalPositionSnapshot.as_of_date,
            CanonicalProduct.product_code,
            _balance_ghs(),
            _attr_float("scheduled_principal_ghs"),
            cast(CanonicalPositionSnapshot.interest_rate, Float),
            CanonicalPositionSnapshot.contractual_maturity,
            cast(CanonicalPositionSnapshot.attributes["months_on_book"].as_string(), Float),
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank_id,
            CanonicalPositionSnapshot.as_of_date <= as_of,
            CanonicalPositionSnapshot.as_of_date >= start,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type == "LOAN",
        )
        .order_by(CanonicalPosition.source_reference, CanonicalPositionSnapshot.as_of_date)
    ).all()
    return [
        LoanMonthRow(
            source_reference=sref, as_of_date=d, product_code=pc,
            balance_ghs=float(bal or 0.0), scheduled_principal_ghs=float(sched or 0.0),
            interest_rate=float(rate) if rate is not None else None,
            contractual_maturity=mat,
            months_on_book=int(mob) if mob is not None else None,
        )
        for sref, d, pc, bal, sched, rate, mat, mob in rows
    ]


def load_ghs_short_rate_history(
    db: Session, ctx: TenantContext, bank_id: UUID, as_of: datetime.date, window_months: int,
) -> dict[datetime.date, float]:
    """Per-month GHS short rate (3m tenor) from the yield_curve reference dataset."""
    start = months_before(as_of, window_months)
    rows = db.execute(
        select(CanonicalReferenceRow.as_of_date, CanonicalReferenceRow.payload).where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank_id,
            CanonicalReferenceRow.dataset_kind == "yield_curve",
            CanonicalReferenceRow.as_of_date <= as_of,
            CanonicalReferenceRow.as_of_date >= start,
        )
    ).all()
    have_3m: set[datetime.date] = set()
    out: dict[datetime.date, float] = {}
    for d, payload in rows:
        try:
            if str(payload.get("currency")) != "GHS":
                continue
            tenor = float(payload.get("tenor_months"))
            rate = float(payload.get("rate"))
        except (TypeError, ValueError):
            continue
        if tenor not in (1.0, 3.0):
            continue
        # prefer the 3m point; only let 1m fill a month that has no 3m
        if tenor == 3.0:
            out[d] = rate
            have_3m.add(d)
        elif d not in have_3m:
            out[d] = rate
    return out
