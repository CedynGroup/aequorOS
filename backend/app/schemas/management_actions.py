"""Governed management-actions plan contracts (docs/stress.md §3.7, Phase 3)."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ActionKind = Literal[
    "raise_capital", "revise_dividend", "reduce_risk", "sell_assets", "change_strategy", "other"
]
type CapitalRaiseTier = Literal["cet1", "at1", "tier2"]
type SizingMode = Literal["fixed", "fill_residual"]
type TriggerKind = Literal["always", "on_breach", "on_severity"]
type PlanStatus = Literal["draft", "pending_approval", "approved", "archived"]
type Severity = Literal["mild", "moderate", "severe"]
type MinimumCode = Literal["car", "cet1", "tier1", "leverage", "paid_up"]

_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,60}$")
_ACTION_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,60}$")


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionItemIn(ClosedModel):
    """One credible action; mirrors the domain ``ManagementAction`` contract."""

    action_id: str = Field(min_length=1, max_length=60)
    kind: ActionKind
    label: str = Field(min_length=1, max_length=200)
    sort_order: int = Field(default=0, ge=0, le=1000)
    trigger_kind: TriggerKind
    watch_minima: list[MinimumCode] | None = None
    min_severity: Severity | None = None
    effective_year: int = Field(default=1, ge=1, le=10)
    capital_raise_ghs: Decimal = Field(default=Decimal("0"), ge=0)
    capital_raise_tier: CapitalRaiseTier = "cet1"
    counts_as_paid_up: bool = False
    sizing: SizingMode = "fixed"
    dividend_reduction_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    rwa_reduction_ghs: Decimal = Field(default=Decimal("0"), ge=0)
    shrinks_leverage_exposure: bool = False
    #: {severity: factor}; omitted ⇒ the engine default (mild 0.5, moderate 0.75, severe 1.0).
    severity_factors: dict[Severity, Decimal] | None = None
    rationale: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not _ACTION_ID_PATTERN.match(self.action_id):
            msg = "action_id must be a lowercase slug: a-z, 0-9 and underscores only"
            raise ValueError(msg)
        if self.trigger_kind == "on_severity" and self.min_severity is None:
            msg = "an on_severity trigger requires min_severity"
            raise ValueError(msg)
        if self.kind == "raise_capital" and self.sizing == "fixed" and self.capital_raise_ghs <= 0:
            msg = "a fixed-size raise_capital action requires capital_raise_ghs > 0"
            raise ValueError(msg)
        if self.kind == "revise_dividend" and self.dividend_reduction_pct <= 0:
            msg = "a revise_dividend action requires dividend_reduction_pct > 0"
            raise ValueError(msg)
        if (
            self.kind in ("reduce_risk", "sell_assets", "change_strategy", "other")
            and self.rwa_reduction_ghs <= 0
        ):
            msg = f"a {self.kind} action requires rwa_reduction_ghs > 0"
            raise ValueError(msg)
        if self.severity_factors is not None:
            for value in self.severity_factors.values():
                if value < 0:
                    msg = "severity_factors must be non-negative"
                    raise ValueError(msg)
        return self


class ActionItemRead(ClosedModel):
    action_id: str
    kind: ActionKind
    label: str
    sort_order: int
    trigger_kind: TriggerKind
    watch_minima: list[str] | None
    min_severity: str | None
    effective_year: int
    capital_raise_ghs: Decimal
    capital_raise_tier: CapitalRaiseTier
    counts_as_paid_up: bool
    sizing: SizingMode
    dividend_reduction_pct: Decimal
    rwa_reduction_ghs: Decimal
    shrinks_leverage_exposure: bool
    severity_factors: dict[str, Decimal] | None
    rationale: str | None


def _validate_unique_action_ids(actions: list[ActionItemIn]) -> None:
    seen: set[str] = set()
    for action in actions:
        if action.action_id in seen:
            msg = f"duplicate action_id '{action.action_id}'"
            raise ValueError(msg)
        seen.add(action.action_id)


class ManagementActionPlanCreate(ClosedModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    #: NULL = org-wide default plan; set = bank-specific (validated to the tenant).
    bank_id: str | None = Field(default=None, max_length=16)
    actions: list[ActionItemIn] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not _CODE_PATTERN.match(self.code):
            msg = "code must be a lowercase slug: a-z, 0-9 and underscores only"
            raise ValueError(msg)
        _validate_unique_action_ids(self.actions)
        return self


class ManagementActionPlanUpdate(ClosedModel):
    """Draft-only edit. Any provided ``actions`` REPLACES the full action set."""

    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    actions: list[ActionItemIn] | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.actions is not None:
            if not self.actions:
                msg = "actions, when provided, must be non-empty"
                raise ValueError(msg)
            _validate_unique_action_ids(self.actions)
        return self


class ManagementActionPlanTransition(ClosedModel):
    reason: str = Field(min_length=1, max_length=1000)


class ManagementActionPlanApproval(ClosedModel):
    reason: str = Field(min_length=1, max_length=1000)


class ManagementActionPlanRead(ClosedModel):
    id: UUID
    organization_id: str
    bank_id: str | None
    code: str
    name: str
    description: str | None
    status: PlanStatus
    version: int
    created_by: UUID | None
    approved_by: UUID | None
    approval_timestamp: datetime | None
    actions: list[ActionItemRead]
    #: Actions whose size depends on how severe the scenario is (¶81). A plan
    #: holding any of these needs a scenario that states its severity; run
    #: against one that does not, it refuses rather than quietly applying every
    #: action at full size. Empty means the plan runs against any scenario.
    severity_priced_actions: list[str]
    #: Convenience mirror of ``severity_priced_actions`` being non-empty.
    requires_declared_severity: bool
    created_at: datetime
    updated_at: datetime


class ManagementActionPlanSummaryRead(ClosedModel):
    id: UUID
    bank_id: str | None
    code: str
    name: str
    status: PlanStatus
    version: int
    action_count: int
    created_by: UUID | None
    approved_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ManagementActionPlanListRead(ClosedModel):
    plans: list[ManagementActionPlanSummaryRead]
    total: int
