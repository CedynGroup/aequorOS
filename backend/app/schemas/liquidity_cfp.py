"""EWI + CFP contracts (LRMD 2026 ¶28(e)–(f), ¶70–77)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type EwiDirection = Literal["above", "below"]
type EwiUnit = Literal["pct", "days", "count", "ghs"]
type EwiStatus = Literal["normal", "watch", "action", "unconfigured", "no_data"]
type EscalationState = Literal["normal", "heightened_monitoring", "escalation", "cfp_active"]
type CfpStatus = Literal["draft", "approved", "superseded"]
type CfpEventType = Literal["activated", "de_escalated"]
# LRMD ¶75(b): funding options must cover intraday through structural horizons.
type CfpHorizon = Literal["intraday", "up_to_1m", "1_to_3m", "3_to_12m", "over_12m"]
type CfpAudience = Literal["internal", "regulator", "counterparties", "media", "public"]


# --- EWI register + evaluation ---------------------------------------------


class EwiIndicatorUpdate(ClosedModel):
    """One register entry in the audited PUT. Starters are overridden by
    code; custom indicators (``custom=True``) must carry their own display
    and semantics fields."""

    code: str = Field(min_length=1, max_length=60)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    recovery_plan_reference: str | None = Field(default=None, max_length=200)
    watch_threshold: Decimal | None = None
    action_threshold: Decimal | None = None
    direction: EwiDirection | None = None
    unit: EwiUnit | None = None
    enabled: bool = True
    custom: bool = False


class EwiRegisterPut(ClosedModel):
    indicators: list[EwiIndicatorUpdate]
    # Board evidence for the trigger levels (threshold-register convention).
    approved_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class EwiEvaluationRead(ClosedModel):
    code: str
    name: str
    description: str | None = None
    recovery_plan_reference: str | None = None
    metric_basis: str
    unit: EwiUnit
    direction: EwiDirection
    value: Decimal | None = None
    prior_value: Decimal | None = None
    watch_threshold: Decimal | None = None
    action_threshold: Decimal | None = None
    status: EwiStatus
    detail: str | None = None
    custom: bool = False
    enabled: bool = True


class EwiDashboardRead(ClosedModel):
    bank_id: str
    reporting_period_id: UUID
    as_of_date: date
    indicators: list[EwiEvaluationRead]
    escalation_state: EscalationState
    cfp_approved_version: int | None = None
    cfp_approval_expires_at: date | None = None
    cfp_active: bool = False


# --- CFP minimum contents (¶72(a)–(g)) --------------------------------------


class CfpEwiTrigger(ClosedModel):
    """¶72(a): which EWI states trigger CFP consideration."""

    indicator_code: str = Field(min_length=1, max_length=60)
    trigger_condition: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class CfpFundingOption(ClosedModel):
    """¶72(b): available funding sources per horizon, intraday included."""

    horizon: CfpHorizon
    source: str = Field(min_length=1, max_length=200)
    estimated_capacity: str | None = Field(default=None, max_length=200)
    lead_time: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)


class CfpActionPlan(ClosedModel):
    """¶72(c): asset-side and liability-side action plans."""

    side: Literal["asset", "liability"]
    action: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=200)
    timeline: str | None = Field(default=None, max_length=200)


class CfpAlternativeSource(ClosedModel):
    """¶72(d): alternative/contingent sources of funds."""

    source: str = Field(min_length=1, max_length=200)
    conditions: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class CfpEscalationStep(ClosedModel):
    """¶72(e): escalation and prioritisation procedures, ordered."""

    priority: int = Field(ge=1, le=99)
    stage: str = Field(min_length=1, max_length=200)
    trigger: str = Field(min_length=1, max_length=500)
    actions: str = Field(min_length=1, max_length=2000)
    owner: str = Field(min_length=1, max_length=200)


class CfpKeyRelationship(ClosedModel):
    """¶72(f): the key-relationship register (counterparties, agents, FMIs)."""

    institution: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)


class CfpCommunicationPlan(ClosedModel):
    """¶72(g): communication plans, regulator and media included."""

    audience: CfpAudience
    channel: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=200)
    message_outline: str | None = Field(default=None, max_length=2000)


class CfpBehavioralLiquidityScenario(ClosedModel):
    """A Board-owned deposit-behavior overlay linked to a CFP action."""

    name: str = Field(min_length=1, max_length=160)
    activation_horizon: CfpHorizon
    linked_action: str = Field(min_length=1, max_length=500)
    deposit_runoff_uplift_pct: Decimal = Field(ge=0, le=100)
    funding_cost_uplift_bps: Decimal = Field(ge=0, le=10_000)
    notes: str | None = Field(default=None, max_length=1000)


class CfpContent(ClosedModel):
    ewi_triggers: list[CfpEwiTrigger] = Field(default_factory=list)
    funding_options: list[CfpFundingOption] = Field(default_factory=list)
    action_plans: list[CfpActionPlan] = Field(default_factory=list)
    alternative_sources: list[CfpAlternativeSource] = Field(default_factory=list)
    escalation_procedures: list[CfpEscalationStep] = Field(default_factory=list)
    key_relationships: list[CfpKeyRelationship] = Field(default_factory=list)
    communication_plans: list[CfpCommunicationPlan] = Field(default_factory=list)
    behavioral_liquidity_scenarios: list[CfpBehavioralLiquidityScenario] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def link_behavioral_scenarios_to_actions(self) -> CfpContent:
        actions = {item.action for item in self.action_plans}
        unlinked = [
            item.linked_action
            for item in self.behavioral_liquidity_scenarios
            if item.linked_action not in actions
        ]
        if unlinked:
            raise ValueError(
                "Each behavioral liquidity scenario must link to an action in action_plans: "
                + "; ".join(unlinked)
            )
        return self


# --- CFP lifecycle -----------------------------------------------------------


class CfpPut(ClosedModel):
    content: CfpContent
    reason: str = Field(min_length=1, max_length=2000)


class CfpApprove(ClosedModel):
    approval_reference: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class CfpRead(ClosedModel):
    id: UUID
    bank_id: str
    version: int
    status: CfpStatus
    content: CfpContent
    prepared_by: UUID | None = None
    approved_by_user_id: UUID | None = None
    approval_reference: str | None = None
    approval_timestamp: datetime | None = None
    approval_expires_at: date | None = None
    approval_overdue: bool = False
    active: bool = False
    created_at: datetime
    updated_at: datetime


class CfpSummaryRead(ClosedModel):
    current: CfpRead | None = None
    approved: CfpRead | None = None


class CfpActivationCreate(ClosedModel):
    reporting_period_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class CfpEventRead(ClosedModel):
    id: UUID
    cfp_id: UUID
    cfp_version: int
    event_type: CfpEventType
    reason: str
    ewi_snapshot: list[EwiEvaluationRead]
    approval_overdue: bool
    regulator_notification_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime


class CfpEventListRead(ClosedModel):
    events: list[CfpEventRead]
