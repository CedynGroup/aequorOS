"""ICAAP AI drafting contracts: the model's output schema and the API's."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- what the model returns ---------------------------------------------------
# Deliberately free of numeric constraints (D-024): paragraph counts and lengths
# are enforced AFTER the call by the grounding validator, from settings, so the
# limits live in one place and a tuning change never means a schema change.


class DraftParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    requirement_ids: list[str]


class SectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraphs: list[DraftParagraph]
    open_questions: list[str]


# --- what the API returns -----------------------------------------------------

type IcaapAiStatus = Literal[
    "queued",
    "running",
    "validated",
    "rejected_validation",
    "refused",
    "failed",
    "rate_limited",
    "cancelled",
]


class IcaapAiSegmentRead(ClosedModel):
    """One run of a previewed paragraph."""

    kind: Literal["text", "fact"]
    text: str | None = None
    block_id: UUID | None = None
    fact_key: str | None = None
    #: The REAL formatted figure, resolved server-side from the current binding.
    display: str | None = None
    status: str | None = None


class IcaapAiParagraphRead(ClosedModel):
    index: int
    segments: list[IcaapAiSegmentRead]
    requirement_ids: list[str]
    requirement_labels: list[str]


class IcaapAiDraftPreviewRead(ClosedModel):
    paragraphs: list[IcaapAiParagraphRead]
    #: Shown to the reviewer; never inserted into the document.
    open_questions: list[str]


class IcaapAiDecisionRead(ClosedModel):
    decision: Literal["accepted", "rejected"]
    paragraph_indexes: list[int]
    acknowledged_stale: bool
    reason: str | None
    decided_at: datetime


class IcaapAiSuggestionRead(ClosedModel):
    id: UUID
    section_key: str
    status: IcaapAiStatus
    requested_by_name: str | None
    created_at: datetime
    completed_at: datetime | None
    fact_sheet_mode: Literal["standard", "descriptor_only"]
    fact_count: int
    model_requested: str
    model_served: str | None
    fallback_used: bool
    prompt_version: str
    failure_code: str | None
    refusal_category: str | None
    #: CODES only. The rejected text itself is never served.
    validation_error_codes: list[str]
    #: True when a figure this draft quoted has been rebound since.
    stale: bool
    decision: IcaapAiDecisionRead | None
    poll_after_seconds: int
    #: Present ONLY when status == "validated".
    draft: IcaapAiDraftPreviewRead | None = None


class IcaapAiSuggestionListRead(ClosedModel):
    items: list[IcaapAiSuggestionRead]


class IcaapAiDraftAccept(ClosedModel):
    base_rev: int = Field(ge=0)
    paragraph_indexes: list[int] = Field(min_length=1)
    acknowledge_stale: bool = False


class IcaapAiDraftReject(ClosedModel):
    reason: str | None = Field(default=None, max_length=2000)


__all__ = [
    "DraftParagraph",
    "IcaapAiDecisionRead",
    "IcaapAiDraftAccept",
    "IcaapAiDraftPreviewRead",
    "IcaapAiDraftReject",
    "IcaapAiParagraphRead",
    "IcaapAiSegmentRead",
    "IcaapAiStatus",
    "IcaapAiSuggestionListRead",
    "IcaapAiSuggestionRead",
    "SectionDraft",
]
