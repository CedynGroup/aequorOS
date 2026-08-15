from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImpliedRatingRunCreate(ClosedModel):
    reporting_period_id: UUID
    support_uplift_notches: int = Field(default=0, ge=0, le=10)


class ImpliedRatingRunRead(ClosedModel):
    id: UUID
    bank_id: str
    reporting_period_id: UUID
    methodology_code: str
    methodology_version: int
    status: str
    engine_version: str
    input_hash: str
    input_snapshot: dict[str, Any]
    results: dict[str, Any]
    error_code: str | None
    error_message: str | None
    completed_at: datetime
    created_at: datetime


class ImpliedRatingRunListRead(ClosedModel):
    bank_id: str
    runs: list[ImpliedRatingRunRead]