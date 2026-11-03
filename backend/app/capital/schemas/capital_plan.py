"""ICAAP capital-plan + quarterly ILAAP contracts (Phase 2 item 10)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type CapitalPlanStatus = Literal["draft", "approved", "superseded"]


class Pillar2AddOn(ClosedModel):
    """One Pillar-2 register entry: a risk Pillar 1 does not capture."""

    risk_type: str = Field(min_length=1, max_length=120)
    add_on_pct_rwa: Decimal = Field(ge=0, le=100)
    rationale: str = Field(min_length=1, max_length=2000)


class ManagementAction(ClosedModel):
    action: str = Field(min_length=1, max_length=500)
    trigger: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=200)
    estimated_impact: str | None = Field(default=None, max_length=500)


class CapitalTrigger(ClosedModel):
    metric_code: str = Field(min_length=1, max_length=60)
    early_warning_level: Decimal
    action_level: Decimal
    escalation: str = Field(min_length=1, max_length=500)


class CapitalPlanContent(ClosedModel):
    pillar2_addons: list[Pillar2AddOn] = Field(default_factory=list)
    management_actions: list[ManagementAction] = Field(default_factory=list)
    trigger_framework: list[CapitalTrigger] = Field(default_factory=list)


class CapitalPlanProjectionYear(ClosedModel):
    """One projected year-end. ``year`` 0 is the as-of starting position.

    Ratios are ``None`` when the forecast run does not carry them ("not
    projected"), never ``0``. Headroom is measured against the governed,
    clamped minimum for that ratio (total capital: the Pillar 1 minimum plus the
    plan's Pillar 2 add-ons).
    """

    year: int
    period_label: str
    period_end: date | None = None
    car_pct: Decimal | None = None
    headroom_pp: Decimal | None = None
    tier1_pct: Decimal | None = None
    tier1_headroom_pp: Decimal | None = None
    cet1_pct: Decimal | None = None
    cet1_headroom_pp: Decimal | None = None
    #: The minima IN FORCE AT THIS YEAR END (deviation DV-005). A regime that
    #: changes during the plan's horizon must be measured year by year, or the
    #: later years report headroom against a rule that no longer applies. With
    #: today's open-ended parameter generations these repeat the as-of values.
    pillar1_min_pct: Decimal | None = None
    total_requirement_pct: Decimal | None = None
    tier1_min_pct: Decimal | None = None
    cet1_min_pct: Decimal | None = None


class CapitalPlanProjectionScenario(ClosedModel):
    scenario_code: str
    run_id: UUID
    input_hash: str
    years: list[CapitalPlanProjectionYear]
    min_car_pct: Decimal | None = None


type CapitalFloorSource = Literal["control_plane", "board_register"]
type ConservationBufferTreatment = Literal["included", "excluded", "not_applicable"]


class CapitalFloorRead(ClosedModel):
    """A capital minimum the projection is measured against, and its authority.

    ``value_pct`` is the EFFECTIVE minimum: the institution's board register
    value clamped tighten-only against the regulatory control plane, resolved
    at the projection's as-of date. ``source`` says which layer supplied it —
    ``control_plane`` when the regulatory value binds (no register row, or a
    register row weaker than or equal to it), ``board_register`` when the
    institution's own minimum is stricter or no regulatory value is governed.

    ``conservation_buffer`` states what the minimum is measured against: the
    bank total-capital minimum INCLUDES the capital conservation buffer (CRD
    ¶71 + ¶75), the CET1 and Tier 1 minima EXCLUDE it (the buffer is held in
    CET1 on top of them, so headroom against them says nothing about buffer
    compliance); an SDI's Act 930 s.29 minimum has no buffer regime. Whether the
    CET1 / Tier 1 status floors should include the buffer is pending stakeholder
    confirmation; the numbers are unchanged, this only labels them.

    ``effective_from`` is the platform parameter record's date, not the
    instrument's commencement date (deviation DV-005); it is not a regulatory
    fact and is not printed as one.
    """

    param_code: str
    value_pct: Decimal
    source: CapitalFloorSource
    board_register_pct: Decimal | None = None
    raised_to_regulatory_floor: bool = False
    regulatory_value_pct: Decimal | None = None
    source_citation: str | None = None
    confirmation_status: str | None = None
    effective_from: date | None = None
    conservation_buffer: ConservationBufferTreatment = "not_applicable"


class CapitalPlanProjectionRead(ClosedModel):
    """Assembled at read time from stored forecast runs — never stale.

    Only the regulatory 5-year forecast runs are projected (the ICAAP horizon;
    desk runs at other horizons never displace it), all from one reporting
    period whose end is ``as_of_date``. Minima resolve at that date, not at the
    date the page happens to be read.
    """

    as_of_date: date
    #: True when the as-of is a calendar year-end, so each projected year is a
    #: financial-year end and the labels read ``FY<year>``.
    year_end_aligned: bool
    #: True under the bank (Basel) capital regime, where CET1 and Tier 1 are
    #: projected against their own minima; False for an SDI (Act 930 s.29).
    basel_ratios_applicable: bool = True
    pillar1_min_pct: Decimal
    pillar1_min: CapitalFloorRead
    pillar2_addon_pct: Decimal
    total_requirement_pct: Decimal
    tier1_min: CapitalFloorRead | None = None
    cet1_min: CapitalFloorRead | None = None
    scenarios: list[CapitalPlanProjectionScenario]


# Write-side variants with their own component names: sharing a
# Decimal-bearing model between request and response makes FastAPI split it
# into hyphenated "-Input"/"-Output" components, which the generated-client
# patcher cannot key (the RelatedPartyRoleInput precedent).
class Pillar2AddOnInput(Pillar2AddOn):
    pass


class CapitalTriggerInput(CapitalTrigger):
    pass


class CapitalPlanContentInput(ClosedModel):
    pillar2_addons: list[Pillar2AddOnInput] = Field(default_factory=list)
    management_actions: list[ManagementAction] = Field(default_factory=list)
    trigger_framework: list[CapitalTriggerInput] = Field(default_factory=list)


class CapitalPlanPut(ClosedModel):
    content: CapitalPlanContentInput
    reason: str = Field(min_length=1, max_length=2000)


class CapitalPlanApprove(ClosedModel):
    approval_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class CapitalPlanRead(ClosedModel):
    id: UUID
    bank_id: str
    version: int
    status: CapitalPlanStatus
    content: CapitalPlanContent
    prepared_by: UUID | None = None
    approved_by_user_id: UUID | None = None
    approval_reference: str | None = None
    approval_timestamp: datetime | None = None
    approval_expires_at: date | None = None
    approval_overdue: bool = False
    created_at: datetime
    updated_at: datetime


class IlaapSnapshotRead(ClosedModel):
    id: UUID
    reporting_period_id: UUID
    as_of_date: date
    adequate: bool
    lcr_pct: Decimal | None = None
    nsfr_pct: Decimal | None = None
    lcr_status: str | None = None
    nsfr_status: str | None = None
    worst_stressed_lcr_pct: Decimal | None = None
    cfp_approved: bool = False
    cfp_active: bool = False
    ewi_escalation_state: str | None = None
    notes: str | None = None
    created_at: datetime


class IlaapRefreshCreate(ClosedModel):
    reporting_period_id: UUID
    notes: str | None = Field(default=None, max_length=2000)


type ProjectionUnavailableCode = Literal[
    "missing_parameter",
    "institution_type_unresolved",
    "jurisdiction_unresolved",
    "forecasting_view_required",
]


class ProjectionUnavailableRead(ClosedModel):
    """Why the projection could not be built, when forecast runs exist.

    The plan document, the approval state and the ILAAP evidence stay readable:
    only the projection — whose headroom needs a resolved capital minimum —
    refuses (ICAAP P0 fix round; QA P0-QA-004, architecture M1).
    ``forecasting_view_required`` is the authorization case: the projection is
    read from Forecasting runs, so a caller without Forecasting view gets the
    plan without it.
    """

    error_code: ProjectionUnavailableCode
    reason: str
    param_code: str | None = None


class CapitalPlanSummaryRead(ClosedModel):
    current: CapitalPlanRead | None = None
    approved: CapitalPlanRead | None = None
    #: ``None`` when no 5-year forecast run exists, or when one exists but the
    #: projection cannot be measured — then ``projection_unavailable`` says why.
    projection: CapitalPlanProjectionRead | None = None
    projection_unavailable: ProjectionUnavailableRead | None = None
    latest_ilaap: IlaapSnapshotRead | None = None


class IlaapSnapshotListRead(ClosedModel):
    snapshots: list[IlaapSnapshotRead]
