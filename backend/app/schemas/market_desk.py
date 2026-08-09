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
    total: int


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


class DeskDeterminationRead(ClosedModel):
    id: UUID
    cob_date: date
    methodology_code: str
    methodology_version: int
    input_snapshot: list[Any] = Field(title="Determination Input Snapshot")
    input_digest: str
    derived_values: dict[str, Any] = Field(title="Determination Derived Values")
    qa_results: dict[str, Any] = Field(title="Determination Qa Results")
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
