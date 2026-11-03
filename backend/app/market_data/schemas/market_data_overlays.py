"""Per-bank market data overlay contracts (spec §9).

Overlays are the tenant's private spread layer on the AequorOS golden copy:
effective-dated, component-tagged adjustment records that compose onto the
published base at read time. Additive basis-point spreads are the primary
form; fixed (rate decimal fraction) and multiplicative (factor) are
secondary. Rows are append-only versioned — an edit supersedes, an end
stamps ``effective_to`` — so past adjusted curves stay reproducible.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OverlayBaseRefKind = Literal["curve", "fx", "index"]
OverlayAdjustmentType = Literal["additive_bps", "fixed", "multiplicative"]
OverlayComponentTag = Literal[
    "liquidity_premium",
    "term_liquidity_premium",
    "funding_spread",
    "credit_spread",
    "other",
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketDataOverlayCreate(ClosedModel):
    """One new adjustment record; ``supersedes`` performs an append-only edit."""

    base_ref_kind: OverlayBaseRefKind
    base_curve_name: str | None = Field(default=None, max_length=80)
    tenor_months: int | None = Field(default=None, gt=0, title="Overlay Create Tenor Months")
    adjustment_type: OverlayAdjustmentType
    # additive_bps: basis points; fixed: rate decimal fraction; multiplicative: factor.
    value: Decimal
    component_tag: OverlayComponentTag
    effective_from: date = Field(title="Overlay Create Effective From")
    effective_to: date | None = Field(default=None, title="Overlay Create Effective To")
    note: str | None = Field(default=None, max_length=2000)
    # Append-only edit: the prior version this record replaces.
    supersedes: UUID | None = None


class MarketDataOverlayEnd(ClosedModel):
    """End an active overlay: adjustment stops applying after ``effective_to``."""

    effective_to: date = Field(title="Overlay End Effective To")


class MarketDataOverlayRead(ClosedModel):
    id: UUID
    bank_id: str
    base_ref_kind: str
    base_curve_name: str | None = Field(title="Overlay Base Curve Name")
    tenor_months: int | None = Field(title="Overlay Tenor Months")
    adjustment_type: str
    value: Decimal
    component_tag: str
    effective_from: date = Field(title="Overlay Effective From")
    effective_to: date | None = Field(title="Overlay Effective To")
    note: str | None = Field(title="Overlay Note")
    created_by: UUID | None = Field(title="Overlay Created By")
    created_by_email: str | None = Field(title="Overlay Created By Email")
    superseded_by: UUID | None = Field(title="Overlay Superseded By")
    # Composes at the requested as-of: current generation + effective window.
    active: bool
    created_at: datetime = Field(title="Overlay Created At")


class MarketDataOverlayListRead(ClosedModel):
    bank_id: str
    as_of_date: date = Field(title="Overlay List As Of Date")
    overlays: list[MarketDataOverlayRead]
    total: int
