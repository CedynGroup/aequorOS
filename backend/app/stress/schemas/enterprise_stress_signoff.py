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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.text import reject_control_characters


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type StressSignoffStatus = Literal["draft", "pending_attestation", "attested", "withdrawn"]


class StressSignoffCreate(ClosedModel):
    #: The enterprise-stress ``RegulatoryRun`` this sign-off governs.
    run_id: UUID
    #: ¶67(a)(b): risks / exposures / entities covered + macro conditions and
    #: assumptions.
    scenario_narrative: str = Field(min_length=1, max_length=16000)
    #: ¶67(b), ¶45: justification of the assumptions and of expert-judgement
    #: overlays.
    assumptions_rationale: str = Field(min_length=1, max_length=16000)
    #: ¶67(c): the methodologies used.
    methodology_summary: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)

    # The narratives print on the filed Appendix II (PDF, XLSX, CSV); a control
    # character cannot be written into a workbook, so it is refused at the door.
    _no_control_characters = field_validator(
        "scenario_narrative", "assumptions_rationale", "methodology_summary"
    )(reject_control_characters)


class StressSignoffUpdate(ClosedModel):
    scenario_narrative: str | None = Field(default=None, min_length=1, max_length=16000)
    assumptions_rationale: str | None = Field(default=None, min_length=1, max_length=16000)
    methodology_summary: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)

    _no_control_characters = field_validator(
        "scenario_narrative", "assumptions_rationale", "methodology_summary"
    )(reject_control_characters)


class StressSignoffTransition(ClosedModel):
    reason: str = Field(min_length=1, max_length=1000)


class StressSignoffSubmission(StressSignoffTransition):
    #: The one explicitly named Board signer for this pending attestation.
    attestation_recipient_user_id: UUID


class StressSignoffStepUpRequest(ClosedModel):
    """Fresh proof of presence for this exact sign-off and digest."""

    id_token: str | None = Field(default=None, min_length=1, max_length=8192)
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class StressSignoffStepUpGranted(ClosedModel):
    authorization_token: str
    expires_at: datetime
    signing_digest: str
    method: Literal["oidc_reauth", "password_reauth"] | None


class StressSignoffAttestation(ClosedModel):
    #: ¶20: the Board's rationale for the credibility of framework + results.
    credibility_rationale: str = Field(min_length=1, max_length=16000)
    #: ¶16 reporting & challenge / ¶20: the Board's challenge and review record.
    board_challenge: str | None = Field(default=None, max_length=16000)
    reason: str = Field(min_length=1, max_length=1000)
    #: One-shot proof obtained from this sign-off's step-up endpoint.
    authorization_token: str = Field(min_length=1, max_length=512)
    #: The value the signer reviewed. The service recomputes it before signing.
    expected_signing_digest: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")

    _no_control_characters = field_validator("credibility_rationale", "board_challenge")(
        reject_control_characters
    )


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
    attestation_recipient_user_id: UUID | None
    signing_digest: str
    signing_authorization_id: UUID | None
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
