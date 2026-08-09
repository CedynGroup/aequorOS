from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.schemas.cashflow_window import CashflowWindowRead
from app.services import cashflow_window

router = APIRouter(tags=["cashflow-window"])


@router.get(
    "/banks/{bank_id}/analytics/cashflow-window",
    response_model=CashflowWindowRead,
    operation_id="computeCashflowWindow",
)
def compute_cashflow_window(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
) -> CashflowWindowRead:
    """Contractual cash flows maturing inside [start_date, end_date]: the
    current canonical book's per-currency, per-calendar-month inflows,
    outflows and nets, with per-currency and base-equivalent totals plus the
    count of positions carrying no contractual maturity (excluded)."""
    return cashflow_window.compute_cashflow_window(
        db, ctx, bank_id, start_date=start_date, end_date=end_date
    )
