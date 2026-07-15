from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    job_type: str
    status: str
    entity_type: str | None
    entity_id: UUID | None
    attempts: int
    max_attempts: int
    progress: dict[str, object]
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
