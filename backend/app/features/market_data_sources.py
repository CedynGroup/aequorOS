"""Per-bank market-data source-preference API (market_data_sources.md §4).

Under ``/banks/{bank_id}/market-data``:

- ``GET/PUT /source-preferences`` — read / upsert the per-category base-plane
  + overlay selection (defaults synthesised when no row; PUT is analyst-gated
  and audited).
- ``GET /planes`` — the same scope resolved under each base plane, side by
  side, so the bank sees what each choice would feed the engines.
- ``GET /curves/{curve_name}/forward-grid`` — the published forward grid for a
  desk curve (FC-5/G1).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.market_data_sources import (
    ForwardGridRead,
    PlanesRead,
    SourcePreferencesRead,
    SourcePreferencesUpdate,
)
from app.services import market_data_sources

router = APIRouter(tags=["market-data-sources"])


@router.get(
    "/banks/{bank_id}/market-data/source-preferences",
    response_model=SourcePreferencesRead,
    operation_id="getMarketDataSourcePreferences",
)
def get_source_preferences(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
) -> SourcePreferencesRead:
    return market_data_sources.get_preference(db, ctx, bank_id)


@router.put(
    "/banks/{bank_id}/market-data/source-preferences",
    response_model=SourcePreferencesRead,
    operation_id="updateMarketDataSourcePreferences",
)
def update_source_preferences(
    bank_id: str,
    payload: SourcePreferencesUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> SourcePreferencesRead:
    return market_data_sources.set_preference(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/market-data/planes",
    response_model=PlanesRead,
    operation_id="getMarketDataPlanes",
)
def get_market_data_planes(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    category: Annotated[str, Query()],
    as_of: Annotated[date | None, Query()] = None,
) -> PlanesRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return market_data_sources.resolve_planes(db, ctx, bank_id, category, resolved_as_of)


@router.get(
    "/banks/{bank_id}/market-data/curves/{curve_name}/forward-grid",
    response_model=ForwardGridRead,
    operation_id="getMarketDataForwardGrid",
)
def get_forward_grid(
    bank_id: str,
    curve_name: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> ForwardGridRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return market_data_sources.get_forward_grid(db, ctx, bank_id, curve_name, resolved_as_of)
