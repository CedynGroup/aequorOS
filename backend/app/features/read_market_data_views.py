"""Read-only market data consumption views for the Markets hub.

Thin HTTP layer over :mod:`app.services.market_data`: discovers which scopes
the canonical store can answer for the bank at the requested as-of date and
serves each one through the vendor-blind getters, so every value carries §15
arbitration and a §11.4 freshness attribution. No vendor concept appears
outside the attribution's ``source_system``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, Tenant, TenantContext
from app.db.base import utc_now
from app.models import Bank
from app.schemas.market_data_views import (
    FxRateHistoryPointRead,
    FxRateViewRead,
    IndexViewRead,
    MarketDataAttributionRead,
    MarketDataViewsRead,
    RatingViewRead,
    YieldCurvePointRead,
    YieldCurveViewRead,
)
from app.services import market_data

router = APIRouter(tags=["market-data"])

# Trailing spot observations served per pair — enough for a sparkline without
# shipping the full VaR window on every read.
FX_HISTORY_POINTS = 30


@router.get(
    "/banks/{bank_id}/market-data/views",
    response_model=MarketDataViewsRead,
    operation_id="getMarketDataViews",
)
def get_market_data_views(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> MarketDataViewsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    now = utc_now()
    effective_as_of = as_of if as_of is not None else now.date()
    org = ctx.organization_id

    curves: list[YieldCurveViewRead] = []
    for currency in market_data.list_curve_currencies(db, org, bank.id, effective_as_of):
        curve = market_data.get_yield_curve(db, org, bank.id, currency, effective_as_of, now=now)
        if curve is None:
            continue
        curves.append(
            YieldCurveViewRead(
                currency=curve.currency,
                curve_name=curve.curve_name,
                as_of_date=curve.as_of_date,
                points=[
                    YieldCurvePointRead(tenor_months=tenor, rate=rate)
                    for tenor, rate in curve.points
                ],
                attribution=_attribution_read(curve.attribution),
            )
        )

    fx_rates: list[FxRateViewRead] = []
    for base, quote in market_data.list_fx_pairs(db, org, bank.id, effective_as_of):
        spot = market_data.get_fx_spot(db, org, bank.id, base, quote, effective_as_of, now=now)
        if spot is None:
            continue
        history = market_data.get_fx_spot_history(
            db, org, bank.id, base, quote, effective_as_of, limit=FX_HISTORY_POINTS
        )
        fx_rates.append(
            FxRateViewRead(
                base=spot.base_currency,
                quote=spot.quote_currency,
                rate_type="spot",
                tenor_months=None,
                rate=spot.rate,
                as_of_date=spot.as_of_date,
                history=[
                    FxRateHistoryPointRead(as_of_date=day, rate=rate) for day, rate in history
                ],
                attribution=_attribution_read(spot.attribution),
            )
        )

    ratings: list[RatingViewRead] = []
    for issuer in market_data.list_rating_issuers(db, org, bank.id, effective_as_of):
        rating = market_data.get_rating(db, org, bank.id, issuer, effective_as_of, now=now)
        if rating is None:
            continue
        ratings.append(
            RatingViewRead(
                issuer=rating.issuer,
                agency=rating.agency,
                rating=rating.rating,
                watch_status=rating.watch_status,
                rating_date=rating.rating_date,
                as_of_date=rating.as_of_date,
                attribution=_attribution_read(rating.attribution),
            )
        )

    indices: list[IndexViewRead] = []
    for index_code, scenario in market_data.list_index_scopes(db, org, bank.id, effective_as_of):
        index = market_data.get_index(
            db, org, bank.id, index_code, effective_as_of, scenario=scenario, now=now
        )
        if index is None:
            continue
        indices.append(
            IndexViewRead(
                index_code=index.index_code,
                value=index.value,
                scenario=index.scenario,
                horizon_months=index.horizon_months,
                as_of_date=index.as_of_date,
                attribution=_attribution_read(index.attribution),
            )
        )

    return MarketDataViewsRead(
        bank_id=bank.id,
        as_of_date=effective_as_of,
        curves=curves,
        fx_rates=fx_rates,
        ratings=ratings,
        indices=indices,
    )


def _attribution_read(attribution: market_data.SourceAttribution) -> MarketDataAttributionRead:
    return MarketDataAttributionRead(
        source_system=attribution.source_system,
        ingestion_batch_id=attribution.ingestion_batch_id,
        ingested_at=attribution.ingested_at,
        stale=attribution.stale,
        age_seconds=attribution.age_seconds,
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
