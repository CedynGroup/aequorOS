"""Enterprise-stress sign-off / Board attestation contracts (docs/stress.md §3.8).

The governance record over one immutable enterprise-stress ``RegulatoryRun``:
the analyst/CRO narrative & assumptions rationale (¶67(b)(c)) and the Board's
attestation of framework + results with a credibility rationale (¶20). Governed
with the same maker-checker lifecycle as the macro-scenario spine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type StressSignoffStatus = Literal[
    "draft", "pending_attestation", "attested", "withdrawn"
]


class StressSignoffCreate(ClosedModel):
    #: The enterprise-stress ``RegulatoryRun`` this sign-off governs.
    run_id: UUID
    #: ¶67(a)(b): risks / exposures / entities covered + macro conditions and
    #: assumptions.
    scenario_narrative: str = Field(min_length=1, max_length=16000)
    #: ¶67(d), ¶45: methodologies + justification of expert-judgement overlays.
    assumptions_rationale: str = Field(min_length=1, max_length=16000)
    methodology_summary: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)


class StressSignoffUpdate(ClosedModel):
    scenario_narrative: str | None = Field(default=None, min_length=1, max_length=16000)
    assumptions_rationale: str | None = Field(default=None, min_length=1, max_length=16000)
    methodology_summary: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)


class StressSignoffTransition(ClosedModel):
    reason: str = Field(min_length=1, max_length=1000)


class StressSignoffAttestation(ClosedModel):
    #: ¶20: the Board's rationale for the credibility of framework + results.
    credibility_rationale: str = Field(min_length=1, max_length=16000)
    #: ¶16 reporting & challenge / ¶20: the Board's challenge and review record.
    board_challenge: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)


class StressSignoffRead(ClosedModel):
    id: UUID
    organization_id: str
    bank_id: str
    run_id: UUID
    reporting_period_id: UUID
    scenario_code: str
    status: StressSignoffStatus
    version: int
    scenario_narrative: str
    assumptions_rationale: str
    methodology_summary: str | None
    board_challenge: str | None
    credibility_rationale: str | None
    stays_above_all_minima: bool | None
    with_actions_stays_above_all_minima: bool | None
    created_by: UUID | None
    submitted_by: UUID | None
    submitted_at: datetime | None
    attested_by: UUID | None
    attested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StressSignoffSummary(ClosedModel):
    id: UUID
    run_id: UUID
    reporting_period_id: UUID
    scenario_code: str
    status: StressSignoffStatus
    version: int
    stays_above_all_minima: bool | None
    attested_by: UUID | None
    attested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StressSignoffListRead(ClosedModel):
    signoffs: list[StressSignoffSummary]
    total: int
