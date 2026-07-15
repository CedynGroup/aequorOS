from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AssessmentCreate(BaseModel):
    case_id: UUID
    assessment_type: str
    name: str


class AssessmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    name: str
    assessment_type: str
    status: str
    input_snapshot: dict[str, object]
    config_snapshot: dict[str, object]
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class AssessmentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    assessment_id: UUID
    status: str
    engine_version: str | None
    prompt_version: str | None
    input_hash: str | None
    started_at: datetime | None
    completed_at: datetime | None
    summary: dict[str, object]
    error: str | None
    created_at: datetime


class RunResponse(BaseModel):
    run_id: UUID
    job_id: UUID
    status: str
