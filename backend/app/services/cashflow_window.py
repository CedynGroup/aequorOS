"""Contractual cash-flow windows: the real book's maturities between two dates.

``compute_cashflow_window`` aggregates the current canonical position book's
contractual maturities falling inside [start_date, end_date] into per-currency,
per-calendar-month inflow/outflow/net figures. Read-only over ingested state:
no behavioral overlays, no interest accrual, no FX invention — a position's
balance lands in its maturity month, in its own currency, and the ingested
base-currency leg (where present) feeds the overall totals.

Position scoping (stated per TIME-3): the CURRENT book mirrors the blotter's
freshest-book semantics from ``ingestion.list_positions`` — current-generation
positions (``CanonicalPosition.superseded_by IS NULL``) each resolved to their
LATEST current snapshot (``CanonicalPositionSnapshot.superseded_by IS NULL``,
max ``as_of_date`` per position) — rather than the period-pinned slice
``regulatory_liquidity._currency_ladders`` uses, because this view is about the
live book between two arbitrary dates, not a filing period. On top of that the
regulatory slice's honesty filters apply: only ``accepted``/``warning``
snapshots count (mirroring ``regulatory_liquidity._CCY_STATUSES``), and only
on-balance asset/liability position types participate (mirroring
``_CCY_ASSET_TYPES``/``_CCY_LIABILITY_TYPES`` — a maturing derivative notional
is not a principal cash flow, so derivative/off-balance types are excluded).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.schemas.cashflow_window import (
    CashflowWindowCurrencyTotalRead,
    CashflowWindowMonthRead,
    CashflowWindowOverallRead,
    CashflowWindowRead,
)
from app.services.jurisdictions import base_currency
from app.services.stress_scenarios import _get_bank_or_404  # noqa: PLC2701 - shared guard

_MAX_WINDOW_MONTHS = 36
# Side classification mirrors regulatory_liquidity._CCY_ASSET_TYPES /
# _CCY_LIABILITY_TYPES: an asset maturing is a contractual inflow, a liability
# maturing is a contractual outflow.
_ASSET_TYPES = ("LOAN", "SECURITY_HOLDING", "CASH", "INTERBANK_PLACEMENT", "OTHER_ASSET")
_LIABILITY_TYPES = ("DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY")
_STATUSES = ("accepted", "warning")

_ZERO = Decimal(0)


def compute_cashflow_window(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    start_date: date,
    end_date: date,
) -> CashflowWindowRead:
    """Per-currency monthly contractual flows maturing inside [start, end]."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    _validate_window(start_date, end_date)
    rows = _current_book(db, ctx, bank)

    no_maturity_count = 0
    position_count = 0
    # (currency, first-of-month) -> [inflows, outflows]
    cells: dict[tuple[str, date], list[Decimal]] = {}
    # currency -> [inflows, outflows, position_count]
    totals: dict[str, list[Decimal | int]] = {}
    overall_in = _ZERO
    overall_out = _ZERO
    unconverted = 0
    base = base_currency(bank)

    for snapshot, position in rows:
        maturity = snapshot.contractual_maturity
        if maturity is None:
            no_maturity_count += 1
            continue
        if maturity < start_date or maturity > end_date:
            continue
        position_count += 1
        amount = Decimal(str(snapshot.balance or 0))
        side = 0 if position.position_type in _ASSET_TYPES else 1
        cell = cells.setdefault((position.currency, maturity.replace(day=1)), [_ZERO, _ZERO])
        cell[side] += amount
        total = totals.setdefault(position.currency, [_ZERO, _ZERO, 0])
        total[side] += amount  # type: ignore[operator]
        total[2] += 1  # type: ignore[operator]
        # Overall figures use only the ingested base-currency leg — the same
        # seam _currency_ladders reads; no conversion is ever invented here.
        raw = (snapshot.attributes or {}).get("balance_ghs")
        if raw not in (None, ""):
            base_amount = Decimal(str(raw))
        elif position.currency == base:
            base_amount = amount
        else:
            unconverted += 1
            continue
        if side == 0:
            overall_in += base_amount
        else:
            overall_out += base_amount

    month_spine = _months(start_date, end_date)
    months = [
        CashflowWindowMonthRead(
            month=month,
            currency=currency,
            inflows=cells.get((currency, month), (_ZERO, _ZERO))[0],
            outflows=cells.get((currency, month), (_ZERO, _ZERO))[1],
            net=(
                cells.get((currency, month), (_ZERO, _ZERO))[0]
                - cells.get((currency, month), (_ZERO, _ZERO))[1]
            ),
        )
        for currency in sorted(totals)
        for month in month_spine
    ]
    return CashflowWindowRead(
        bank_id=bank.id,
        start_date=start_date,
        end_date=end_date,
        months=months,
        totals=[
            CashflowWindowCurrencyTotalRead(
                currency=currency,
                inflows=inflows,  # type: ignore[arg-type]
                outflows=outflows,  # type: ignore[arg-type]
                net=inflows - outflows,  # type: ignore[operator, arg-type]
                position_count=count,  # type: ignore[arg-type]
            )
            for currency, (inflows, outflows, count) in sorted(totals.items())
        ],
        overall=CashflowWindowOverallRead(
            currency=base,
            inflows=overall_in,
            outflows=overall_out,
            net=overall_in - overall_out,
            unconverted_count=unconverted,
        ),
        position_count=position_count,
        no_maturity_count=no_maturity_count,
    )


def _validate_window(start_date: date, end_date: date) -> None:
    """Mirrors window_analytics._validate_window (same codes and detail dicts)."""
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "invalid_window",
                "message": "start_date must be on or before end_date.",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if months > _MAX_WINDOW_MONTHS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "window_too_large",
                "message": f"The analysis window is capped at {_MAX_WINDOW_MONTHS} months.",
                "window_months": months,
                "max_months": _MAX_WINDOW_MONTHS,
            },
        )


def _current_book(
    db: Session, ctx: TenantContext, bank: Bank
) -> list[tuple[CanonicalPositionSnapshot, CanonicalPosition]]:
    """The freshest in-scope book: latest current snapshot per current position.

    The status filter applies AFTER latest-snapshot resolution on purpose: if a
    position's freshest state is ``error``/``blocked`` it drops out entirely
    rather than silently falling back to an older accepted snapshot.
    """
    latest = (
        select(
            CanonicalPositionSnapshot.position_id.label("position_id"),
            func.max(CanonicalPositionSnapshot.as_of_date).label("as_of"),
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank.id,
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
            CanonicalPosition.position_type.in_((*_ASSET_TYPES, *_LIABILITY_TYPES)),
        )
        .group_by(CanonicalPositionSnapshot.position_id)
        .subquery()
    )
    records = db.execute(
        select(CanonicalPositionSnapshot, CanonicalPosition)
        .join(
            latest,
            (CanonicalPositionSnapshot.position_id == latest.c.position_id)
            & (CanonicalPositionSnapshot.as_of_date == latest.c.as_of),
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_STATUSES),
        )
    ).all()
    return [(snapshot, position) for snapshot, position in records]


def _months(start_date: date, end_date: date) -> list[date]:
    """First-of-month spine covering every calendar month the window touches."""
    months: list[date] = []
    cursor = start_date.replace(day=1)
    last = end_date.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
    return months
