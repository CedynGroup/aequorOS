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


class CapitalProjectionYear(ClosedModel):
    year: int
    period_label: str
    car_pct: Decimal | None = None
    headroom_pp: Decimal | None = None


class CapitalProjectionScenario(ClosedModel):
    scenario_code: str
    run_id: UUID
    input_hash: str
    years: list[CapitalProjectionYear]
    min_car_pct: Decimal | None = None


class CapitalProjectionRead(ClosedModel):
    """Assembled at read time from stored forecast runs — never stale."""

    pillar1_min_pct: Decimal
    pillar2_addon_pct: Decimal
    total_requirement_pct: Decimal
    scenarios: list[CapitalProjectionScenario]


class CapitalPlanPut(ClosedModel):
    content: CapitalPlanContent
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


class CapitalPlanSummaryRead(ClosedModel):
    current: CapitalPlanRead | None = None
    approved: CapitalPlanRead | None = None
    projection: CapitalProjectionRead | None = None
    latest_ilaap: IlaapSnapshotRead | None = None


class IlaapSnapshotListRead(ClosedModel):
    snapshots: list[IlaapSnapshotRead]
