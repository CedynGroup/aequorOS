"""Read models for the market data consumption views (Markets hub).

A thin, vendor-blind projection of the canonical market data store: every
view carries the §15 arbitration result plus a §11.4 freshness attribution,
so the UI can show provenance (source system, batch, age, staleness) next to
every number without naming vendor concepts anywhere else.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketDataAttributionRead(ClosedModel):
    """Provenance + freshness for one served view (mirrors SourceAttribution)."""

    source_system: str
    ingestion_batch_id: UUID
    ingested_at: datetime = Field(title="Market Data Ingested At")
    stale: bool
    age_seconds: float


class YieldCurvePointRead(ClosedModel):
    tenor_months: int
    rate: Decimal


class CurveOverlayComponentRead(ClosedModel):
    """One active per-bank overlay composing into a curve's adjusted series.

    ``value`` units follow ``adjustment_type``: basis points for
    ``additive_bps``, a rate decimal fraction for ``fixed``, a factor for
    ``multiplicative``. ``tenor_months`` NULL means the spread is flat
    across all tenors.
    """

    overlay_id: UUID
    component_tag: str
    adjustment_type: str
    value: Decimal
    tenor_months: int | None = Field(title="Overlay Component Tenor Months")
    effective_from: date = Field(title="Overlay Component Effective From")


class YieldCurveViewRead(ClosedModel):
    """One published curve (keyed by ``curve_name``); rates decimal fractions.

    ``curve_type`` badges the series (zero / forward / discount for desk-
    constructed curves; sovereign etc. for observed vendor curves).
    ``adjusted_points`` + ``overlay_components`` are non-empty only when the
    bank has active private overlays on this curve name: base + overlays
    composed at read time, golden data untouched (spec §2, §9). Both empty
    means "no adjusted series" — the base is never duplicated.
    """

    currency: str
    curve_name: str
    curve_type: str
    as_of_date: date = Field(title="Yield Curve As Of Date")
    points: list[YieldCurvePointRead]
    adjusted_points: list[YieldCurvePointRead] = Field(title="Yield Curve Adjusted Points")
    overlay_components: list[CurveOverlayComponentRead]
    attribution: MarketDataAttributionRead


class FxRateHistoryPointRead(ClosedModel):
    as_of_date: date = Field(title="Fx History As Of Date")
    rate: Decimal


class FxRateViewRead(ClosedModel):
    """The authoritative spot for one pair: ``rate`` quote units per 1 base.

    ``history`` is the trailing persisted spot series (ascending by business
    date, capped) for sparkline rendering.
    """

    base: str
    quote: str
    rate_type: str
    tenor_months: int | None = Field(title="Fx Rate Tenor Months")
    rate: Decimal
    as_of_date: date = Field(title="Fx Rate As Of Date")
    history: list[FxRateHistoryPointRead]
    attribution: MarketDataAttributionRead


class RatingViewRead(ClosedModel):
    issuer: str
    agency: str
    rating: str
    watch_status: str | None = Field(title="Rating Watch Status")
    rating_date: date
    as_of_date: date = Field(title="Rating As Of Date")
    attribution: MarketDataAttributionRead


class IndexViewRead(ClosedModel):
    index_code: str
    value: Decimal
    scenario: str
    horizon_months: int | None = Field(title="Index Horizon Months")
    as_of_date: date = Field(title="Index As Of Date")
    attribution: MarketDataAttributionRead


class MarketDataViewsRead(ClosedModel):
    """Everything the canonical store can answer for a bank at an as-of date."""

    bank_id: str
    as_of_date: date = Field(title="Market Data Views As Of Date")
    curves: list[YieldCurveViewRead]
    fx_rates: list[FxRateViewRead]
    fx_forwards: list[FxRateViewRead]
    ratings: list[RatingViewRead]
    indices: list[IndexViewRead]
