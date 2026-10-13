"""The filing review chain's wire contract.

The dashboard's chain panel (redesign §4b.4) answers three questions, and this
is shaped to answer exactly those: where is the return and who holds it, what
has happened to it, and what happens next. ``viewer`` is the fourth thing a
screen needs and the one the old stepper could not give — what THIS caller may
actually do, and the plain-language reason when they may not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type FilingStageKind = Literal["prepare", "review", "approve"]
type FilingStageState = Literal["pending", "current", "done", "returned"]
type FilingStageSource = Literal["platform_default", "bank_template"]
type FilingWorkflowTemplateStatus = Literal[
    "draft", "pending_approval", "approved", "rejected", "superseded"
]
#: ``returned`` is the INTERNAL send-back. The regulator's own outcomes
#: (``rejected``, ``declined``) are package statuses and never appear here.
type FilingStageDecisionType = Literal["submitted", "reviewed", "approved", "returned"]
type FilingDecisionRequest = Literal["approved", "returned"]


class FilingStageInput(ClosedModel):
    """One stage of a proposed filing chain."""

    seq: int = Field(ge=1, le=20)
    stage_key: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    decision_kind: FilingStageKind
    officer_titles: list[str] = Field(default_factory=list, max_length=10)
    #: The one stage whose approval releases the return to the regulator.
    transmit_on_approve: bool = False


class FilingWorkflowTemplateCreate(ClosedModel):
    stages: list[FilingStageInput] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=2000)


class FilingWorkflowTemplateUpdate(ClosedModel):
    stages: list[FilingStageInput] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=10, max_length=2000)


class FilingWorkflowTemplateSubmit(ClosedModel):
    reason: str = Field(min_length=10, max_length=2000)


class FilingWorkflowTemplateDecision(ClosedModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)


class FilingWorkflowTemplateRead(ClosedModel):
    id: UUID
    bank_id: str
    version: int
    status: FilingWorkflowTemplateStatus
    stages: list[FilingStageInput]
    reason: str
    proposed_by: UUID
    submitted_at: datetime | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    superseded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FilingWorkflowTemplateListRead(ClosedModel):
    templates: list[FilingWorkflowTemplateRead]
    #: The chain a return sent for approval today would pin, whatever its source.
    effective_stages: list[FilingStageInput]
    effective_source: FilingStageSource


class PackageStageDecisionRead(ClosedModel):
    id: UUID
    stage_seq: int
    stage_key: str
    round: int
    decision: FilingStageDecisionType
    #: The stage a send-back named. Present only on ``returned``, which is how
    #: "returned to the Preparer" is legible rather than inferred.
    return_to_seq: int | None = None
    review_digest: str
    comment: str | None = None
    decided_by: UUID
    decided_by_name: str
    officer_title: str | None = None
    created_at: datetime
    #: False once a later send-back reopened this stage.
    still_stands: bool


class PackageStageRead(ClosedModel):
    seq: int
    stage_key: str
    title: str
    decision_kind: FilingStageKind
    officer_titles: list[str]
    transmit_on_approve: bool
    state: FilingStageState
    decisions: list[PackageStageDecisionRead]


class PackageChainViewerRead(ClosedModel):
    """What THIS caller may do, and the plain-language reason when they may not.

    The screen renders a control only where the server would accept it
    (redesign §4b.1): ABSENT when the surface is not this role's at all,
    DISABLED WITH THE REASON when it is theirs but not yet their turn. These
    flags are the projection that decision is made from — never a scalar
    session role.

    ``can_transmit`` says the CHAIN is complete and the return is filable; the
    filing authority itself is ``Permission.SUBMIT`` and is reported by
    ``/auth/me``. Both must be true, and the server checks both again.
    """

    can_send_for_approval: bool
    can_decide: bool
    can_return: bool
    can_transmit: bool
    blocked_reason: str | None = None


class PackageChainRead(ClosedModel):
    package_id: UUID
    status: str
    round: int
    current_stage_seq: int | None = None
    current_stage_key: str | None = None
    source: FilingStageSource
    review_digest: str
    #: The MACHINE check result. Named for what it is: it gates entry to the
    #: chain and is not a person and not a stage. The word "Validated" belongs
    #: to the Validator.
    checks_passed: bool
    complete: bool
    #: The current stage has been decided and the return has NOT yet been
    #: passed on. Approving and handing on are two acts, so the surface needs
    #: to know which of them is available — and must not infer it from the
    #: decision list, because the server owns what "not yet" means.
    awaiting_hand_off: bool = False
    is_rehearsal: bool
    stages: list[PackageStageRead]
    viewer: PackageChainViewerRead


class PackageSendForApproval(ClosedModel):
    review_digest: str = Field(min_length=64, max_length=64)
    note: str | None = Field(default=None, max_length=2000)


class PackageStageDecisionCreate(ClosedModel):
    decision: FilingDecisionRequest
    round: int = Field(ge=1)
    review_digest: str = Field(min_length=64, max_length=64)
    #: Required on ``returned``: the earlier stage the return goes back to. A
    #: Validator names the Approver or the Preparer.
    return_to_seq: int | None = Field(default=None, ge=1, le=20)
    comment: str | None = Field(default=None, max_length=4000)


__all__ = [
    "FilingDecisionRequest",
    "FilingStageDecisionType",
    "FilingStageInput",
    "FilingStageKind",
    "FilingStageSource",
    "FilingStageState",
    "FilingWorkflowTemplateCreate",
    "FilingWorkflowTemplateDecision",
    "FilingWorkflowTemplateListRead",
    "FilingWorkflowTemplateRead",
    "FilingWorkflowTemplateStatus",
    "FilingWorkflowTemplateSubmit",
    "FilingWorkflowTemplateUpdate",
    "PackageChainRead",
    "PackageChainViewerRead",
    "PackageSendForApproval",
    "PackageStageDecisionCreate",
    "PackageStageDecisionRead",
    "PackageStageRead",
]
