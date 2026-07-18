"""Per-tenant daily cash-flow series from a bank's own canonical history.

Reads the bank's latest accepted ``historical_cashflows`` reference dataset — the
daily inflow / outflow / net series it ingested through the Data Engine — RLS-scoped
so one bank's series can never mix with another's. This is what a *bank-specific*
cash-flow model trains and forecasts on. A bank with no (or too little) history is
served the generic model instead, clearly labelled (see ``cashflow_forecast``): the
generic model is trained on synthetic bootstrap data, never on another bank's data.

Payload shape mirrors ``DailyFlow``: a ``date`` plus ``inflow_ghs`` / ``outflow_ghs``
/ ``net_ghs`` (millions), with tolerant field-name fallbacks so an onboarding mapping
does not have to match one exact spelling.
"""

from __future__ import annotations

import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.ml.synthetic import DailyFlow
from app.models.canonical import CanonicalReferenceRow

DATASET_KIND = "historical_cashflows"


def _num(payload: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return default


def _flow_from_payload(payload: dict[str, Any]) -> DailyFlow | None:
    raw_date = payload.get("date") or payload.get("as_of_date") or payload.get("flow_date")
    if not raw_date:
        return None
    try:
        date = datetime.date.fromisoformat(str(raw_date)[:10])
    except ValueError:
        return None
    inflow = _num(payload, "inflow_ghs", "inflow")
    outflow = _num(payload, "outflow_ghs", "outflow")
    net = _num(payload, "net_ghs", "net", default=inflow - outflow)
    return DailyFlow(date=date, inflow=inflow, outflow=outflow, net=net)


def load_bank_daily_series(db: Session, ctx: TenantContext, bank_id: UUID) -> list[DailyFlow]:
    """The bank's own daily cash-flow series (ascending by date), or ``[]`` if none.

    Uses the latest accepted ``historical_cashflows`` batch — newest ``created_at``,
    UUIDv7 text tie-break — matching how the calculation modules pick a reference
    generation in ``fact_derivation``.
    """
    batch_rows = db.execute(
        select(
            CanonicalReferenceRow.ingestion_batch_id,
            func.max(CanonicalReferenceRow.created_at),
        )
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank_id,
            CanonicalReferenceRow.dataset_kind == DATASET_KIND,
        )
        .group_by(CanonicalReferenceRow.ingestion_batch_id)
    ).all()
    if not batch_rows:
        return []
    winner = max(batch_rows, key=lambda row: (row[1], str(row[0])))[0]

    payloads = db.scalars(
        select(CanonicalReferenceRow.payload)
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank_id,
            CanonicalReferenceRow.dataset_kind == DATASET_KIND,
            CanonicalReferenceRow.ingestion_batch_id == winner,
        )
        .order_by(CanonicalReferenceRow.row_index)
    ).all()

    flows = [flow for payload in payloads if (flow := _flow_from_payload(payload)) is not None]
    flows.sort(key=lambda flow: flow.date)
    return flows
