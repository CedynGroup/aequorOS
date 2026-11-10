from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: str
    job_type: str
    status: str
    entity_type: str | None
    # The entity reference the producer persisted, as a STRING: producers
    # target institutions by their platform id (`BK-...`) and rows by UUID,
    # so the wire form keeps whichever identifier the row carries.
    entity_id: str | None
    attempts: int
    max_attempts: int
    progress: dict[str, object]
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class JobListRead(BaseModel):
    jobs: list[JobRead]
    total: int
    limit: int
    offset: int
    has_more: bool
