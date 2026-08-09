"""Per-bank market data overlay API (spec §9, §11b).

The tenant's private spread layer on the AequorOS golden copy. Viewers read;
analysts and above mutate (the standard ``MutationTenant`` gate, matching the
sibling market-data endpoints). Overlays are append-only versioned: POST
creates (optionally superseding a prior version), ``/end`` closes the
effective window. Composition onto curves happens in the read views —
these endpoints never touch canonical market data.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.db.base import utc_now
from app.schemas.market_data_overlays import (
    MarketDataOverlayCreate,
    MarketDataOverlayEnd,
    MarketDataOverlayListRead,
    MarketDataOverlayRead,
)
from app.services import market_data_overlays

router = APIRouter(tags=["market-data"])


@router.get(
    "/banks/{bank_id}/market-data/overlays",
    response_model=MarketDataOverlayListRead,
    operation_id="listMarketDataOverlays",
)
def list_market_data_overlays(  # noqa: PLR0913 - filters are the read contract
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
    include_history: Annotated[bool, Query()] = False,
    base_curve_name: Annotated[str | None, Query(max_length=80)] = None,
) -> MarketDataOverlayListRead:
    return market_data_overlays.list_overlays(
        db,
        ctx,
        bank_id,
        as_of=as_of if as_of is not None else utc_now().date(),
        include_history=include_history,
        base_curve_name=base_curve_name,
    )


@router.post(
    "/banks/{bank_id}/market-data/overlays",
    response_model=MarketDataOverlayRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMarketDataOverlay",
)
def create_market_data_overlay(
    bank_id: str,
    payload: MarketDataOverlayCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataOverlayRead:
    return market_data_overlays.create_overlay(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/market-data/overlays/{overlay_id}/end",
    response_model=MarketDataOverlayRead,
    operation_id="endMarketDataOverlay",
)
def end_market_data_overlay(
    bank_id: str,
    overlay_id: UUID,
    payload: MarketDataOverlayEnd,
    db: DbSession,
    ctx: MutationTenant,
) -> MarketDataOverlayRead:
    return market_data_overlays.end_overlay(db, ctx, bank_id, overlay_id, payload)
