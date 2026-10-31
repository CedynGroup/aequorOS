"""Contracts for the per-bank market-data source preference (market_data_sources.md §4).

The frontend Sources control room and the side-by-side planes comparison read
these shapes; keep them aligned with the frozen spec §4. All rate/yield values
are decimal fractions (0.15 = 15%); index reference-rate values stay
percent-valued (15.0) per their canonical convention.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["curves", "fx", "rates"]
Source = Literal["aequor", "bank", "vendor"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# GET/PUT /source-preferences
# ---------------------------------------------------------------------------


class CategoryPreferenceRead(ClosedModel):
    """One category's resolved selection: base plane + overlay toggle."""

    source: Source
    overlay: bool


class SourcePreferencesRead(ClosedModel):
    """The bank's resolved preference (defaults synthesised when no row)."""

    bank_id: str
    curves: CategoryPreferenceRead
    fx: CategoryPreferenceRead
    rates: CategoryPreferenceRead
    updated_at: datetime | None = Field(default=None, title="Source Preferences Updated At")
    updated_by: UUID | None = Field(default=None, title="Source Preferences Updated By")


class CategoryPreferenceUpdate(ClosedModel):
    """Partial patch of one category — omitted fields keep their current value."""

    source: Source | None = None
    overlay: bool | None = None


class SourcePreferencesUpdate(ClosedModel):
    """Partial preference patch (any subset of categories); ``reason`` is audit only."""

    curves: CategoryPreferenceUpdate | None = None
    fx: CategoryPreferenceUpdate | None = None
    rates: CategoryPreferenceUpdate | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# GET /planes
# ---------------------------------------------------------------------------


class PlaneAttributionRead(ClosedModel):
    """Provenance + freshness for a served plane (mirrors SourceAttribution)."""

    source_system: str
    ingestion_batch_id: UUID
    ingested_at: datetime = Field(title="Plane Ingested At")
    stale: bool
    age_seconds: float


class PlaneCurvePointRead(ClosedModel):
    tenor_months: int
    rate: Decimal


class PlaneCurveItemRead(ClosedModel):
    """One curve resolved under a plane (rates decimal fractions)."""

    kind: Literal["curve"] = "curve"
    currency: str
    curve_name: str
    curve_type: str
    as_of_date: date = Field(title="Plane Curve As Of Date")
    points: list[PlaneCurvePointRead]
    adjusted_points: list[PlaneCurvePointRead]
    attribution: PlaneAttributionRead


class PlaneFxItemRead(ClosedModel):
    """One FX spot resolved under a plane: ``rate`` quote units per 1 base."""

    kind: Literal["fx"] = "fx"
    base: str
    quote: str
    rate: Decimal
    as_of_date: date = Field(title="Plane Fx As Of Date")
    attribution: PlaneAttributionRead


class PlaneIndexItemRead(ClosedModel):
    """One reference index resolved under a plane (percent-valued)."""

    kind: Literal["index"] = "index"
    index_code: str
    scenario: str
    value: Decimal
    horizon_months: int | None = Field(default=None, title="Plane Index Horizon Months")
    as_of_date: date = Field(title="Plane Index As Of Date")
    attribution: PlaneAttributionRead


PlaneItemRead = PlaneCurveItemRead | PlaneFxItemRead | PlaneIndexItemRead


class PlaneRead(ClosedModel):
    """One base plane's side-by-side resolution of the requested scope."""

    source: Source
    available: bool
    is_selected: bool
    items: list[PlaneItemRead]
    attribution: PlaneAttributionRead | None = Field(default=None, title="Plane Attribution")


class PlaneOverlayDeltaRead(ClosedModel):
    """One overlay-adjusted point vs its base, on the selected plane."""

    curve_name: str
    tenor_months: int
    base: Decimal
    adjusted: Decimal
    delta: Decimal


class PlaneOverlayRead(ClosedModel):
    """The overlay layer preview for the category (curves only; empty otherwise)."""

    available: bool
    delta_preview: list[PlaneOverlayDeltaRead]


class PlanesRead(ClosedModel):
    """The three-plane comparison for one category at an as-of date (spec §4)."""

    category: Category
    as_of: date = Field(title="Planes As Of")
    selected_source: Source
    overlay_enabled: bool
    planes: list[PlaneRead]
    overlay: PlaneOverlayRead


# ---------------------------------------------------------------------------
# GET /curves/{curve_name}/forward-grid  (FC-5 / G1)
# ---------------------------------------------------------------------------


class ForwardGridRowRead(ClosedModel):
    """One Start/End/DF/Yield row of the published forward grid.

    ``discount_factor`` and ``forward_yield`` are decimal strings (yields are
    decimal fractions, e.g. ``"0.235"`` = 23.5%).
    """

    start: date
    end: date
    discount_factor: str
    forward_yield: str


class ForwardGridPillarRead(ClosedModel):
    """One calibrating pillar instrument from the determination's input snapshot."""

    tenor: str
    instrument: str
    quote: str


class ForwardGridAssumptionsRead(ClosedModel):
    """Immutable governed definition applied to this published grid."""

    version: int
    calendar_name: str
    instrument_set_ref: str
    projection_index: str | None
    discount_curve_code: str | None
    interpolation_method: str
    output_daycount: str
    payment_frequency: str | None
    payment_interval_months: int
    curve_frequency: str
    spot_lag_days: int
    roll_convention: str
    extrapolation_rule: str


class ForwardGridRead(ClosedModel):
    """The published forward grid for a desk curve (spec §4)."""

    curve_name: str
    currency: str
    as_of: date = Field(title="Forward Grid As Of")
    methodology_ref: str
    interpolation: str
    grid_is_authoritative: bool
    frequency: str
    available_frequencies: list[str]
    assumptions: ForwardGridAssumptionsRead | None
    rows: list[ForwardGridRowRead]
    pillars: list[ForwardGridPillarRead]
