"""Market research desk API contracts (operator control plane ONLY).

These schemas serve the STAFF desk console under ``app.operator``; nothing
here is mounted on — or importable into — the tenant API surface. Actor
identities are operator emails (workforce IdP), never tenant users.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type DeskObservationUnit = Literal["pct", "rate", "ghs", "index"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- methodology register ---------------------------------------------------------


class DeskMethodologyCreate(ClosedModel):
    """Register a NEW methodology code at version 1 (draft)."""

    methodology_code: str = Field(min_length=1, max_length=40)
    parameters: dict[str, Any] = Field(title="Methodology Parameters")
    change_rationale: str = Field(min_length=1)


class DeskMethodologyVersionPropose(ClosedModel):
    """Track 2: draft version+1 of an existing code with a rationale."""

    parameters: dict[str, Any] = Field(title="Methodology Version Parameters")
    change_rationale: str = Field(min_length=1)


class DeskMethodologyApprove(ClosedModel):
    """Track-2 approval; the approver is the authenticated operator."""

    effective_from: date


class DeskMethodologyRead(ClosedModel):
    id: UUID
    methodology_code: str
    version: int
    status: str
    parameters: dict[str, Any] = Field(title="Methodology Parameters")
    change_rationale: str
    proposed_by: str
    approved_by: str | None = Field(title="Methodology Approved By")
    approved_at: datetime | None = Field(title="Methodology Approved At")
    effective_from: date | None = Field(title="Methodology Effective From")
    created_at: datetime


class DeskMethodologyListRead(ClosedModel):
    methodologies: list[DeskMethodologyRead]
    total: int


# -- observations / captures -------------------------------------------------------


class DeskObservationCreate(ClosedModel):
    """Manual-entry fallback (spec §3): a first-class observation with
    operator provenance; corrections supersede append-only."""

    series_code: str = Field(min_length=1, max_length=80)
    as_of_date: date
    value: Decimal
    unit: DeskObservationUnit
    attributes: dict[str, Any] = Field(default_factory=dict, title="Observation Attributes")
    quality_flags: list[str] = Field(default_factory=list, title="Observation Quality Flags")


class DeskObservationRead(ClosedModel):
    id: UUID
    capture_id: UUID | None = Field(title="Observation Capture Id")
    series_code: str
    as_of_date: date
    value: Decimal
    unit: str
    attributes: dict[str, Any] = Field(title="Observation Attributes")
    quality_flags: list[Any] = Field(title="Observation Quality Flags")
    entered_by: str | None = Field(title="Observation Entered By")
    superseded_by: UUID | None = Field(title="Observation Superseded By")
    created_at: datetime


class DeskObservationListRead(ClosedModel):
    observations: list[DeskObservationRead]
    #: Full count of rows matching the filters, BEFORE limit/offset.
    total: int
    #: Page window echoed back so the console can paginate without re-deriving.
    limit: int
    offset: int


class DeskCaptureRead(ClosedModel):
    id: UUID
    source_key: str
    captured_at: datetime
    as_of_date: date
    source_url: str | None = Field(title="Capture Source Url")
    content_sha256: str
    storage_path: str | None = Field(title="Capture Storage Path")
    parser_version: str
    status: str
    parse_error: str | None = Field(title="Capture Parse Error")
    created_by: str


class DeskCaptureListRead(ClosedModel):
    captures: list[DeskCaptureRead]
    total: int


# -- determinations -----------------------------------------------------------------


class DeskDeterminationCreate(ClosedModel):
    cob_date: date
    methodology_code: str | None = Field(
        default=None, max_length=40, title="Determination Methodology Code"
    )


class DeskDeterminationReject(ClosedModel):
    reason: str = Field(min_length=1)


class DeskResearchAdjustment(ClosedModel):
    """One determination-scoped research judgment (Option B Track-1).

    Kinds: override (absolute percent level), additive_bps (spread on the
    methodology-derived level), assumption_note (disclosure only).
    """

    series_code: str = Field(min_length=1, max_length=80)
    kind: Literal["override", "additive_bps", "assumption_note"]
    value: str | None = Field(default=None, title="Adjustment Value")
    rationale: str = Field(default="", title="Adjustment Rationale")


class DeskResearchAdjustmentsPut(ClosedModel):
    """Replace the full research_adjustments set on a draft determination."""

    adjustments: list[DeskResearchAdjustment] = Field(default_factory=list)


class DeskDeterminationRead(ClosedModel):
    id: UUID
    cob_date: date
    methodology_code: str
    methodology_version: int
    input_snapshot: list[Any] = Field(title="Determination Input Snapshot")
    input_digest: str
    derived_values: dict[str, Any] = Field(title="Determination Derived Values")
    qa_results: dict[str, Any] = Field(title="Determination Qa Results")
    research_adjustments: list[Any] = Field(
        default_factory=list, title="Determination Research Adjustments"
    )
    status: str
    prepared_by: str
    reviewed_by: str | None = Field(title="Determination Reviewed By")
    review_note: str | None = Field(title="Determination Review Note")
    published_at: datetime | None = Field(title="Determination Published At")
    supersedes_id: UUID | None = Field(title="Determination Supersedes Id")
    created_at: datetime


class DeskDeterminationListRead(ClosedModel):
    determinations: list[DeskDeterminationRead]
    total: int


class DeskPackageCompletenessItem(ClosedModel):
    series_code: str
    required: bool
    status: str  # present | stale | missing
    as_of_date: str | None = None
    value: str | None = None
    unit: str | None = None
    age_days: int | None = None
    stale_limit_days: int
    provenance: dict[str, Any] = Field(default_factory=dict)


class DeskPackageCompleteness(ClosedModel):
    cob_date: str
    items: list[DeskPackageCompletenessItem]
    required_total: int
    required_present: int
    required_missing: list[str]
    required_stale: list[str]
    ready: bool
    failed_captures: list[dict[str, Any]] = Field(default_factory=list)


class DeskRateDelta(ClosedModel):
    series_code: str
    current: str | None = None
    prior: str | None = None
    delta_pp: str | None = None
    unit: str | None = None


class DeskWeekOverWeek(ClosedModel):
    prior_determination_id: str | None = None
    prior_cob_date: str | None = None
    prior_published_at: str | None = None
    deltas: list[DeskRateDelta] = Field(default_factory=list)


class DeskPackageView(ClosedModel):
    """Wizard payload: completeness, WoW deltas, input provenance."""

    completeness: DeskPackageCompleteness
    week_over_week: DeskWeekOverWeek
    input_provenance: list[dict[str, Any]] = Field(default_factory=list)
    rates_qa_passed: bool | None = None
    curves_qa_passed: bool | None = None
    package_digest: str | None = None


# -- capture content / snippets ------------------------------------------------------


class DeskCaptureContentView(ClosedModel):
    capture_id: str
    source_key: str
    source_url: str | None = None
    as_of_date: str
    status: str
    content_sha256: str
    parser_version: str
    kind: str
    content_omitted: str | None = None
    content_available: bool
    content_bytes: int
    text: str | None = None
    truncated: bool = False
    snippet: str | None = None
    needle: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class DeskObservationSnippet(ClosedModel):
    capture_id: str
    source_key: str
    source_url: str | None = None
    kind: str
    content_available: bool
    content_omitted: str | None = None
    snippet: str | None = None
    needle: str | None = None
    hint: str | None = None


# -- entitlements --------------------------------------------------------------------


class DeskEntitlementGrantTier(ClosedModel):
    organization_id: str = Field(min_length=1, max_length=20)
    tier: Literal["core", "standard", "premium"]
    effective_from: date
    notes: str | None = None


class DeskEntitlementGrantDataset(ClosedModel):
    organization_id: str = Field(min_length=1, max_length=20)
    dataset_code: str = Field(min_length=1, max_length=40)
    effective_from: date
    notes: str | None = None


class DeskEntitlementRead(ClosedModel):
    id: UUID
    organization_id: str
    dataset_code: str
    tier: str | None = None
    status: str
    effective_from: date
    effective_to: date | None = None
    granted_by: str
    revoked_by: str | None = None
    revoked_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


class DeskEntitlementListRead(ClosedModel):
    entitlements: list[DeskEntitlementRead]
    total: int
    catalog: dict[str, Any] = Field(default_factory=dict)


# -- publications --------------------------------------------------------------------


class DeskPublicationRead(ClosedModel):
    id: UUID
    determination_id: UUID
    published_by: str
    published_at: datetime
    status: str
    results: list[Any] = Field(title="Publication Results")


class DeskPublicationListRead(ClosedModel):
    publications: list[DeskPublicationRead]
    total: int
