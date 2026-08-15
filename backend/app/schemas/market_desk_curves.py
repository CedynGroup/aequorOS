"""Forward-curve construction API contracts (operator control plane ONLY).

Serves the STAFF curve-construction console under ``app.operator`` (FC-3); nothing
here is mounted on — or importable into — the tenant API surface. Actor identities
are operator emails (workforce IdP), never tenant users. Mirrors the ClosedModel
(``extra="forbid"``) discipline of ``schemas/market_desk.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.market_desk import DeskResearchAdjustment


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- curve definitions (Track 2) -----------------------------------------------------


class _CurveDefinitionFields(ClosedModel):
    """The shared governed parameter surface (create + propose)."""

    currency: str = Field(min_length=3, max_length=3)
    calendar_name: str = Field(min_length=1, max_length=40)
    curve_kind: Literal["forward", "zero", "discount"] = "forward"
    projection_index: str | None = Field(default=None, max_length=40)
    discount_curve_code: str | None = Field(default=None, max_length=60)
    instrument_set_ref: str = Field(min_length=1, max_length=80)
    interpolation_method: str = Field(min_length=1, max_length=40)
    output_daycount: str = Field(min_length=1, max_length=20)
    payment_frequency: str | None = Field(default=None, max_length=20)
    payment_interval_months: int = Field(gt=0)
    curve_frequency: str = Field(min_length=1, max_length=20)
    spot_lag_days: int = Field(default=2, ge=0)
    roll_convention: str = Field(default="modified_following", max_length=30)
    extrapolation_rule: str = Field(default="flat_forward", max_length=30)
    # FC-6d: distribution tier a published curve requires (core < standard <
    # premium). Default standard — the platform-wide grandfather tier.
    entitlement_tier: Literal["core", "standard", "premium"] = "standard"
    params: dict[str, Any] = Field(default_factory=dict, title="Curve Definition Params")
    change_rationale: str = Field(min_length=1)


class DeskCurveDefinitionCreate(_CurveDefinitionFields):
    """Register a NEW curve code at version 1 (draft)."""

    curve_code: str = Field(min_length=1, max_length=60)


class DeskCurveDefinitionVersionPropose(_CurveDefinitionFields):
    """Track 2: draft version+1 of an existing curve code (code is a path param)."""


class DeskCurveDefinitionApprove(ClosedModel):
    """Track-2 approval; the approver is the authenticated operator."""

    effective_from: date


class DeskCurveDefinitionRead(ClosedModel):
    id: UUID
    curve_code: str
    version: int
    status: str
    currency: str
    calendar_name: str
    curve_kind: str
    projection_index: str | None = Field(title="Curve Projection Index")
    discount_curve_code: str | None = Field(title="Curve Discount Curve Code")
    instrument_set_ref: str
    interpolation_method: str
    output_daycount: str
    payment_frequency: str | None = Field(title="Curve Payment Frequency")
    payment_interval_months: int
    curve_frequency: str
    spot_lag_days: int
    roll_convention: str
    extrapolation_rule: str
    entitlement_tier: str
    params: dict[str, Any] = Field(title="Curve Definition Params")
    change_rationale: str
    proposed_by: str
    approved_by: str | None = Field(title="Curve Approved By")
    approved_at: datetime | None = Field(title="Curve Approved At")
    effective_from: date | None = Field(title="Curve Effective From")
    created_at: datetime


class DeskCurveDefinitionListRead(ClosedModel):
    definitions: list[DeskCurveDefinitionRead]
    total: int


# -- construction (preview) / publish ------------------------------------------------


class DeskCurveQuote(ClosedModel):
    """One instrument-grid quote (rate as a DECIMAL fraction, e.g. 0.245 = 24.5%)."""

    instrument: str = Field(min_length=1, max_length=20)
    tenor: str = Field(min_length=1, max_length=20)
    quote: float
    leg: Literal["discount", "projection"] = "discount"
    # FC-G3: an explicit forward/IMM start date makes the instrument FORWARD-START
    # — accruing over [forward_start, forward_start + tenor] instead of from the
    # spot date. Required for a ``futures`` instrument (its IMM period start).
    # ``None`` keeps the classic spot-start behaviour.
    forward_start: date | None = Field(default=None, title="Quote Forward Start")
    # FC-6a: absolute (normal) short-rate vol for the futures->forward convexity
    # adjustment; read only for a ``futures`` instrument. ``None`` = governed default.
    sigma: float | None = Field(default=None, ge=0.0, title="Quote Futures Sigma")


class DeskCurveConstructRequest(ClosedModel):
    """Run construction against the active definition for a curve code + cob."""

    curve_code: str = Field(min_length=1, max_length=60)
    as_of: date
    quotes: list[DeskCurveQuote] = Field(min_length=1)


class DeskCurvePublishRequest(DeskCurveConstructRequest):
    """Construct + publish through the desk determination fan-out."""

    methodology_code: str | None = Field(default=None, max_length=40)


class DeskCurveStageRequest(DeskCurvePublishRequest):
    """Construct + stage a DRAFT determination for per-cob maker-checker (FC-G2).

    Writes a draft only — never approved, never published. Bounded, audited
    ``research_adjustments`` (Option B Track-1) may ride along; they are
    recorded on the draft exactly like the rates flow (``set_research_adjustments``).
    """

    research_adjustments: list[DeskResearchAdjustment] = Field(default_factory=list)


class DeskCurveGridRow(ClosedModel):
    start: date
    end: date
    discount_factor: float
    forward_yield: float


class DeskCurvePillarView(ClosedModel):
    instrument: str
    tenor: str
    leg: str
    pillar_date: date
    quote: float
    discount_factor: float
    reprice_residual: float


class DeskCurveQaView(ClosedModel):
    passed: bool
    reprice_max_residual: float
    reprice_tolerance: float
    reprice_pass: bool
    pillar_count: int
    pillar_min_count: int
    pillar_coverage_pass: bool
    monotone_df_pass: bool
    forward_positivity_pass: bool
    forward_oscillation_pass: bool
    forward_min: float
    forward_total_variation_ratio: float


class DeskCurveConstructResponse(ClosedModel):
    """The desk 'Run construction' preview: grid + pillar nodes + QA (writes nothing)."""

    curve_code: str
    definition_version: int
    as_of: date
    output_basis: str
    curve_frequency_months: int
    input_digest: str
    rows: list[DeskCurveGridRow]
    pillars: list[DeskCurvePillarView]
    qa: DeskCurveQaView
